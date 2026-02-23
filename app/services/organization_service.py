import json
from pathlib import Path

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models.api_key import ApiKey
from app.models.application import Application
from app.models.invited_user import InvitedUser
from app.models.organization import Organization
from app.schemas.organization import CreateOrganizationRequest, CreateOrganizationResponse, SetupTelegramResponse
from app.services.glitchtip_client import GlitchtipClient
from app.services.grafana_client import GrafanaClient
from app.services.key_generator import generate_api_key
from app.services.nginx_manager import NginxManager

logger = structlog.get_logger()

TEMPLATES_DIR = Path(__file__).parent.parent / "dashboard_templates"


def _load_dashboard(filename: str) -> dict:
    path = TEMPLATES_DIR / filename
    with open(path) as f:
        return json.load(f)


def _make_slug(name: str) -> str:
    from slugify import slugify
    return slugify(name, max_length=50)


class OrganizationService:
    def __init__(
        self,
        db: AsyncSession,
        grafana: GrafanaClient,
        glitchtip: GlitchtipClient,
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

        # Step 1 — Validate input
        log.info("org_create_step1_validate")
        slug = _make_slug(request.name)

        if request.emails:
            for email in request.emails:
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

        # Step 2 — Save to DB
        log.info("org_create_step2_save_db")
        org = Organization(
            name=request.name,
            slug=slug,
            telegram_chat_id=request.telegram_chat_id,
        )
        self.db.add(org)
        await self.db.flush()
        await self.db.refresh(org)
        log = log.bind(org_id=org.id, slug=slug)

        completed_steps = ["db_insert"]
        grafana_org_id = None
        glitchtip_slug = None

        try:
            # Step 3 — Create Grafana Organization
            log.info("org_create_step3_grafana_org")
            grafana_org_id = await self.grafana.create_org(request.name)
            org.grafana_org_id = grafana_org_id
            await self.db.flush()
            completed_steps.append("grafana_org")

            # Step 4 — Add admin to Grafana org
            log.info("org_create_step4_grafana_admin")
            await self.grafana.add_admin_to_org(grafana_org_id)
            completed_steps.append("grafana_admin")

            # Step 5 — Create datasources in Grafana
            log.info("org_create_step5_datasources")
            await self.grafana.create_all_datasources(grafana_org_id, slug)
            completed_steps.append("grafana_datasources")

            # Step 6 — Create dashboard folder + import templates
            log.info("org_create_step6_dashboards")
            folder_uid = await self.grafana.create_folder(
                grafana_org_id, "Application Dashboards"
            )
            overview_dash = _load_dashboard("application_overview.json")
            logs_dash = _load_dashboard("logs_explorer.json")
            await self.grafana.import_dashboard(grafana_org_id, overview_dash, folder_uid)
            await self.grafana.import_dashboard(grafana_org_id, logs_dash, folder_uid)
            completed_steps.append("grafana_dashboards")

            # Step 7 — Setup Telegram notification (optional, non-fatal)
            telegram_configured = False
            if request.telegram_chat_id:
                log.info("org_create_step7_telegram")
                try:
                    await self.grafana.create_telegram_contact_point(
                        grafana_org_id,
                        self.settings.telegram_bot_token,
                        request.telegram_chat_id,
                    )
                    telegram_configured = True
                    completed_steps.append("grafana_telegram")
                except Exception as exc:
                    log.warning(
                        "org_create_telegram_failed_non_fatal",
                        error=str(exc),
                    )

            # Step 7b — Create default alert rules (non-fatal)
            log.info("org_create_step7b_alert_rules")
            try:
                await self.grafana.create_default_alert_rules(
                    grafana_org_id, folder_uid, "mimir"
                )
                completed_steps.append("alert_rules")
            except Exception as exc:
                log.warning(
                    "org_create_alert_rules_failed_non_fatal",
                    error=str(exc),
                )

            # Step 8 — Create GlitchTip Organization
            log.info("org_create_step8_glitchtip_org")
            glitchtip_slug = await self.glitchtip.create_org(request.name)
            org.glitchtip_slug = glitchtip_slug
            await self.db.flush()
            completed_steps.append("glitchtip_org")

            # Step 9 — Create GlitchTip Team
            log.info("org_create_step9_glitchtip_team")
            team_slug = f"{glitchtip_slug}-team"
            await self.glitchtip.create_team(glitchtip_slug, team_slug)
            completed_steps.append("glitchtip_team")

            # Step 10 & 11 — Invite users (optional)
            invited_emails: list[str] = []
            if request.emails:
                log.info("org_create_step10_11_invite_users")
                for email in request.emails:
                    grafana_ok, grafana_link = await self.grafana.invite_user(
                        grafana_org_id, email
                    )
                    glitchtip_ok, glitchtip_link = await self.glitchtip.invite_member(
                        glitchtip_slug, email
                    )
                    invited_user = InvitedUser(
                        organization_id=org.id,
                        email=email,
                        grafana_invited=grafana_ok,
                        grafana_invite_link=grafana_link,
                        glitchtip_invited=glitchtip_ok,
                        glitchtip_invite_link=glitchtip_link,
                    )
                    self.db.add(invited_user)
                    invited_emails.append(email)
                completed_steps.append("user_invites")

            # Step 12 — Generate API key
            log.info("org_create_step12_api_key")
            raw_key = generate_api_key(slug)
            api_key_obj = ApiKey(
                organization_id=org.id,
                key=raw_key,
                description="Default key",
            )
            self.db.add(api_key_obj)
            await self.db.flush()
            completed_steps.append("api_key")

            # Commit everything before nginx (nginx failure must not roll back the org)
            await self.db.commit()

        except HTTPException:
            await self.db.rollback()
            raise
        except Exception as exc:
            log.error(
                "org_create_failed",
                error=str(exc),
                completed_steps=completed_steps,
            )
            await self.db.rollback()
            await self._rollback_external(
                grafana_org_id=grafana_org_id,
                glitchtip_slug=glitchtip_slug,
                completed_steps=completed_steps,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Organization creation failed at step after {completed_steps[-1] if completed_steps else 'start'}: {exc}",
            )

        # Step 13 — Update Nginx and reload (non-fatal: org is already persisted)
        log.info("org_create_step13_nginx")
        try:
            await self.nginx.update_and_reload(self.db)
            completed_steps.append("nginx_reload")
        except Exception as exc:
            log.error("org_create_nginx_failed_non_fatal", error=str(exc))

        grafana_url = f"https://{self.settings.grafana_domain}/?orgId={grafana_org_id}"
        glitchtip_url = f"https://{self.settings.glitchtip_domain}/{glitchtip_slug}/issues"
        otlp_endpoint = f"https://{self.settings.alloy_domain}"

        log.info("org_create_success", completed_steps=completed_steps)

        return CreateOrganizationResponse(
            id=org.id,
            name=org.name,
            slug=slug,
            scope_org_id=slug,
            grafana_org_id=grafana_org_id,
            grafana_url=grafana_url,
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

    async def _rollback_external(
        self,
        grafana_org_id: int | None,
        glitchtip_slug: str | None,
        completed_steps: list[str],
    ) -> None:
        """Best-effort rollback of external resources."""
        if "grafana_org" in completed_steps and grafana_org_id:
            try:
                await self.grafana.delete_org(grafana_org_id)
                logger.info("rollback_grafana_org", org_id=grafana_org_id)
            except Exception as exc:
                logger.error("rollback_grafana_org_failed", error=str(exc))

        if "glitchtip_org" in completed_steps and glitchtip_slug:
            try:
                await self.glitchtip.delete_org(glitchtip_slug)
                logger.info("rollback_glitchtip_org", slug=glitchtip_slug)
            except Exception as exc:
                logger.error("rollback_glitchtip_org_failed", error=str(exc))

    async def setup_telegram(self, org_id: int, chat_id: str) -> SetupTelegramResponse:
        result = await self.db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        if not org.grafana_org_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Organization has no Grafana org")

        await self.grafana.create_telegram_contact_point(
            org.grafana_org_id,
            self.settings.telegram_bot_token,
            chat_id,
        )

        org.telegram_chat_id = chat_id
        await self.db.commit()

        return SetupTelegramResponse(
            org_id=org_id,
            chat_id=chat_id,
            message="Telegram contact point created successfully",
        )

    async def delete_organization(self, org_id: int) -> dict:
        """Full cleanup — 6 steps."""
        log = logger.bind(org_id=org_id)

        # Step 1 — Get org from DB
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

        # Step 3 — Update Nginx map → reload (keys removed)
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
        return {"message": "Organization deleted", "organization_id": org_id, "name": org.name}
