from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:4154515e@127.0.0.1:5432/obs"

    # Auth for Observer Manager API
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # Grafana
    grafana_url: str = "http://grafana:3000"
    grafana_admin_user: str = "admin"
    grafana_admin_password: str = "admin"

    # GlitchTip
    glitchtip_url: str = "http://glitchtip-web:8000"
    glitchtip_api_token: str = ""

    # Telegram
    telegram_bot_token: str = ""

    # Nginx
    nginx_map_path: str = "/shared/nginx/api_keys.map"
    nginx_container_name: str = "lgtm-nginx-1"

    # Domains (for response URLs)
    grafana_domain: str = "grafana.example.com"
    glitchtip_domain: str = "glitchtip.example.com"
    alloy_domain: str = "alloy.example.com"

    # Email domain restriction
    allowed_email_domain: str = "example.com"

    model_config = SettingsConfigDict(env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
