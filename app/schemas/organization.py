from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


class CreateOrganizationRequest(BaseModel):
    name: str
    telegram_chat_id: str | None = None
    emails: list[str] | None = None

    @field_validator("name")
    @classmethod
    def name_length(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or len(v) > 100:
            raise ValueError("name must be between 2 and 100 characters")
        return v


class OtlpHeaders(BaseModel):
    Authorization: str
    Content_Type: str = "application/json"

    model_config = {"populate_by_name": True}


class CreateOrganizationResponse(BaseModel):
    id: int
    name: str
    slug: str
    scope_org_id: str
    grafana_org_id: int | None
    grafana_url: str
    glitchtip_slug: str | None
    glitchtip_url: str
    api_key: str
    otlp_endpoint: str
    otlp_headers: dict
    invited_users: list[str]
    telegram_configured: bool


class OrganizationListItem(BaseModel):
    id: int
    name: str
    slug: str
    grafana_org_id: int | None
    glitchtip_slug: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyDetail(BaseModel):
    id: int
    key_masked: str
    description: str | None
    is_active: bool
    created_at: datetime


class ApplicationDetail(BaseModel):
    id: int
    name: str
    platform: str | None
    glitchtip_dsn: str | None
    created_at: datetime


class InvitedUserDetail(BaseModel):
    id: int
    email: str
    grafana_invited: bool
    grafana_invite_link: str | None
    glitchtip_invited: bool
    glitchtip_invite_link: str | None
    created_at: datetime


class OrganizationDetail(BaseModel):
    id: int
    name: str
    slug: str
    grafana_org_id: int | None
    glitchtip_slug: str | None
    telegram_chat_id: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    api_keys: list[ApiKeyDetail]
    applications: list[ApplicationDetail]
    invited_users: list[InvitedUserDetail]

    model_config = {"from_attributes": True}


class DeleteOrganizationResponse(BaseModel):
    message: str
    organization_id: int
    name: str


class SetupTelegramRequest(BaseModel):
    chat_id: str


class SetupTelegramResponse(BaseModel):
    org_id: int
    chat_id: str
    message: str
