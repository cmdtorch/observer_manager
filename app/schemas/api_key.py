import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateApiKeyRequest(BaseModel):
    description: str | None = None


class CreateApiKeyResponse(BaseModel):
    id: uuid.UUID
    key: str
    description: str | None
    organization: str
    created_at: datetime


class ApiKeyListItem(BaseModel):
    id: uuid.UUID
    key_masked: str
    description: str | None
    is_active: bool
    created_at: datetime


class DeleteApiKeyResponse(BaseModel):
    message: str
    key_id: uuid.UUID
