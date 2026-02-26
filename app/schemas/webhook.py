from pydantic import BaseModel


class AttachmentField(BaseModel):
    title: str
    value: str
    short: bool = False


class Attachment(BaseModel):
    title: str = ""
    title_link: str | None = None
    text: str | None = None
    color: str | None = None
    fields: list[AttachmentField] = []


class GlitchTipPayload(BaseModel):
    alias: str = "GlitchTip"
    text: str = "GlitchTip Alert"
    attachments: list[Attachment] = []
