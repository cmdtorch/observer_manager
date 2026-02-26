import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_glitchtip_client, get_grafana_client
from app.core.config import get_settings
from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.application import Application
from app.models.organization import Organization
from app.schemas.application import (
    ApplicationListItem,
    CreateApplicationRequest,
    CreateApplicationResponse,
    DeleteApplicationResponse,
    InviteUsersRequest,
    InviteUsersResponse,
)
from app.models.user import User
from app.services.alert_setup import setup_glitchtip_project_alert
from app.services.clients.glitchtip_client import GlitchTipService
from app.services.clients.grafana_client import GrafanaService

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/organizations/{org_id}/apps",
    response_model=CreateApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application(
    org_id: uuid.UUID,
    request: CreateApplicationRequest,
    db: AsyncSession = Depends(get_db),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()

    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not org.glitchtip_slug:
        raise HTTPException(status_code=400, detail="Organization has no GlitchTip slug configured")

    app = Application(
        organization_id=org_id,
        name=request.name,
        platform=request.platform,
    )
    db.add(app)
    await db.flush()

    team_slug = f"{org.glitchtip_slug}-team"
    project_slug = await glitchtip.create_project(
        org.glitchtip_slug, team_slug, request.name, request.platform or "other"
    )
    dsn = await glitchtip.get_project_dsn(org.glitchtip_slug, project_slug)

    app.glitchtip_project_slug = project_slug
    app.glitchtip_dsn = dsn
    await db.commit()
    await db.refresh(app)

    # Setup GlitchTip alert webhook (non-critical, errors are caught inside)
    if app.glitchtip_project_slug:
        if not org.glitchtip_slug:
            logger.warning("glitchtip_alert_skipped_no_org_slug", app_id=str(app.id))
        else:
            webhook_url = f"{settings.public_base_url}/webhook/{app.glitchtip_project_slug}"
            await setup_glitchtip_project_alert(
                glitchtip_service=glitchtip,
                organization_slug=org.glitchtip_slug,
                project_slug=app.glitchtip_project_slug,
                webhook_url=webhook_url,
            )

    otlp_endpoint = f"https://{settings.alloy_domain}"

    return CreateApplicationResponse(
        id=app.id,
        name=app.name,
        platform=app.platform,
        glitchtip_dsn=dsn,
        otlp_endpoint=otlp_endpoint,
        resource_attributes={
            "service.name": request.name,
            "application": request.name,
        },
        instructions=(
            f"Send OTLP data with Authorization header and set "
            f"service.name and application resource attributes to '{request.name}'"
        ),
    )


@router.get("/organizations/{org_id}/apps", response_model=list[ApplicationListItem])
async def list_applications(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Organization not found")

    apps_result = await db.execute(
        select(Application)
        .where(Application.organization_id == org_id)
        .order_by(Application.created_at)
    )
    return apps_result.scalars().all()


@router.delete("/apps/{app_id}", response_model=DeleteApplicationResponse)
async def delete_application(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    org_result = await db.execute(
        select(Organization).where(Organization.id == app.organization_id)
    )
    org = org_result.scalar_one_or_none()

    if org and org.glitchtip_slug and app.glitchtip_project_slug:
        try:
            await glitchtip.delete_project(org.glitchtip_slug, app.glitchtip_project_slug)
        except Exception:
            pass

    await db.delete(app)
    await db.commit()

    return DeleteApplicationResponse(message="Application deleted", app_id=app_id)


@router.post("/organizations/{org_id}/invite", response_model=InviteUsersResponse)
async def invite_users(
    org_id: uuid.UUID,
    request: InviteUsersRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()

    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    for email in request.emails:
        domain = email.split("@")[-1] if "@" in email else ""
        if domain != settings.allowed_email_domain:
            raise HTTPException(
                status_code=422,
                detail=f"Email {email} not from allowed domain @{settings.allowed_email_domain}",
            )

    results = []
    for email in request.emails:
        grafana_ok = False
        glitchtip_ok = False
        grafana_link: str | None = None
        glitchtip_link: str | None = None

        if org.grafana_org_id:
            grafana_ok, grafana_link = await grafana.invite_user(
                org.grafana_org_id, email, request.grafana_role
            )

        if org.glitchtip_slug:
            glitchtip_ok, glitchtip_link = await glitchtip.invite_member(
                org.glitchtip_slug, email, request.glitchtip_role
            )

        # Upsert User record
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()
        if not user:
            user = User(email=email)
            db.add(user)
            await db.flush()

        if grafana_link:
            user.grafana_invite_url = grafana_link
        if glitchtip_link:
            user.glitchtip_invite_url = glitchtip_link

        results.append(
            {
                "email": email,
                "grafana_invited": grafana_ok,
                "grafana_invite_link": grafana_link,
                "glitchtip_invited": glitchtip_ok,
                "glitchtip_invite_link": glitchtip_link,
            }
        )

    await db.commit()
    return InviteUsersResponse(results=results)
