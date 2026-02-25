import time

import httpx
import structlog

from app.core.config import Settings

logger = structlog.get_logger()


class GlitchTipService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.base_url = settings.glitchtip_url.rstrip("/")
        self.token = settings.glitchtip_api_token
        self.client = client

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        start = time.monotonic()
        try:
            response = await self.client.request(
                method, url, headers=headers, timeout=10.0, **kwargs
            )
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            log_fn = logger.info if response.status_code < 400 else logger.warning
            log_fn(
                "glitchtip_request",
                method=method,
                url=url,
                status_code=response.status_code,
                duration_ms=duration_ms,
                body=response.text if response.status_code >= 400 else None,
            )
            return response
        except Exception as exc:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.error(
                "glitchtip_request_error",
                method=method,
                url=url,
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise

    # ── Org management ──────────────────────────────────────────────────────

    async def create_org(self, name: str) -> tuple[int, str]:
        """POST /api/0/organizations/ → returns (org_id, slug)"""
        response = await self._request(
            "POST", "/api/0/organizations/", json={"name": name}
        )
        response.raise_for_status()
        data = response.json()
        return int(data["id"]), data["slug"]

    async def delete_org(self, slug: str) -> None:
        """DELETE /api/0/organizations/{slug}/"""
        response = await self._request("DELETE", f"/api/0/organizations/{slug}/")
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()

    async def create_team(self, org_slug: str, team_slug: str) -> None:
        """POST /api/0/organizations/{org_slug}/teams/"""
        response = await self._request(
            "POST",
            f"/api/0/organizations/{org_slug}/teams/",
            json={"slug": team_slug},
        )
        response.raise_for_status()

    # ── User management ─────────────────────────────────────────────────────

    async def find_user_by_email(self, org_slug: str, email: str) -> int | None:
        """Check org members for a matching email. Returns member id or None."""
        members = await self.get_org_members(org_slug)
        for member in members:
            member_email = (
                member.get("email")
                or member.get("user", {}).get("email")
            )
            if member_email and member_email.lower() == email.lower():
                raw_id = member.get("id") or member.get("user", {}).get("id")
                return int(raw_id) if raw_id is not None else None
        return None

    async def add_existing_user_to_org(self, org_slug: str, email: str) -> None:
        """Add an existing GlitchTip user to org by re-inviting (idempotent).
        GlitchTip handles this gracefully — if user already exists, they're added directly.
        """
        await self.invite_member(org_slug, email)

    async def invite_member(
        self, org_slug: str, email: str, role: str = "member"
    ) -> tuple[bool, str | None]:
        """POST /api/0/organizations/{org_slug}/members/ → returns (success, invite_url)"""
        response = await self._request(
            "POST",
            f"/api/0/organizations/{org_slug}/members/",
            json={"email": email, "orgRole": role},
        )
        if response.status_code in (200, 201):
            data = response.json()
            invite_url = data.get("inviteLink") or data.get("url")
            return True, invite_url
        logger.warning(
            "glitchtip_invite_failed",
            email=email,
            org_slug=org_slug,
            status_code=response.status_code,
            body=response.text,
        )
        return False, None

    async def get_org_members(self, org_slug: str) -> list[dict]:
        """GET /api/0/organizations/{org_slug}/members/"""
        response = await self._request(
            "GET", f"/api/0/organizations/{org_slug}/members/"
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    async def delete_member(self, org_slug: str, member_id: str | int) -> bool:
        """DELETE /api/0/organizations/{org_slug}/members/{member_id}/"""
        response = await self._request(
            "DELETE",
            f"/api/0/organizations/{org_slug}/members/{member_id}/",
        )
        if response.status_code == 404:
            return False
        if response.status_code not in (200, 204):
            response.raise_for_status()
        return True

    # ── Projects ─────────────────────────────────────────────────────────────

    async def create_project(
        self, org_slug: str, team_slug: str, name: str, platform: str
    ) -> str:
        """POST /api/0/teams/{org_slug}/{team_slug}/projects/ → returns project slug"""
        response = await self._request(
            "POST",
            f"/api/0/teams/{org_slug}/{team_slug}/projects/",
            json={"name": name, "platform": platform or "other"},
        )
        response.raise_for_status()
        return response.json()["slug"]

    async def get_project_dsn(self, org_slug: str, project_slug: str) -> str:
        """GET /api/0/projects/{org_slug}/{project_slug}/keys/ → returns DSN"""
        response = await self._request(
            "GET", f"/api/0/projects/{org_slug}/{project_slug}/keys/"
        )
        response.raise_for_status()
        keys = response.json()
        if keys:
            return keys[0].get("dsn", {}).get("public", "")
        return ""

    async def delete_project(self, org_slug: str, project_slug: str) -> None:
        """DELETE /api/0/projects/{org_slug}/{project_slug}/"""
        response = await self._request(
            "DELETE", f"/api/0/projects/{org_slug}/{project_slug}/"
        )
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()

    # Backward-compat aliases
    async def list_members(self, org_slug: str) -> list[dict]:
        return await self.get_org_members(org_slug)


# Backward-compat alias
GlitchtipClient = GlitchTipService
