import uuid

import httpx
import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import get_settings
from app.db.session import get_db
from app.models.application import Application
from app.models.organization import Organization
from app.models.telegram_group import TelegramGroup
from app.schemas.webhook import GlitchTipPayload
from app.services.telegram import build_message

router = APIRouter()
logger = structlog.get_logger()


@router.get("/webhook/{glitchtip_project_slug}", include_in_schema=False)
async def glitchtip_webhook_verify(glitchtip_project_slug: str):
    return {"status": "ok"}


async def send_telegram_message(
    bot_token: str,
    chat_id: str,
    message: str,
    db: AsyncSession,
    telegram_group_id: uuid.UUID,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

        # Handle supergroup migration — Telegram changes chat_id
        if response.status_code == 400:
            tg_body = response.json()
            migrated_id = tg_body.get("parameters", {}).get("migrate_to_chat_id")
            if migrated_id:
                migrated_id_str = str(migrated_id)
                logger.warning(
                    "telegram_supergroup_migration",
                    old_chat_id=chat_id,
                    new_chat_id=migrated_id_str,
                    telegram_group_id=str(telegram_group_id),
                )
                # Update chat_id in DB
                tg_group = await db.get(TelegramGroup, telegram_group_id)
                if tg_group:
                    tg_group.chat_id = migrated_id_str
                    await db.commit()
                    logger.info(
                        "telegram_group_chat_id_updated",
                        telegram_group_id=str(telegram_group_id),
                        new_chat_id=migrated_id_str,
                    )
                # Retry with new chat_id
                response = await client.post(url, json={
                    "chat_id": migrated_id_str,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })

        response.raise_for_status()
        return response.json()


@router.post("/webhook/{glitchtip_project_slug}", include_in_schema=False)
async def glitchtip_webhook(
    glitchtip_project_slug: str,
    payload: GlitchTipPayload,
    db: AsyncSession = Depends(get_db),
):
    logger.info("glitchtip_webhook_received", slug=glitchtip_project_slug)

    try:
        stmt = (
            select(Application)
            .options(
                joinedload(Application.organization).joinedload(Organization.telegram_group)
            )
            .where(Application.glitchtip_project_slug == glitchtip_project_slug)
        )
        result = await db.execute(stmt)
        application = result.scalar_one_or_none()

        if application is None:
            logger.warning(
                "glitchtip_webhook_skipped",
                slug=glitchtip_project_slug,
                reason="project not found",
            )
            return {"status": "skipped", "reason": "project not found"}

        org = application.organization
        if org is None or org.telegram_group is None:
            logger.warning(
                "glitchtip_webhook_skipped",
                slug=glitchtip_project_slug,
                reason="no telegram group linked",
            )
            return {"status": "skipped", "reason": "no telegram group linked"}

        tg_group = org.telegram_group
        chat_id = tg_group.chat_id
        settings = get_settings()
        bot_token = settings.telegram_bot_token

        message = build_message(payload)
        tg_response = await send_telegram_message(
            bot_token=bot_token,
            chat_id=chat_id,
            message=message,
            db=db,
            telegram_group_id=tg_group.id,
        )

        message_id = tg_response.get("result", {}).get("message_id")
        logger.info(
            "glitchtip_alert_forwarded",
            slug=glitchtip_project_slug,
            org=org.slug,
            chat_id=chat_id,
        )
        return {"status": "ok", "telegram_message_id": message_id}

    except httpx.HTTPStatusError as e:
        logger.error("glitchtip_webhook_error", slug=glitchtip_project_slug, error=str(e))
        return {"status": "error", "reason": "telegram api error", "detail": str(e)}
    except httpx.RequestError as e:
        logger.error("glitchtip_webhook_error", slug=glitchtip_project_slug, error=str(e))
        return {"status": "error", "reason": "telegram request error", "detail": str(e)}
    except Exception as e:
        logger.error("glitchtip_webhook_error", slug=glitchtip_project_slug, error=str(e))
        return {"status": "error", "reason": "unexpected error", "detail": str(e)}
