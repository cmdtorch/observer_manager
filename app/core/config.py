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
    telegram_webhook_url: str = ""

    # Domains (for response URLs)
    grafana_domain: str = "grafana.example.com"
    glitchtip_domain: str = "glitchtip.example.com"
    alloy_domain: str = "alloy.example.com"

    # Email domain restriction
    allowed_email_domain: str = "example.com"

    # Internal service URL (used to construct webhook URLs sent to GlitchTip)
    public_base_url: str = "http://observer_manager:8080"

    # CORS
    cors_allow_origins: str = "*"
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    cors_allow_credentials: bool = False

    model_config = SettingsConfigDict(env_file=".env")

    @staticmethod
    def parse_cors_list(value: str) -> list[str]:
        if not value:
            return []
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass
        parts = [p.strip() for p in raw.split(",")]
        return [p for p in parts if p]


@lru_cache
def get_settings() -> Settings:
    return Settings()
