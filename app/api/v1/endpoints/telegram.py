import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_credentials
from app.db.session import get_db
from app.models.organization import Organization
from app.models.telegram_group import TelegramGroup
from app.schemas.telegram_group import (
    TelegramGroupCreate,
    TelegramGroupRead,
    TelegramGroupUpdate,
    TelegramWebhookResponse,
)

router = APIRouter()
logger = structlog.get_logger()


@router.get("/telegram/groups", response_model=list[TelegramGroupRead])
async def list_telegram_groups(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(TelegramGroup).order_by(TelegramGroup.created_at))
    return result.scalars().all()


@router.get("/telegram/groups/{group_id}", response_model=TelegramGroupRead)
async def get_telegram_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(TelegramGroup).where(TelegramGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="TelegramGroup not found")
    return group


@router.post(
    "/telegram/groups",
    response_model=TelegramGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_telegram_group(
    payload: TelegramGroupCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    existing = await db.execute(
        select(TelegramGroup).where(TelegramGroup.chat_id == payload.chat_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="TelegramGroup with this chat_id already exists")

    if payload.org_id:
        org_result = await db.execute(
            select(Organization).where(
                Organization.id == payload.org_id,
                Organization.is_active == True,  # noqa: E712
            )
        )
        if not org_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Organization not found")

    group = TelegramGroup(name=payload.name, chat_id=payload.chat_id, org_id=payload.org_id)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


@router.patch("/telegram/groups/{group_id}", response_model=TelegramGroupRead)
async def update_telegram_group(
    group_id: uuid.UUID,
    payload: TelegramGroupUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(TelegramGroup).where(TelegramGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="TelegramGroup not found")

    if payload.org_id is not None:
        org_result = await db.execute(
            select(Organization).where(
                Organization.id == payload.org_id,
                Organization.is_active == True,  # noqa: E712
            )
        )
        if not org_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Organization not found")
        group.org_id = payload.org_id

    if payload.name is not None:
        group.name = payload.name

    await db.commit()
    await db.refresh(group)
    return group


@router.delete("/telegram/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_telegram_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    result = await db.execute(select(TelegramGroup).where(TelegramGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="TelegramGroup not found")
    await db.delete(group)
    await db.commit()


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
