"""FastAPI dependencies for service clients."""
from fastapi import Request

from app.services.clients.grafana_client import GrafanaService
from app.services.clients.glitchtip_client import GlitchTipService
from app.services.nginx_manager import NginxManager

# Backward-compat aliases
GrafanaClient = GrafanaService
GlitchtipClient = GlitchTipService


def get_grafana_client(request: Request) -> GrafanaService:
    return request.app.state.grafana_client


def get_glitchtip_client(request: Request) -> GlitchTipService:
    return request.app.state.glitchtip_client


def get_nginx_manager(request: Request) -> NginxManager:
    return request.app.state.nginx_manager
