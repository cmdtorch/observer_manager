import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.organization import Organization
from app.models.telegram_group import TelegramGroup
from app.schemas.telegram_group import TelegramGroupRead, TelegramWebhookResponse

router = APIRouter()
logger = structlog.get_logger()


@router.get("/telegram/groups", response_model=list[TelegramGroupRead])
async def list_telegram_groups(
    unlinked_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """List all known Telegram groups.

    If unlinked_only=True, return only groups not linked to any organization
    (i.e., no Organization has telegram_group_id pointing to them).
    """
    query = select(TelegramGroup).options(selectinload(TelegramGroup.organization))
    if unlinked_only:
        query = query.where(
            ~select(Organization.id)
            .where(Organization.telegram_group_id == TelegramGroup.id)
            .correlate(TelegramGroup)
            .exists()
        )
    result = await db.execute(query)
    groups = result.scalars().all()

    return [
        TelegramGroupRead(
            id=tg.id,
            name=tg.name,
            chat_id=tg.chat_id,
            org_id=tg.organization.id if tg.organization else None,
            org_name=tg.organization.name if tg.organization else None,
            created_at=tg.created_at,
            updated_at=tg.updated_at,
        )
        for tg in groups
    ]


@router.post("/telegram/webhook", response_model=TelegramWebhookResponse)
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle incoming Telegram bot updates.

    When the bot is added to a group, auto-create a TelegramGroup record.
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

            tg_group.name = chat_title
            await db.commit()
            return TelegramWebhookResponse(ok=True, message="TelegramGroup upserted")

    return TelegramWebhookResponse(ok=True, message="update processed")
