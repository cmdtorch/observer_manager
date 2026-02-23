from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_credentials
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.application import Application
from app.models.invited_user import InvitedUser
from app.models.organization import Organization
from app.schemas.organization import (
    ApiKeyDetail,
    ApplicationDetail,
    CreateOrganizationRequest,
    CreateOrganizationResponse,
    DeleteOrganizationResponse,
    InvitedUserDetail,
    OrganizationDetail,
    OrganizationListItem,
    SetupTelegramRequest,
    SetupTelegramResponse,
)
from app.services.glitchtip_client import GlitchtipClient
from app.services.grafana_client import GrafanaClient
from app.services.nginx_manager import NginxManager
from app.services.organization_service import OrganizationService
from app.services.key_generator import mask_api_key
from app.dependencies import get_grafana_client, get_glitchtip_client, get_nginx_manager
from app.config import get_settings

router = APIRouter()


@router.post(
    "/organizations",
    response_model=CreateOrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    request: CreateOrganizationRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    return await service.create_organization(request)


@router.get("/organizations", response_model=list[OrganizationListItem])
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.is_active == True).order_by(Organization.id)  # noqa: E712
    )
    return result.scalars().all()


@router.get("/organizations/{org_id}", response_model=OrganizationDetail)
async def get_organization(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization)
        .where(Organization.id == org_id)
        .options(
            selectinload(Organization.api_keys),
            selectinload(Organization.applications),
            selectinload(Organization.invited_users),
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Organization not found")

    return OrganizationDetail(
        id=org.id,
        name=org.name,
        slug=org.slug,
        grafana_org_id=org.grafana_org_id,
        glitchtip_slug=org.glitchtip_slug,
        telegram_chat_id=org.telegram_chat_id,
        is_active=org.is_active,
        created_at=org.created_at,
        updated_at=org.updated_at,
        api_keys=[
            ApiKeyDetail(
                id=k.id,
                key_masked=mask_api_key(k.key),
                description=k.description,
                is_active=k.is_active,
                created_at=k.created_at,
            )
            for k in org.api_keys
        ],
        applications=[
            ApplicationDetail(
                id=a.id,
                name=a.name,
                platform=a.platform,
                glitchtip_dsn=a.glitchtip_dsn,
                created_at=a.created_at,
            )
            for a in org.applications
        ],
        invited_users=[
            InvitedUserDetail(
                id=u.id,
                email=u.email,
                grafana_invited=u.grafana_invited,
                grafana_invite_link=u.grafana_invite_link,
                glitchtip_invited=u.glitchtip_invited,
                glitchtip_invite_link=u.glitchtip_invite_link,
                created_at=u.created_at,
            )
            for u in org.invited_users
        ],
    )


@router.post(
    "/organizations/{org_id}/telegram",
    response_model=SetupTelegramResponse,
    status_code=status.HTTP_200_OK,
)
async def setup_telegram(
    org_id: int,
    request: SetupTelegramRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    return await service.setup_telegram(org_id, request.chat_id)


@router.delete("/organizations/{org_id}", response_model=DeleteOrganizationResponse)
async def delete_organization(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    result = await service.delete_organization(org_id)
    return DeleteOrganizationResponse(**result)


# create alerts create_default_alert_rules for organization
@router.post("/organizations/{org_id}/alerts/create-default", status_code=status.HTTP_200_OK)
async def create_default_alerts(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    folder_uid = await service.grafana.create_folder(
        org_id, "Application Dashboards"
    )
    await service.grafana.create_default_alert_rules(org_id, folder_uid, "mimir")
    return {"detail": "Default alert rules created successfully"}
