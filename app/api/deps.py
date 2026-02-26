"""FastAPI dependencies for service clients."""
from fastapi import Request

from app.services.clients.grafana_client import GrafanaService
from app.services.clients.glitchtip_client import GlitchTipService

# Backward-compat aliases
GrafanaClient = GrafanaService
GlitchtipClient = GlitchTipService


def get_grafana_client(request: Request) -> GrafanaService:
    return request.app.state.grafana_client


def get_glitchtip_client(request: Request) -> GlitchTipService:
    return request.app.state.glitchtip_client
