import asyncio
import json
import re
import time
from pathlib import Path

import httpx
import structlog

from app.core.config import Settings

logger = structlog.get_logger()

ALERTS_DIR = Path(__file__).parent.parent.parent / "assets" / "alerts"


def _load_alert(filename: str) -> dict:
    with open(ALERTS_DIR / filename) as f:
        return json.load(f)


class GrafanaService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.base_url = settings.grafana_url.rstrip("/")
        self.auth = (settings.grafana_admin_user, settings.grafana_admin_password)
        self.client = client

    async def _request(
        self,
        method: str,
        path: str,
        extra_headers: dict | None = None,
        **kwargs,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        start = time.monotonic()
        try:
            response = await self.client.request(
                method, url, auth=self.auth, headers=headers, timeout=10.0, **kwargs
            )
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.info(
                "grafana_request",
                method=method,
                url=url,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        except Exception as exc:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.error(
                "grafana_request_error",
                method=method,
                url=url,
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise

    # ── Org management ──────────────────────────────────────────────────────

    async def create_org(self, name: str) -> int:
        """POST /api/orgs → returns grafana org_id"""
        response = await self._request("POST", "/api/orgs", json={"name": name})
        response.raise_for_status()
        return response.json()["orgId"]

    async def delete_org(self, org_id: int) -> None:
        """DELETE /api/orgs/{org_id}"""
        response = await self._request("DELETE", f"/api/orgs/{org_id}")
        if response.status_code not in (200, 404):
            response.raise_for_status()

    async def add_admin_to_org(self, org_id: int) -> None:
        """POST /api/orgs/{org_id}/users — adds admin user as Admin role"""
        response = await self._request(
            "POST",
            f"/api/orgs/{org_id}/users",
            json={"loginOrEmail": self.auth[0], "role": "Admin"},
        )
        if response.status_code not in (200, 409):
            response.raise_for_status()

    # ── User management ─────────────────────────────────────────────────────

    async def find_user_by_email(self, email: str) -> int | None:
        """GET /api/users/lookup?loginOrEmail={email} → returns grafana user_id or None"""
        response = await self._request(
            "GET", f"/api/users/lookup?loginOrEmail={email}"
        )
        if response.status_code == 404:
            return None
        if response.status_code == 200:
            return response.json().get("id")
        return None

    async def add_existing_user_to_org(
        self, login_or_email: str, grafana_org_id: int, role: str = "Editor"
    ) -> None:
        """POST /api/orgs/{grafana_org_id}/users — add existing Grafana user to org"""
        response = await self._request(
            "POST",
            f"/api/orgs/{grafana_org_id}/users",
            json={"loginOrEmail": login_or_email, "role": role},
        )
        if response.status_code not in (200, 409):
            response.raise_for_status()

    async def invite_user(
        self, org_id: int, email: str, role: str = "Editor"
    ) -> tuple[bool, str | None]:
        """POST /api/org/invites — returns (success, invite_url)"""
        response = await self._request(
            "POST",
            "/api/org/invites",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json={"loginOrEmail": email, "role": role, "sendEmail": True},
        )
        if response.status_code in (200, 201):
            data = response.json()
            invite_url = data.get("inviteUrl") or data.get("url")
            return True, invite_url
        logger.warning(
            "grafana_invite_failed",
            email=email,
            org_id=org_id,
            status_code=response.status_code,
            body=response.text,
        )
        return False, None

    async def get_org_users(self, org_id: int) -> list[dict]:
        """GET /api/org/users — list users within org"""
        response = await self._request(
            "GET",
            "/api/org/users",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    async def delete_org_user(self, org_id: int, user_id: int) -> bool:
        """DELETE /api/orgs/{org_id}/users/{user_id}"""
        response = await self._request(
            "DELETE",
            f"/api/orgs/{org_id}/users/{user_id}",
        )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    # ── Datasources ──────────────────────────────────────────────────────────

    async def create_datasource(self, org_id: int, datasource_config: dict) -> dict:
        """POST /api/datasources with X-Grafana-Org-Id header"""
        response = await self._request(
            "POST",
            "/api/datasources",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json=datasource_config,
        )
        response.raise_for_status()
        return response.json()

    async def create_all_datasources(self, org_id: int, scope_org_id: str) -> dict:
        """Creates Mimir, Loki, Tempo datasources. Returns dict with UIDs."""
        mimir_config = {
            "name": "Mimir",
            "type": "prometheus",
            "uid": "mimir",
            "url": "http://mimir:9009/prometheus",
            "access": "proxy",
            "jsonData": {
                "httpMethod": "POST",
                "httpHeaderName1": "X-Scope-OrgID",
                "exemplarTraceIdDestinations": [
                    {"name": "traceID", "datasourceUid": "tempo"}
                ],
            },
            "secureJsonData": {"httpHeaderValue1": scope_org_id},
        }
        loki_config = {
            "name": "Loki",
            "type": "loki",
            "uid": "loki",
            "url": "http://loki:3100",
            "access": "proxy",
            "jsonData": {
                "httpHeaderName1": "X-Scope-OrgID",
                "derivedFields": [
                    {
                        "datasourceUid": "tempo",
                        "matcherRegex": '"traceID":"(\\w+)"',
                        "name": "traceID",
                        "url": "${__value.raw}",
                    }
                ],
            },
            "secureJsonData": {"httpHeaderValue1": scope_org_id},
        }
        tempo_config = {
            "name": "Tempo",
            "type": "tempo",
            "uid": "tempo",
            "url": "http://tempo:3200",
            "access": "proxy",
            "isDefault": True,
            "jsonData": {
                "httpMethod": "GET",
                "httpHeaderName1": "X-Scope-OrgID",
                "tracesToMetrics": {
                    "datasourceUid": "mimir",
                    "spanStartTimeShift": "-1h",
                    "spanEndTimeShift": "1h",
                    "tags": [{"key": "service.name", "value": "service"}],
                },
                "tracesToLogs": {
                    "datasourceUid": "loki",
                    "spanStartTimeShift": "-1h",
                    "spanEndTimeShift": "1h",
                    "tags": [{"key": "service.name", "value": "service_name"}],
                },
                "serviceMap": {"datasourceUid": "mimir"},
                "nodeGraph": {"enabled": True},
                "search": {"hide": False},
                "traceQuery": {
                    "timeShiftEnabled": True,
                    "spanStartTimeShift": "-1h",
                    "spanEndTimeShift": "1h",
                },
            },
            "secureJsonData": {"httpHeaderValue1": scope_org_id},
        }

        await self.create_datasource(org_id, mimir_config)
        await self.create_datasource(org_id, loki_config)
        await self.create_datasource(org_id, tempo_config)

        return {"mimir": "mimir", "loki": "loki", "tempo": "tempo"}

    # ── Dashboards ───────────────────────────────────────────────────────────

    async def create_folder(self, org_id: int, title: str) -> str:
        """POST /api/folders → returns folder UID"""
        uid = re.sub(r"[^a-z0-9-]", "-", title.lower())[:40]
        response = await self._request(
            "POST",
            "/api/folders",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json={"title": title, "uid": uid},
        )
        if response.status_code == 412:
            # Folder already exists with this UID — return it as-is
            return uid
        response.raise_for_status()
        return response.json()["uid"]

    async def import_dashboard(
        self, org_id: int, dashboard_json: dict, folder_uid: str
    ) -> None:
        """POST /api/dashboards/import"""
        payload = {
            "dashboard": dashboard_json,
            "overwrite": True,
            "folderId": 0,
            "folderUid": folder_uid,
            "inputs": [],
        }
        response = await self._request(
            "POST",
            "/api/dashboards/import",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json=payload,
        )
        response.raise_for_status()

    # ── Service accounts (internal) ──────────────────────────────────────────

    async def _create_temp_service_account_token(self, org_id: int) -> tuple[str, int]:
        """Create a temporary Admin service account in the org.
        Returns (token, service_account_id).
        """
        sa_response = await self._request(
            "POST",
            "/api/serviceaccounts",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json={"name": "observer-manager-provisioning", "role": "Admin"},
        )
        sa_response.raise_for_status()
        sa_id = sa_response.json()["id"]

        token_response = await self._request(
            "POST",
            f"/api/serviceaccounts/{sa_id}/tokens",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json={"name": "temp-provisioning-token"},
        )
        token_response.raise_for_status()
        return token_response.json()["key"], sa_id

    async def _delete_service_account(self, org_id: int, sa_id: int) -> None:
        await self._request(
            "DELETE",
            f"/api/serviceaccounts/{sa_id}",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
        )

    # ── Alerting ─────────────────────────────────────────────────────────────

    async def get_contact_points(self, grafana_org_id: int) -> list[dict]:
        """GET /api/v1/provisioning/contact-points scoped to org."""
        sa_token, sa_id = await self._create_temp_service_account_token(grafana_org_id)
        try:
            url = f"{self.base_url}/api/v1/provisioning/contact-points"
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }

            # Wait for Alertmanager to be ready
            deadline = time.monotonic() + 90
            while True:
                response = await self.client.get(url, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Alertmanager not ready after 90s (last status: {response.status_code})"
                    )
                await asyncio.sleep(15)

            data = response.json()
            if isinstance(data, list):
                return data
            return data.get("items") or data.get("contactPoints") or []
        finally:
            try:
                await self._delete_service_account(grafana_org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def create_contact_point(
        self, grafana_org_id: int, chat_id: str, name: str
    ) -> dict:
        """POST /api/v1/provisioning/contact-points — create telegram contact point."""
        from app.core.config import get_settings
        settings = get_settings()
        bot_token = settings.telegram_bot_token

        sa_token, sa_id = await self._create_temp_service_account_token(grafana_org_id)
        try:
            url = f"{self.base_url}/api/v1/provisioning/contact-points"
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }

            # Wait for Alertmanager to be ready
            deadline = time.monotonic() + 90
            while True:
                probe = await self.client.get(url, headers=headers, timeout=10.0)
                if probe.status_code == 200:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Alertmanager not ready after 90s (last status: {probe.status_code})"
                    )
                await asyncio.sleep(15)

            response = await self.client.post(
                url,
                headers=headers,
                json={
                    "name": name,
                    "type": "telegram",
                    "settings": {"bottoken": bot_token, "chatid": chat_id},
                    "disableResolveMessage": False,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        finally:
            try:
                await self._delete_service_account(grafana_org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def update_contact_point(
        self, grafana_org_id: int, uid: str, chat_id: str
    ) -> dict:
        """PUT /api/v1/provisioning/contact-points/{uid} — update chat_id in settings."""
        from app.core.config import get_settings
        settings = get_settings()
        bot_token = settings.telegram_bot_token

        sa_token, sa_id = await self._create_temp_service_account_token(grafana_org_id)
        try:
            url = f"{self.base_url}/api/v1/provisioning/contact-points/{uid}"
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }
            response = await self.client.put(
                url,
                headers=headers,
                json={
                    "type": "telegram",
                    "settings": {"bottoken": bot_token, "chatid": chat_id},
                    "disableResolveMessage": False,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        finally:
            try:
                await self._delete_service_account(grafana_org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def set_default_contact_point(
        self, grafana_org_id: int, contact_point_name: str
    ) -> dict:
        """PUT /api/v1/provisioning/policies — set default notification policy."""
        sa_token, sa_id = await self._create_temp_service_account_token(grafana_org_id)
        try:
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }
            policies_url = f"{self.base_url}/api/v1/provisioning/policies"
            response = await self.client.put(
                policies_url,
                headers=headers,
                json={
                    "receiver": contact_point_name,
                    "group_by": ["grafana_folder", "alertname"],
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        finally:
            try:
                await self._delete_service_account(grafana_org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def upsert_telegram_contact_point(
        self, org_id: int, bot_token: str, chat_id: str
    ) -> None:
        """Create or update Telegram contact point and ensure policy is set."""
        sa_token, sa_id = await self._create_temp_service_account_token(org_id)
        try:
            base_url = f"{self.base_url}/api/v1/provisioning/contact-points"
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }

            deadline = time.monotonic() + 90
            while True:
                probe = await self.client.get(base_url, headers=headers, timeout=10.0)
                if probe.status_code == 200:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Alertmanager not ready after 90s (last status: {probe.status_code})"
                    )
                await asyncio.sleep(15)

            payload = {
                "name": "Telegram",
                "type": "telegram",
                "settings": {"bottoken": bot_token, "chatid": chat_id},
                "disableResolveMessage": False,
            }

            response = await self.client.get(base_url, headers=headers, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                items = data.get("items") or data.get("contactPoints") or []
            elif isinstance(data, list):
                items = data
            else:
                items = []

            existing = next(
                (
                    item
                    for item in items
                    if str(item.get("name", "")).lower() == "telegram"
                ),
                None,
            )

            if existing and (existing.get("uid") or existing.get("id")):
                uid = existing.get("uid") or existing.get("id")
                update_url = f"{base_url}/{uid}"
                update_response = await self.client.put(
                    update_url, headers=headers, json=payload, timeout=10.0
                )
                if update_response.status_code == 404:
                    create_response = await self.client.post(
                        base_url, headers=headers, json=payload, timeout=10.0
                    )
                    create_response.raise_for_status()
                else:
                    update_response.raise_for_status()
            else:
                create_response = await self.client.post(
                    base_url, headers=headers, json=payload, timeout=10.0
                )
                create_response.raise_for_status()

            policies_url = f"{self.base_url}/api/v1/provisioning/policies"
            policy_response = await self.client.put(
                policies_url,
                headers=headers,
                json={
                    "receiver": "Telegram",
                    "group_by": ["grafana_folder", "alertname"],
                },
                timeout=10.0,
            )
            policy_response.raise_for_status()
        finally:
            try:
                await self._delete_service_account(org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def create_alert_rule(self, org_id: int, folder_uid: str, rule_config: dict) -> None:
        """Create a single alert rule via provisioning API."""
        sa_token, sa_id = await self._create_temp_service_account_token(org_id)
        try:
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }
            rule = dict(rule_config)
            rule["folderUID"] = folder_uid
            rule["orgID"] = org_id
            # Replace datasource placeholder
            for step in rule.get("data", []):
                if step.get("datasourceUid") == "{{prometheus_uid}}":
                    step["datasourceUid"] = "mimir"

            url = f"{self.base_url}/api/v1/provisioning/alert-rules"
            response = await self.client.post(url, headers=headers, json=rule, timeout=10.0)
            response.raise_for_status()
        finally:
            try:
                await self._delete_service_account(org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def create_default_alert_rules(
        self, org_id: int, folder_uid: str, prometheus_uid: str = "mimir"
    ) -> None:
        """Create High CPU, High RAM, and High Disk alert rules."""
        sa_token, sa_id = await self._create_temp_service_account_token(org_id)
        try:
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/api/v1/provisioning/alert-rules"

            alert_files = ["high_cpu.json", "high_ram.json", "high_disk.json"]
            for filename in alert_files:
                rule = _load_alert(filename)
                rule["folderUID"] = folder_uid
                rule["orgID"] = org_id
                for step in rule.get("data", []):
                    if step.get("datasourceUid") == "{{prometheus_uid}}":
                        step["datasourceUid"] = prometheus_uid

                start = time.monotonic()
                response = await self.client.post(url, headers=headers, json=rule, timeout=10.0)
                duration_ms = round((time.monotonic() - start) * 1000, 1)
                logger.info(
                    "grafana_request",
                    method="POST",
                    url=url,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                response.raise_for_status()
        finally:
            try:
                await self._delete_service_account(org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    # Keep backward-compat alias
    async def switch_org(self, org_id: int) -> None:
        response = await self._request("POST", f"/api/user/using/{org_id}")
        response.raise_for_status()


# Backward-compat alias
GrafanaClient = GrafanaService
