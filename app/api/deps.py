"""FastAPI dependencies for service clients."""
from fastapi import Request

from app.services.clients.grafana_client import GrafanaClient
from app.services.clients.glitchtip_client import GlitchtipClient
from app.services.nginx_manager import NginxManager


def get_grafana_client(request: Request) -> GrafanaClient:
    return request.app.state.grafana_client


def get_glitchtip_client(request: Request) -> GlitchtipClient:
    return request.app.state.glitchtip_client


def get_nginx_manager(request: Request) -> NginxManager:
    return request.app.state.nginx_manager
