import uuid
from datetime import datetime

from pydantic import BaseModel


class TelegramGroupRead(BaseModel):
    id: uuid.UUID
    name: str
    chat_id: str
    org_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TelegramGroupCreate(BaseModel):
    name: str
    chat_id: str
    org_id: uuid.UUID | None = None


class TelegramWebhookResponse(BaseModel):
    ok: bool
    message: str
