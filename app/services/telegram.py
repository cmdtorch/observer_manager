import html

from app.schemas.webhook import Attachment, GlitchTipPayload

COLOR_EMOJI = {
    "#e52b50": "🔴",
    "#f4a836": "⚠️",
    "#1e88e5": "ℹ️",
    "#757575": "🐛",
}

FIELD_EMOJI = {
    "project":     "📦",
    "environment": "🌍",
    "release":     "🏷",
}

SKIP_FIELDS = {"server name"}


def build_message(payload: GlitchTipPayload) -> str:
    attachment = payload.attachments[0] if payload.attachments else Attachment()
    emoji = COLOR_EMOJI.get((attachment.color or "").lower(), "🔴")
    title = html.escape(attachment.title or payload.text)
    lines = [f"{emoji} <b>{title}</b>"]
    for field in attachment.fields:
        if field.title.lower() in SKIP_FIELDS:
            continue
        field_emoji = FIELD_EMOJI.get(field.title.lower(), "•")
        lines.append(
            f"{field_emoji} <b>{html.escape(field.title)}:</b> {html.escape(field.value)}"
        )
    if attachment.title_link:
        lines += ["", f'🔗 <a href="{html.escape(attachment.title_link)}">View Issue on GlitchTip</a>']
    return "\n".join(lines)
