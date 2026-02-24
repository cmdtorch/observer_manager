import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.organization import Organization
from app.models.telegram_group import TelegramGroup
from app.schemas.telegram_group import TelegramWebhookResponse

router = APIRouter()
logger = structlog.get_logger()


@router.post("/telegram/webhook", response_model=TelegramWebhookResponse)
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle incoming Telegram bot updates.

    When the bot is added to a group, auto-create a TelegramGroup record
    and link to an org if telegram_chat matches.
    Always returns 200 OK to prevent Telegram from retrying.
    """
    try:
        body = await request.json()
    except Exception:
        return TelegramWebhookResponse(ok=True, message="invalid payload ignored")

    log = logger.bind(update_id=body.get("update_id"))

    # Handle my_chat_member event (bot was added/removed from a group)
    my_chat_member = body.get("my_chat_member")
    if my_chat_member:
        new_status = my_chat_member.get("new_chat_member", {}).get("status")
        chat = my_chat_member.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_title = chat.get("title") or chat.get("username") or chat_id
        chat_type = chat.get("type", "")

        if new_status in ("member", "administrator") and chat_type in ("group", "supergroup"):
            log.info("telegram_bot_added_to_group", chat_id=chat_id, chat_title=chat_title)

            # Upsert TelegramGroup
            existing_result = await db.execute(
                select(TelegramGroup).where(TelegramGroup.chat_id == chat_id)
            )
            tg_group = existing_result.scalar_one_or_none()

            if not tg_group:
                tg_group = TelegramGroup(name=chat_title, chat_id=chat_id)
                db.add(tg_group)
                await db.flush()

            # Check if any org has telegram_chat matching this chat_id
            if not tg_group.org_id:
                org_result = await db.execute(
                    select(Organization).where(
                        Organization.telegram_chat == chat_id,
                        Organization.is_active == True,  # noqa: E712
                    )
                )
                org = org_result.scalar_one_or_none()
                if org:
                    tg_group.org_id = org.id
                    log.info("telegram_group_linked_to_org", org_id=str(org.id))

            tg_group.name = chat_title
            await db.commit()
            return TelegramWebhookResponse(ok=True, message="TelegramGroup upserted")

    return TelegramWebhookResponse(ok=True, message="update processed")
