import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_grafana_client, get_glitchtip_client
from app.core.config import get_settings
from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.user import (
    DeleteUserResponse,
    OrgUserAddRequest,
    ResendInviteRequest,
    ResendInviteResponse,
    UserRead,
)
from app.services.clients.grafana_client import GrafanaService
from app.services.clients.glitchtip_client import GlitchTipService

router = APIRouter()


def _raise_external_error(exc: Exception, service: str) -> None:
    if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{service} API unavailable",
        )
    raise exc


# ── Global user list ──────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(User).order_by(User.created_at))
    return [UserRead.from_user(u) for u in result.scalars().all()]


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserRead.from_user(user)


@router.post("/users/{user_id}/sync", response_model=UserRead)
async def sync_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    """Sync user to Grafana and/or GlitchTip if IDs are missing."""
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.orgs))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    settings = get_settings()

    # Sync Grafana
    if user.grafana_id is None:
        try:
            grafana_id = await grafana.find_user_by_email(user.email)
            if grafana_id:
                user.grafana_id = grafana_id
                user.grafana_invite_url = None
            else:
                # Find any org this user belongs to for invitation
                org = next((o for o in user.orgs if o.grafana_org_id), None)
                if org:
                    ok, invite_url = await grafana.invite_user(org.grafana_org_id, user.email)
                    if ok and invite_url:
                        user.grafana_invite_url = invite_url
        except Exception as exc:
            _raise_external_error(exc, "Grafana")

    # Sync GlitchTip
    if user.glitchtip_id is None:
        try:
            org = next((o for o in user.orgs if o.glitchtip_slug), None)
            if org:
                gt_id = await glitchtip.find_user_by_email(org.glitchtip_slug, user.email)
                if gt_id:
                    user.glitchtip_id = gt_id
                    user.glitchtip_invite_url = None
                else:
                    ok, invite_url = await glitchtip.invite_member(org.glitchtip_slug, user.email)
                    if ok and invite_url:
                        user.glitchtip_invite_url = invite_url
        except Exception as exc:
            _raise_external_error(exc, "GlitchTip")

    await db.commit()
    await db.refresh(user)
    return UserRead.from_user(user)


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def delete_user_global(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """Delete a user record from DB (does not remove from Grafana/GlitchTip)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()
    return DeleteUserResponse(user_id=str(user_id), grafana_deleted=False, glitchtip_deleted=False)


# ── Org-scoped user management ────────────────────────────────────────────────

@router.get("/organizations/{org_id}/users", response_model=list[UserRead])
async def list_org_users(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization)
        .where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
        .options(selectinload(Organization.users))
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return [UserRead.from_user(u) for u in org.users]


@router.post(
    "/organizations/{org_id}/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_user_to_org(
    org_id: uuid.UUID,
    request: OrgUserAddRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    """Add a user to an organization. Accepts either user_id or email."""
    if not request.user_id and not request.email:
        raise HTTPException(status_code=422, detail="Provide either user_id or email")

    settings = get_settings()

    # Validate email domain
    if request.email:
        domain = request.email.split("@")[-1] if "@" in request.email else ""
        if domain != settings.allowed_email_domain:
            raise HTTPException(
                status_code=422,
                detail=f"Email must be from @{settings.allowed_email_domain}",
            )

    # Load org
    org_result = await db.execute(
        select(Organization)
        .where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
        .options(selectinload(Organization.users))
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Find or create user
    if request.user_id:
        user_result = await db.execute(select(User).where(User.id == request.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
    else:
        user_result = await db.execute(select(User).where(User.email == request.email))
        user = user_result.scalar_one_or_none()
        if not user:
            user = User(email=request.email)
            db.add(user)
            await db.flush()

    # Link to org
    if org not in user.orgs:
        user.orgs.append(org)

    # Sync to Grafana
    if org.grafana_org_id:
        try:
            grafana_id = await grafana.find_user_by_email(user.email)
            if grafana_id:
                await grafana.add_existing_user_to_org(grafana_id, org.grafana_org_id)
                user.grafana_id = grafana_id
            else:
                ok, invite_url = await grafana.invite_user(org.grafana_org_id, user.email)
                if ok and invite_url:
                    user.grafana_invite_url = invite_url
        except Exception as exc:
            _raise_external_error(exc, "Grafana")

    # Sync to GlitchTip
    if org.glitchtip_slug:
        try:
            gt_id = await glitchtip.find_user_by_email(org.glitchtip_slug, user.email)
            if gt_id:
                user.glitchtip_id = gt_id
            else:
                ok, invite_url = await glitchtip.invite_member(org.glitchtip_slug, user.email)
                if ok and invite_url:
                    user.glitchtip_invite_url = invite_url
        except Exception as exc:
            _raise_external_error(exc, "GlitchTip")

    await db.commit()
    await db.refresh(user)
    return UserRead.from_user(user)


@router.delete(
    "/organizations/{org_id}/users/{user_id}",
    response_model=DeleteUserResponse,
)
async def remove_user_from_org(
    org_id: uuid.UUID,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    org_result = await db.execute(
        select(Organization)
        .where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
        .options(selectinload(Organization.users))
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    grafana_deleted = False
    glitchtip_deleted = False

    # Try treating user_id as UUID first (DB user)
    db_user: User | None = None
    try:
        uid = uuid.UUID(user_id)
        u_result = await db.execute(select(User).where(User.id == uid))
        db_user = u_result.scalar_one_or_none()
    except ValueError:
        pass

    if db_user:
        # Remove from org association
        if org in db_user.orgs:
            db_user.orgs.remove(org)

        # Remove from Grafana via stored grafana_id
        if org.grafana_org_id and db_user.grafana_id:
            try:
                grafana_deleted = await grafana.delete_org_user(org.grafana_org_id, db_user.grafana_id)
            except Exception as exc:
                _raise_external_error(exc, "Grafana")

        # Remove from GlitchTip via stored glitchtip_id
        if org.glitchtip_slug and db_user.glitchtip_id:
            try:
                glitchtip_deleted = await glitchtip.delete_member(org.glitchtip_slug, db_user.glitchtip_id)
            except Exception as exc:
                _raise_external_error(exc, "GlitchTip")

        await db.commit()
    else:
        # Fall back to numeric user_id for Grafana/GlitchTip
        if org.grafana_org_id:
            try:
                grafana_deleted = await grafana.delete_org_user(org.grafana_org_id, int(user_id))
            except (ValueError, Exception) as exc:
                if not isinstance(exc, ValueError):
                    _raise_external_error(exc, "Grafana")

        if org.glitchtip_slug:
            try:
                glitchtip_deleted = await glitchtip.delete_member(org.glitchtip_slug, user_id)
            except Exception as exc:
                _raise_external_error(exc, "GlitchTip")

    if not grafana_deleted and not glitchtip_deleted and not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    return DeleteUserResponse(
        user_id=user_id,
        grafana_deleted=grafana_deleted,
        glitchtip_deleted=glitchtip_deleted,
    )


# ── Invitation helpers ────────────────────────────────────────────────────────

@router.post(
    "/organizations/{org_id}/invite/resend",
    response_model=ResendInviteResponse,
    status_code=status.HTTP_200_OK,
)
async def resend_invite(
    org_id: uuid.UUID,
    request: ResendInviteRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    grafana_ok = False
    grafana_link: str | None = None
    glitchtip_ok = False
    glitchtip_link: str | None = None

    try:
        if org.grafana_org_id:
            grafana_ok, grafana_link = await grafana.invite_user(org.grafana_org_id, request.email)
    except Exception as exc:
        _raise_external_error(exc, "Grafana")

    try:
        if org.glitchtip_slug:
            glitchtip_ok, glitchtip_link = await glitchtip.invite_member(org.glitchtip_slug, request.email)
    except Exception as exc:
        _raise_external_error(exc, "GlitchTip")

    return ResendInviteResponse(
        email=request.email,
        grafana_invited=grafana_ok,
        grafana_invite_link=grafana_link,
        glitchtip_invited=glitchtip_ok,
        glitchtip_invite_link=glitchtip_link,
    )
