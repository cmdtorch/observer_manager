from datetime import datetime

from pydantic import BaseModel

VALID_PLATFORMS = {"python", "nodejs", "react-native", "nextjs", "other"}


class CreateApplicationRequest(BaseModel):
    name: str
    platform: str | None = None


class CreateApplicationResponse(BaseModel):
    id: int
    name: str
    platform: str | None
    glitchtip_dsn: str | None
    otlp_endpoint: str
    resource_attributes: dict
    instructions: str


class ApplicationListItem(BaseModel):
    id: int
    name: str
    platform: str | None
    glitchtip_dsn: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeleteApplicationResponse(BaseModel):
    message: str
    app_id: int


class InviteUsersRequest(BaseModel):
    emails: list[str]
    grafana_role: str = "Editor"
    glitchtip_role: str = "member"


class InviteUsersResponse(BaseModel):
    results: list[dict]
