from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_credentials
from app.db.session import get_db
from app.api.deps import get_nginx_manager
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.schemas.api_key import (
    ApiKeyListItem,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    DeleteApiKeyResponse,
)
from app.services.key_generator import generate_api_key, mask_api_key
from app.services.nginx_manager import NginxManager

router = APIRouter()


@router.post(
    "/organizations/{org_id}/keys",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    org_id: int,
    request: CreateApiKeyRequest,
    db: AsyncSession = Depends(get_db),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    # Step 1 — Validate org
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Step 2 — Generate key
    raw_key = generate_api_key(org.slug)

    # Step 3 — Insert
    api_key = ApiKey(
        organization_id=org_id,
        key=raw_key,
        description=request.description,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    # Step 4+5 — Nginx update & reload
    await nginx.update_and_reload(db)

    return CreateApiKeyResponse(
        id=api_key.id,
        key=raw_key,
        description=api_key.description,
        organization=org.name,
        created_at=api_key.created_at,
    )


@router.get("/organizations/{org_id}/keys", response_model=list[ApiKeyListItem])
async def list_api_keys(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Organization not found")

    keys_result = await db.execute(
        select(ApiKey)
        .where(ApiKey.organization_id == org_id)
        .order_by(ApiKey.id)
    )
    keys = keys_result.scalars().all()
    return [
        ApiKeyListItem(
            id=k.id,
            key_masked=mask_api_key(k.key),
            description=k.description,
            is_active=k.is_active,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/keys/{key_id}", response_model=DeleteApiKeyResponse)
async def delete_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    nginx: NginxManager = Depends(get_nginx_manager),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Step 1 — Deactivate
    key.is_active = False
    await db.commit()

    # Step 2+3 — Nginx
    await nginx.update_and_reload(db)

    return DeleteApiKeyResponse(message="API key revoked", key_id=key_id)
