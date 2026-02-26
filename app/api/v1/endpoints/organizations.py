import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_glitchtip_client, get_grafana_client, get_nginx_manager
from app.core.config import get_settings
from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.schemas.organization import (
    ApiKeyDetail,
    ApplicationDetail,
    CreateOrganizationRequest,
    CreateOrganizationResponse,
    DeleteOrganizationResponse,
    OrganizationDetail,
    OrganizationListItem,
    SetupTelegramRequest,
    SetupTelegramResponse,
    SyncOrganizationResponse,
)
from app.schemas.telegram_group import TelegramGroupRead
from app.schemas.user import UserRead
from app.services.clients.glitchtip_client import GlitchTipService
from app.services.clients.grafana_client import GrafanaService
from app.services.nginx_manager import NginxManager
from app.services.key_generator import mask_api_key
from app.services.organization_service import OrganizationService
from app.services.user_service import fetch_org_users

router = APIRouter()


@router.post(
    "/organizations",
    response_model=CreateOrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    request: CreateOrganizationRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    return await service.create_organization(request)


@router.get("/organizations", response_model=list[OrganizationListItem])
async def list_organizations(
    without_telegram: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    query = (
        select(Organization)
        .options(selectinload(Organization.telegram_group))
        .order_by(Organization.created_at)
    )
    if without_telegram:
        query = query.where(Organization.telegram_group_id == None)  # noqa: E711

    result = await db.execute(query)
    orgs = result.scalars().all()

    return [
        OrganizationListItem(
            id=org.id,
            name=org.name,
            slug=org.slug,
            grafana_org_id=org.grafana_org_id,
            glitchtip_org_id=org.glitchtip_org_id,
            glitchtip_slug=org.glitchtip_slug,
            telegram_group_id=org.telegram_group_id,
            telegram_group_name=org.telegram_group.name if org.telegram_group else None,
            is_active=org.is_active,
            created_at=org.created_at,
        )
        for org in orgs
    ]


@router.get("/organizations/{org_id}", response_model=OrganizationDetail)
async def get_organization(
    org_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization)
        .where(Organization.id == org_id)
        .options(
            selectinload(Organization.api_keys),
            selectinload(Organization.applications),
            selectinload(Organization.users),
            selectinload(Organization.telegram_group),
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Return DB data immediately; trigger background sync
    async def _sync_in_bg():
        try:
            await fetch_org_users(org, grafana, glitchtip)
        except Exception:
            pass

    background_tasks.add_task(_sync_in_bg)

    tg_read: TelegramGroupRead | None = None
    if org.telegram_group:
        tg = org.telegram_group
        tg_read = TelegramGroupRead(
            id=tg.id,
            name=tg.name,
            chat_id=tg.chat_id,
            org_id=org.id,
            org_name=org.name,
            created_at=tg.created_at,
            updated_at=tg.updated_at,
        )

    return OrganizationDetail(
        id=org.id,
        name=org.name,
        slug=org.slug,
        grafana_org_id=org.grafana_org_id,
        glitchtip_org_id=org.glitchtip_org_id,
        glitchtip_slug=org.glitchtip_slug,
        telegram_group=tg_read,
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
        users=[UserRead.from_user(u) for u in org.users],
    )


@router.post(
    "/organizations/{org_id}/sync",
    response_model=SyncOrganizationResponse,
    status_code=status.HTTP_200_OK,
)
async def sync_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    try:
        return await service.sync_organization(org_id)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="External API unavailable")
    except httpx.RequestError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="External API unavailable")


@router.post(
    "/organizations/{org_id}/telegram",
    response_model=SetupTelegramResponse,
    status_code=status.HTTP_200_OK,
)
async def setup_telegram(
    org_id: uuid.UUID,
    request: SetupTelegramRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    return await service.setup_telegram(org_id, request.telegram_group_id)


@router.delete("/organizations/{org_id}", response_model=DeleteOrganizationResponse)
async def delete_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)
    result = await service.delete_organization(org_id)
    return DeleteOrganizationResponse(**result)


@router.post("/organizations/{org_id}/alerts/create-default", status_code=status.HTTP_200_OK)
async def create_default_alerts(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaService = Depends(get_grafana_client),
    glitchtip: GlitchTipService = Depends(get_glitchtip_client),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    settings = get_settings()
    service = OrganizationService(db, grafana, glitchtip, nginx, settings)

    res = await db.execute(select(Organization).where(Organization.id == org_id))
    org = res.scalar_one_or_none()
    if not org or not org.grafana_org_id:
        raise HTTPException(status_code=404, detail="Organization not found or missing Grafana org")

    folder_uid = await service.grafana.create_folder(org.grafana_org_id, "Application Dashboards")
    await service.grafana.create_default_alert_rules(org.grafana_org_id, folder_uid, "mimir")
    return {"detail": "Default alert rules created successfully"}
