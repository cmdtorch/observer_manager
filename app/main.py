import logging
from contextlib import asynccontextmanager

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1.router import api_router
from app.services.clients.grafana_client import GrafanaService
from app.services.clients.glitchtip_client import GlitchTipService
from app.services.nginx_manager import NginxManager

# Configure structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    http_client = httpx.AsyncClient()

    app.state.grafana_client = GrafanaService(settings, http_client)
    app.state.glitchtip_client = GlitchTipService(settings, http_client)
    app.state.nginx_manager = NginxManager(settings)

    if settings.telegram_bot_token and settings.telegram_webhook_url:
        try:
            resp = await http_client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                json={
                    "url": settings.telegram_webhook_url,
                    "allowed_updates": ["message", "my_chat_member"],
                },
            )
            logger.info("telegram_webhook_set", status=resp.status_code, body=resp.json())
        except Exception as e:
            logger.warning("telegram_webhook_set_failed", error=str(e))

    logger.info("observer_manager_started")
    yield

    await http_client.aclose()
    logger.info("observer_manager_stopped")


app = FastAPI(
    title="Observer Manager",
    version="1.0.0",
    description="Centralized management API for self-hosted observability stack",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parse_cors_list(settings.cors_allow_origins),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.parse_cors_list(settings.cors_allow_methods),
    allow_headers=settings.parse_cors_list(settings.cors_allow_headers),
)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=str(request.url))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )

