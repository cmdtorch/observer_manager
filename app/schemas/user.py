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
    email: str | None = None
    grafana: GrafanaUserDetail | None = None
    glitchtip: GlitchtipUserDetail | None = None


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
