import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_grafana_client, get_glitchtip_client
from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.organization import Organization
from app.schemas.user import DeleteUserResponse, ResendInviteRequest, ResendInviteResponse, UserDetail
from app.services.clients.grafana_client import GrafanaClient
from app.services.clients.glitchtip_client import GlitchtipClient
from app.services.user_service import fetch_org_users

router = APIRouter()


def _raise_external_error(exc: Exception, service: str) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{service} API unavailable",
        )
    if isinstance(exc, httpx.RequestError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{service} API unavailable",
        )
    raise


@router.get(
    "/organizations/{org_id}/users",
    response_model=list[UserDetail],
)
async def list_users(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        return await fetch_org_users(org, grafana, glitchtip)
    except Exception as exc:
        _raise_external_error(exc, "Grafana/GlitchTip")


@router.post(
    "/organizations/{org_id}/invite/resend",
    response_model=ResendInviteResponse,
    status_code=status.HTTP_200_OK,
)
async def resend_invite(
    org_id: int,
    request: ResendInviteRequest,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
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
            grafana_ok, grafana_link = await grafana.invite_user(
                org.grafana_org_id, request.email
            )
    except Exception as exc:
        _raise_external_error(exc, "Grafana")

    try:
        if org.glitchtip_slug:
            glitchtip_ok, glitchtip_link = await glitchtip.invite_member(
                org.glitchtip_slug, request.email
            )
    except Exception as exc:
        _raise_external_error(exc, "GlitchTip")

    return ResendInviteResponse(
        email=request.email,
        grafana_invited=grafana_ok,
        grafana_invite_link=grafana_link,
        glitchtip_invited=glitchtip_ok,
        glitchtip_invite_link=glitchtip_link,
    )


@router.delete(
    "/organizations/{org_id}/users/{user_id}",
    response_model=DeleteUserResponse,
)
async def delete_user(
    org_id: int,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    grafana: GrafanaClient = Depends(get_grafana_client),
    glitchtip: GlitchtipClient = Depends(get_glitchtip_client),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(
        select(Organization).where(Organization.id == org_id, Organization.is_active == True)  # noqa: E712
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    grafana_deleted = False
    glitchtip_deleted = False

    if org.grafana_org_id:
        try:
            grafana_deleted = await grafana.delete_org_user(
                org.grafana_org_id, int(user_id)
            )
        except ValueError:
            grafana_deleted = False
        except Exception as exc:
            _raise_external_error(exc, "Grafana")

    if org.glitchtip_slug:
        try:
            glitchtip_deleted = await glitchtip.delete_member(org.glitchtip_slug, user_id)
        except Exception as exc:
            _raise_external_error(exc, "GlitchTip")

    if not grafana_deleted and not glitchtip_deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return DeleteUserResponse(
        user_id=user_id,
        grafana_deleted=grafana_deleted,
        glitchtip_deleted=glitchtip_deleted,
    )
