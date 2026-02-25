import json
import uuid
from pathlib import Path

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.models.telegram_group import TelegramGroup
from app.models.user import User
from app.schemas.organization import (
    CreateOrganizationRequest,
    CreateOrganizationResponse,
    SetupTelegramResponse,
    SyncOrganizationResponse,
)
from app.schemas.user import UserRead
from app.services.clients.glitchtip_client import GlitchTipService
from app.services.clients.grafana_client import GrafanaService
from app.services.key_generator import generate_api_key
from app.services.nginx_manager import NginxManager
from app.services.rollback_manager import RollbackManager

logger = structlog.get_logger()

DASHBOARDS_DIR = Path(__file__).parent.parent / "assets" / "dashboards"


def _load_dashboard(filename: str) -> dict:
    path = DASHBOARDS_DIR / filename
    with open(path) as f:
        return json.load(f)


def _make_slug(name: str) -> str:
    from slugify import slugify
    return slugify(name, max_length=50)


class OrganizationService:
    def __init__(
        self,
        db: AsyncSession,
        grafana: GrafanaService,
        glitchtip: GlitchTipService,
        nginx: NginxManager,
        settings: Settings,
    ):
        self.db = db
        self.grafana = grafana
        self.glitchtip = glitchtip
        self.nginx = nginx
        self.settings = settings

    async def create_organization(
        self, request: CreateOrganizationRequest
    ) -> CreateOrganizationResponse:
        log = logger.bind(org_name=request.name)

        # Step 1 — Validate
        log.info("org_create_step1_validate")
        slug = _make_slug(request.name)

        user_emails = request.users or []
        for email in user_emails:
            domain = email.split("@")[-1] if "@" in email else ""
            if domain != self.settings.allowed_email_domain:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Email {email} not from allowed domain @{self.settings.allowed_email_domain}",
                )

        existing = await self.db.execute(
            select(Organization).where(Organization.slug == slug)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization with slug '{slug}' already exists",
            )

        # Validate telegram_group_id if provided (before any external calls)
        tg_group: TelegramGroup | None = None
        if request.telegram_group_id:
            tg_result = await self.db.execute(
                select(TelegramGroup).where(TelegramGroup.id == request.telegram_group_id)
            )
            tg_group = tg_result.scalar_one_or_none()
            if not tg_group:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="TelegramGroup not found",
                )
            # Check not already linked to another org
            linked_org_result = await self.db.execute(
                select(Organization).where(
                    Organization.telegram_group_id == tg_group.id,
                    Organization.is_active == True,  # noqa: E712
                )
            )
            if linked_org_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This Telegram group is already linked to another organization",
                )

        # Step 2 — Save to DB
        log.info("org_create_step2_save_db")
        org = Organization(
            name=request.name,
            slug=slug,
        )
        self.db.add(org)
        await self.db.flush()
        await self.db.refresh(org)
        log = log.bind(org_id=str(org.id), slug=slug)

        rollback = RollbackManager()
        grafana_org_id: int | None = None
        glitchtip_org_id: int | None = None
        glitchtip_slug: str | None = None

        try:
            # Step 3 — Grafana org
            log.info("org_create_step3_grafana_org")
            grafana_org_id = await self.grafana.create_org(request.name)
            org.grafana_org_id = grafana_org_id
            await self.db.flush()
            _gid = grafana_org_id
            rollback.register(lambda gid=_gid: self.grafana.delete_org(gid))

            # Step 4 — Add admin to Grafana org
            log.info("org_create_step4_grafana_admin")
            await self.grafana.add_admin_to_org(grafana_org_id)

            # Step 5 — Datasources
            log.info("org_create_step5_datasources")
            await self.grafana.create_all_datasources(grafana_org_id, slug)

            # Step 6 — Folder + dashboards
            log.info("org_create_step6_dashboards")
            folder_uid = await self.grafana.create_folder(grafana_org_id, "Application Dashboards")
            overview_dash = _load_dashboard("application_overview.json")
            logs_dash = _load_dashboard("logs_explorer.json")
            await self.grafana.import_dashboard(grafana_org_id, overview_dash, folder_uid)
            await self.grafana.import_dashboard(grafana_org_id, logs_dash, folder_uid)

            # Step 7 — Telegram contact point (optional, non-fatal)
            telegram_configured = False
            if tg_group is not None:
                log.info("org_create_step7_telegram")
                contact_point_name = f"telegram-{slug}"
                try:
                    await self.grafana.create_contact_point(
                        grafana_org_id, tg_group.chat_id, contact_point_name
                    )
                    await self.grafana.set_default_contact_point(
                        grafana_org_id, contact_point_name
                    )
                    telegram_configured = True
                except Exception as exc:
                    log.warning("org_create_telegram_failed_non_fatal", error=str(exc))

            # Step 7b — Alert rules (non-fatal)
            log.info("org_create_step7b_alert_rules")
            try:
                await self.grafana.create_default_alert_rules(grafana_org_id, folder_uid, "mimir")
            except Exception as exc:
                log.warning("org_create_alert_rules_failed_non_fatal", error=str(exc))

            # Step 8 — GlitchTip org
            log.info("org_create_step8_glitchtip_org")
            glitchtip_org_id, glitchtip_slug = await self.glitchtip.create_org(request.name)
            org.glitchtip_org_id = glitchtip_org_id
            org.glitchtip_slug = glitchtip_slug
            await self.db.flush()
            _slug = glitchtip_slug
            rollback.register(lambda slug=_slug: self.glitchtip.delete_org(slug))

            # Step 9 — GlitchTip team
            log.info("org_create_step9_glitchtip_team")
            team_slug = f"{glitchtip_slug}-team"
            await self.glitchtip.create_team(glitchtip_slug, team_slug)

            # Steps 10 & 11 — Invite users
            invited_emails: list[str] = []
            if user_emails:
                log.info("org_create_step10_11_invite_users")
                for email in user_emails:
                    user = await self._find_or_create_user(email)

                    # Grafana
                    grafana_user_id = await self.grafana.find_user_by_email(email)
                    if grafana_user_id:
                        await self.grafana.add_existing_user_to_org(email, grafana_org_id)
                        user.grafana_id = grafana_user_id
                    else:
                        ok, invite_url = await self.grafana.invite_user(grafana_org_id, email)
                        if ok and invite_url:
                            user.grafana_invite_url = invite_url

                    # GlitchTip
                    gt_member_id = await self.glitchtip.find_user_by_email(glitchtip_slug, email)
                    if gt_member_id:
                        user.glitchtip_id = gt_member_id
                    else:
                        ok, invite_url = await self.glitchtip.invite_member(glitchtip_slug, email)
                        if ok and invite_url:
                            user.glitchtip_invite_url = invite_url

                    # Link user to org
                    if org not in user.orgs:
                        user.orgs.append(org)

                    await self.db.flush()
                    invited_emails.append(email)

            # Step 12 — API key
            log.info("org_create_step12_api_key")
            raw_key = generate_api_key(slug)
            api_key_obj = ApiKey(
                organization_id=org.id,
                key=raw_key,
                description="Default key",
            )
            self.db.add(api_key_obj)

            # Link TelegramGroup to org
            if tg_group is not None:
                org.telegram_group_id = tg_group.id

            await self.db.flush()
            await self.db.commit()

        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as exc:
            log.error("org_create_failed", error=str(exc))
            await self.db.rollback()
            await rollback.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Organization creation failed: {exc}",
            )

        # Step 13 — Nginx (non-fatal, org already committed)
        log.info("org_create_step13_nginx")
        try:
            await self.nginx.update_and_reload(self.db)
        except Exception as exc:
            log.error("org_create_nginx_failed_non_fatal", error=str(exc))

        grafana_url = f"https://{self.settings.grafana_domain}/?orgId={grafana_org_id}"
        glitchtip_url = f"https://{self.settings.glitchtip_domain}/{glitchtip_slug}/issues"
        otlp_endpoint = f"https://{self.settings.alloy_domain}"

        log.info("org_create_success")

        return CreateOrganizationResponse(
            id=org.id,
            name=org.name,
            slug=slug,
            scope_org_id=slug,
            grafana_org_id=grafana_org_id,
            grafana_url=grafana_url,
            glitchtip_org_id=glitchtip_org_id,
            glitchtip_slug=glitchtip_slug,
            glitchtip_url=glitchtip_url,
            api_key=raw_key,
            otlp_endpoint=otlp_endpoint,
            otlp_headers={
                "Authorization": f"Bearer {raw_key}",
                "Content-Type": "application/json",
            },
            invited_users=invited_emails,
            telegram_configured=telegram_configured,
        )

    async def _find_or_create_user(self, email: str) -> User:
        """Find existing User by email or create a new one."""
        result = await self.db.execute(
            select(User).where(User.email == email).options(selectinload(User.orgs))
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(email=email)
            user.orgs = []
            self.db.add(user)
            await self.db.flush()
        return user

    async def setup_telegram(
        self, org_id: uuid.UUID, telegram_group_id: uuid.UUID
    ) -> SetupTelegramResponse:
        # Fetch org
        org_result = await self.db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalar_one_or_none()
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )

        # Fetch TelegramGroup
        tg_result = await self.db.execute(
            select(TelegramGroup).where(TelegramGroup.id == telegram_group_id)
        )
        tg_group = tg_result.scalar_one_or_none()
        if not tg_group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="TelegramGroup not found",
            )

        # Check not already linked to a different org
        linked_org_result = await self.db.execute(
            select(Organization).where(
                Organization.telegram_group_id == tg_group.id,
                Organization.id != org.id,
            )
        )
        if linked_org_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Telegram group is already linked to another organization",
            )

        # Grafana contact point setup (only if org has grafana_org_id)
        if org.grafana_org_id:
            contact_point_name = f"telegram-{org.slug}"
            try:
                contact_points = await self.grafana.get_contact_points(org.grafana_org_id)
                existing = next(
                    (
                        cp for cp in contact_points
                        if cp.get("type") == "telegram"
                        or str(cp.get("name", "")).lower() == contact_point_name.lower()
                    ),
                    None,
                )
                if existing:
                    uid = existing.get("uid") or existing.get("id")
                    await self.grafana.update_contact_point(
                        org.grafana_org_id, uid, tg_group.chat_id
                    )
                else:
                    await self.grafana.create_contact_point(
                        org.grafana_org_id, tg_group.chat_id, contact_point_name
                    )
                    await self.grafana.set_default_contact_point(
                        org.grafana_org_id, contact_point_name
                    )
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning(
                    "setup_telegram_grafana_failed",
                    org_id=str(org_id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Grafana contact point update failed: {exc}",
                )
        else:
            logger.warning(
                "setup_telegram_no_grafana_org",
                org_id=str(org_id),
                message="Skipping Grafana steps — org has no grafana_org_id",
            )

        org.telegram_group_id = tg_group.id
        await self.db.commit()

        return SetupTelegramResponse(
            org_id=org.id,
            telegram_group_id=tg_group.id,
            telegram_group_name=tg_group.name,
            message="Telegram contact point updated successfully",
        )

    async def sync_organization(self, org_id) -> SyncOrganizationResponse:
        """Pull fresh data from Grafana and GlitchTip, update DB users."""
        result = await self.db.execute(
            select(Organization).where(Organization.id == org_id).options(
                selectinload(Organization.users)
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

        log = logger.bind(org_id=str(org_id), org_name=org.name)
        users_synced = 0

        # Sync from Grafana
        if org.grafana_org_id:
            log.info("org_sync_grafana_users")
            try:
                grafana_users = await self.grafana.get_org_users(org.grafana_org_id)
                for gu in grafana_users:
                    if gu.get("isDisabled"):
                        continue
                    email = gu.get("email")
                    if not email:
                        continue
                    user = await self._find_or_create_user(email)
                    user.grafana_id = gu.get("userId")
                    if org not in user.orgs:
                        user.orgs.append(org)
                    await self.db.flush()
                    users_synced += 1
            except Exception as exc:
                log.warning("org_sync_grafana_failed", error=str(exc))

        # Sync from GlitchTip
        if org.glitchtip_slug:
            log.info("org_sync_glitchtip_users")
            try:
                members = await self.glitchtip.get_org_members(org.glitchtip_slug)
                for member in members:
                    if member.get("pending"):
                        continue
                    email = member.get("email") or member.get("user", {}).get("email")
                    if not email:
                        continue
                    user = await self._find_or_create_user(email)
                    member_id = member.get("id") or member.get("user", {}).get("id")
                    if member_id:
                        user.glitchtip_id = int(member_id)
                    if org not in user.orgs:
                        user.orgs.append(org)
                    await self.db.flush()
                    users_synced += 1
            except Exception as exc:
                log.warning("org_sync_glitchtip_failed", error=str(exc))

        await self.db.commit()

        return SyncOrganizationResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            grafana_org_id=org.grafana_org_id,
            glitchtip_org_id=org.glitchtip_org_id,
            glitchtip_slug=org.glitchtip_slug,
            users_synced=users_synced,
            message=f"Sync complete. {users_synced} user records updated.",
        )

    async def delete_organization(self, org_id) -> dict:
        """Full cleanup — 6 steps."""
        log = logger.bind(org_id=str(org_id))

        result = await self.db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization {org_id} not found",
            )

        log = log.bind(org_name=org.name, slug=org.slug)
        log.info("org_delete_step1_found")

        # Step 2 — Deactivate API keys
        log.info("org_delete_step2_deactivate_keys")
        keys_result = await self.db.execute(
            select(ApiKey).where(ApiKey.organization_id == org_id)
        )
        for key in keys_result.scalars().all():
            key.is_active = False

        # Step 3 — Nginx
        log.info("org_delete_step3_nginx")
        await self.db.flush()
        try:
            await self.nginx.update_and_reload(self.db)
        except Exception as exc:
            log.error("org_delete_nginx_failed", error=str(exc))

        # Step 4 — Delete Grafana org
        if org.grafana_org_id:
            log.info("org_delete_step4_grafana")
            try:
                await self.grafana.delete_org(org.grafana_org_id)
            except Exception as exc:
                log.error("org_delete_grafana_failed", error=str(exc))

        # Step 5 — Delete GlitchTip org
        if org.glitchtip_slug:
            log.info("org_delete_step5_glitchtip")
            try:
                await self.glitchtip.delete_org(org.glitchtip_slug)
            except Exception as exc:
                log.error("org_delete_glitchtip_failed", error=str(exc))

        # Step 6 — Soft delete
        log.info("org_delete_step6_soft_delete")
        org.is_active = False
        await self.db.commit()

        log.info("org_delete_success")
        return {"message": "Organization deleted", "organization_id": org.id, "name": org.name}
