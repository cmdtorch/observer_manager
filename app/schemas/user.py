import uuid
from datetime import datetime

from pydantic import BaseModel


class GrafanaUserDetail(BaseModel):
    user_id: int | None = None
    email: str | None = None
    login: str | None = None
    name: str | None = None
    role: str | None = None
    is_disabled: bool | None = None


class GlitchtipUserDetail(BaseModel):
    member_id: str | None = None
    user_id: str | None = None
    email: str | None = None
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    is_pending: bool | None = None


class UserDetail(BaseModel):
    """Merged live view of a user from Grafana + GlitchTip."""
    email: str | None = None
    grafana: GrafanaUserDetail | None = None
    glitchtip: GlitchtipUserDetail | None = None


class UserRead(BaseModel):
    """DB User record with sync status flags."""
    id: uuid.UUID
    email: str
    grafana_id: int | None
    grafana_invite_url: str | None
    glitchtip_id: int | None
    glitchtip_invite_url: str | None
    needs_grafana_sync: bool
    needs_glitchtip_sync: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user: object) -> "UserRead":
        return cls(
            id=user.id,
            email=user.email,
            grafana_id=user.grafana_id,
            grafana_invite_url=user.grafana_invite_url,
            glitchtip_id=user.glitchtip_id,
            glitchtip_invite_url=user.glitchtip_invite_url,
            needs_grafana_sync=user.grafana_id is None,
            needs_glitchtip_sync=user.glitchtip_id is None,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class OrgUserAddRequest(BaseModel):
    """Add a user to an org. Provide either user_id (existing) or email (create/lookup)."""
    user_id: uuid.UUID | None = None
    email: str | None = None


class ResendInviteRequest(BaseModel):
    email: str


class ResendInviteResponse(BaseModel):
    email: str
    grafana_invited: bool
    grafana_invite_link: str | None
    glitchtip_invited: bool
    glitchtip_invite_link: str | None


class DeleteUserResponse(BaseModel):
    user_id: str
    grafana_deleted: bool
    glitchtip_deleted: bool
