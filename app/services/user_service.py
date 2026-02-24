from app.models.organization import Organization
from app.services.clients.grafana_client import GrafanaService
from app.services.clients.glitchtip_client import GlitchTipService


def _user_key(email: str | None, fallback: str) -> str:
    if email:
        return email.strip().lower()
    return fallback


async def fetch_org_users(
    org: Organization,
    grafana: GrafanaService,
    glitchtip: GlitchTipService,
) -> list[dict]:
    users: dict[str, dict] = {}

    if org.grafana_org_id:
        grafana_users = await grafana.get_org_users(org.grafana_org_id)
        for user in grafana_users:
            if user.get("isDisabled") is True:
                continue
            email = user.get("email")
            key = _user_key(email, f"grafana:{user.get('userId')}")
            users.setdefault(key, {"email": email, "grafana": None, "glitchtip": None})
            users[key]["grafana"] = {
                "user_id": user.get("userId"),
                "email": email,
                "login": user.get("login"),
                "name": user.get("name"),
                "role": user.get("role"),
                "is_disabled": user.get("isDisabled"),
            }

    if org.glitchtip_slug:
        members = await glitchtip.get_org_members(org.glitchtip_slug)
        for member in members:
            if member.get("pending") is True:
                continue
            email = member.get("email") or member.get("user", {}).get("email")
            key = _user_key(email, f"glitchtip:{member.get('id')}")
            users.setdefault(key, {"email": email, "grafana": None, "glitchtip": None})
            users[key]["glitchtip"] = {
                "member_id": str(member.get("id")) if member.get("id") is not None else None,
                "user_id": str(member.get("user", {}).get("id"))
                if member.get("user", {}).get("id") is not None
                else None,
                "email": email,
                "name": member.get("name") or member.get("user", {}).get("name"),
                "role": member.get("role") or member.get("orgRole"),
                "is_active": member.get("isActive"),
                "is_pending": member.get("pending"),
            }

    return list(users.values())
