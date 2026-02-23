from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_credentials
from app.core.config import get_settings
from app.db.session import get_db
from app.api.deps import get_glitchtip_client
from app.models.application import Application
from app.models.invited_user import InvitedUser
from app.models.organization import Organization
from app.schemas.application import (
    ApplicationListItem,
    CreateApplicationRequest,
    CreateApplicationResponse,
    DeleteApplicationResponse,
    InviteUsersRequest,
    InviteUsersResponse,
)
from app.services.clients.glitchtip_client import GlitchtipClient
from app.api.deps import get_grafana_client
from app.services.clients.grafana_client import GrafanaClient

router = APIRouter()


@router.post(
    "/organizations/{org_id}/apps",
    response_model=CreateApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application(
    org_id: int,
    request: CreateApplicationRequest,
    db: AsyncSession = Depends(get_db),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()

    # Step 1 — Validate org
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not org.glitchtip_slug:
        raise HTTPException(
            status_code=400, detail="Organization has no GlitchTip slug configured"
        )

    # Step 2 — Insert application
    app = Application(
        organization_id=org_id,
        name=request.name,
        platform=request.platform,
    )
    db.add(app)
    await db.flush()

    # Step 3 — Create GlitchTip project
    team_slug = f"{org.glitchtip_slug}-team"
    project_slug = await glitchtip.create_project(
        org.glitchtip_slug, team_slug, request.name, request.platform or "other"
    )

    # Step 4 — Get DSN
    dsn = await glitchtip.get_project_dsn(org.glitchtip_slug, project_slug)

    app.glitchtip_project_slug = project_slug
    app.glitchtip_dsn = dsn
    await db.commit()
    await db.refresh(app)

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


@router.get(
    "/organizations/{org_id}/apps", response_model=list[ApplicationListItem]
)
async def list_applications(
    org_id: int,
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
        .order_by(Application.id)
    )
    return apps_result.scalars().all()


@router.delete("/apps/{app_id}", response_model=DeleteApplicationResponse)
async def delete_application(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    # Step 1 — Get app + org
    result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    org_result = await db.execute(
        select(Organization).where(Organization.id == app.organization_id)
    )
    org = org_result.scalar_one_or_none()

    # Step 2 — Delete GlitchTip project
    if org and org.glitchtip_slug and app.glitchtip_project_slug:
        try:
            await glitchtip.delete_project(org.glitchtip_slug, app.glitchtip_project_slug)
        except Exception:
            pass  # Log but don't block deletion

    # Step 3 — Delete from DB
    await db.delete(app)
    await db.commit()

    return DeleteApplicationResponse(message="Application deleted", app_id=app_id)


@router.post("/organizations/{org_id}/invite", response_model=InviteUsersResponse)
async def invite_users(
    org_id: int,
    request: InviteUsersRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()

    # Step 1 — Validate org + emails
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

    # Step 2 — Invite each user
    results = []
    for email in request.emails:
        grafana_ok = False
        glitchtip_ok = False

        grafana_link: str | None = None
        glitchtip_link: str | None = None

        # 2a — Grafana
        if org.grafana_org_id:
            grafana_ok, grafana_link = await grafana.invite_user(
                org.grafana_org_id, email, request.grafana_role
            )

        # 2b — GlitchTip
        if org.glitchtip_slug:
            glitchtip_ok, glitchtip_link = await glitchtip.invite_member(
                org.glitchtip_slug, email, request.glitchtip_role
            )

        # 2c — Save to DB
        invited = InvitedUser(
            organization_id=org_id,
            email=email,
            grafana_invited=grafana_ok,
            grafana_invite_link=grafana_link,
            glitchtip_invited=glitchtip_ok,
            glitchtip_invite_link=glitchtip_link,
        )
        db.add(invited)

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
