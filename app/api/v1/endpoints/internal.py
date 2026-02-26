import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.organization import Organization

logger = structlog.get_logger()

router = APIRouter(include_in_schema=False)


@router.get("/auth/validate")
async def validate_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "invalid api key"})

    key = auth_header[len("Bearer "):]

    stmt = (
        select(ApiKey.is_active, Organization.slug, Organization.is_active)
        .join(Organization, ApiKey.organization_id == Organization.id)
        .where(ApiKey.key == key)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if row is None:
        logger.info("auth_validate", key_prefix=key[:8], org_slug=None, valid=False)
        return JSONResponse(status_code=401, content={"error": "invalid api key"})

    key_active, org_slug, org_active = row

    if not key_active or not org_active:
        logger.info("auth_validate", key_prefix=key[:8], org_slug=org_slug, valid=False)
        return JSONResponse(status_code=403, content={"error": "key or organization is inactive"})

    logger.info("auth_validate", key_prefix=key[:8], org_slug=org_slug, valid=True)
    return JSONResponse(
        status_code=200,
        content={"ok": True},
        headers={
            "X-Scope-OrgID": org_slug,
            "Cache-Control": "max-age=180",
        },
    )
