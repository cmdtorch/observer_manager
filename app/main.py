import logging
from contextlib import asynccontextmanager

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import health, organizations, api_keys, applications
from app.services.grafana_client import GrafanaClient
from app.services.glitchtip_client import GlitchtipClient
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

    app.state.grafana_client = GrafanaClient(settings, http_client)
    app.state.glitchtip_client = GlitchtipClient(settings, http_client)
    app.state.nginx_manager = NginxManager(settings)

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


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=str(request.url))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(health.router, prefix="/api")
app.include_router(organizations.router, prefix="/api")
app.include_router(api_keys.router, prefix="/api")
app.include_router(applications.router, prefix="/api")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )

