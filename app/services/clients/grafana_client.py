import asyncio
import time

import httpx
import structlog

from app.core.config import Settings

logger = structlog.get_logger()


class GrafanaClient:
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

    async def create_org(self, name: str) -> int:
        """POST /api/orgs → returns org_id"""
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
        # 409 = already member, treat as success
        if response.status_code not in (200, 409):
            response.raise_for_status()

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

    async def create_folder(self, org_id: int, title: str) -> str:
        """POST /api/folders → returns folder UID"""
        import re

        uid = re.sub(r"[^a-z0-9-]", "-", title.lower())[:40]
        response = await self._request(
            "POST",
            "/api/folders",
            extra_headers={"X-Grafana-Org-Id": str(org_id)},
            json={"title": title, "uid": uid},
        )
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

    async def _create_temp_service_account_token(self, org_id: int) -> tuple[str, int]:
        """Create a temporary Admin service account in the org.
        Returns (token, service_account_id).
        The service account token is inherently org-scoped — no header tricks needed.
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

    async def create_telegram_contact_point(
        self, org_id: int, bot_token: str, chat_id: str
    ) -> None:
        """Set up Telegram contact point using a temporary org-scoped service account.

        The provisioning API requires the caller's token to belong to the target org.
        Basic Auth + X-Grafana-Org-Id is not enough for the provisioning endpoints.
        A service account token created inside the org is always org-scoped.
        """
        sa_token, sa_id = await self._create_temp_service_account_token(org_id)
        try:
            url = f"{self.base_url}/api/v1/provisioning/contact-points"
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }

            # Wait for Alertmanager to be ready for this org before provisioning.
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

            start = time.monotonic()
            response = await self.client.post(
                url,
                headers=headers,
                json={
                    "name": "Telegram",
                    "type": "telegram",
                    "settings": {"bottoken": bot_token, "chatid": chat_id},
                    "disableResolveMessage": False,
                },
                timeout=10.0,
            )
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.info(
                "grafana_request",
                method="POST",
                url=url,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            response.raise_for_status()

            policies_url = f"{self.base_url}/api/v1/provisioning/policies"
            start = time.monotonic()
            policy_response = await self.client.put(
                policies_url,
                headers=headers,
                json={
                    "receiver": "Telegram",
                    "group_by": ["grafana_folder", "alertname"],
                },
                timeout=10.0,
            )
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.info(
                "grafana_request",
                method="PUT",
                url=policies_url,
                status_code=policy_response.status_code,
                duration_ms=duration_ms,
            )
            policy_response.raise_for_status()
        finally:
            try:
                await self._delete_service_account(org_id, sa_id)
            except Exception as exc:
                logger.warning("grafana_delete_service_account_failed", error=str(exc))

    async def create_default_alert_rules(
        self, org_id: int, folder_uid: str, prometheus_uid: str
    ) -> None:
        """Create High CPU, High RAM, and High Disk alert rules via provisioning API."""
        sa_token, sa_id = await self._create_temp_service_account_token(org_id)
        try:
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/api/v1/provisioning/alert-rules"

            rules = [
                {
                    "title": "High CPU",
                    "condition": "C",
                    "data": [
                        {
                            "refId": "A",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": prometheus_uid,
                            "model": {
                                "expr": '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
                                "refId": "A",
                            },
                        },
                        {
                            "refId": "B",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "reduce",
                                "refId": "B",
                                "expression": "A",
                                "reducer": "mean",
                                "settings": {"mode": ""},
                            },
                        },
                        {
                            "refId": "C",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "threshold",
                                "refId": "C",
                                "expression": "B",
                                "conditions": [
                                    {
                                        "evaluator": {"params": [80], "type": "gt"},
                                        "operator": {"type": "and"},
                                        "query": {"params": ["B"]},
                                        "reducer": {"params": [], "type": "last"},
                                        "type": "query",
                                    }
                                ],
                            },
                        },
                    ],
                    "folderUID": folder_uid,
                    "for": "5m",
                    "orgID": org_id,
                    "ruleGroup": "default",
                    "noDataState": "NoData",
                    "execErrState": "Error",
                },
                {
                    "title": "High RAM",
                    "condition": "C",
                    "data": [
                        {
                            "refId": "A",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": prometheus_uid,
                            "model": {
                                "expr": "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
                                "refId": "A",
                            },
                        },
                        {
                            "refId": "B",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "reduce",
                                "refId": "B",
                                "expression": "A",
                                "reducer": "mean",
                                "settings": {"mode": ""},
                            },
                        },
                        {
                            "refId": "C",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "threshold",
                                "refId": "C",
                                "expression": "B",
                                "conditions": [
                                    {
                                        "evaluator": {"params": [85], "type": "gt"},
                                        "operator": {"type": "and"},
                                        "query": {"params": ["B"]},
                                        "reducer": {"params": [], "type": "last"},
                                        "type": "query",
                                    }
                                ],
                            },
                        },
                    ],
                    "folderUID": folder_uid,
                    "for": "5m",
                    "orgID": org_id,
                    "ruleGroup": "default",
                    "noDataState": "NoData",
                    "execErrState": "Error",
                },
                {
                    "title": "High Disk",
                    "condition": "C",
                    "data": [
                        {
                            "refId": "A",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": prometheus_uid,
                            "model": {
                                "expr": '(1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})) * 100',
                                "refId": "A",
                            },
                        },
                        {
                            "refId": "B",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "reduce",
                                "refId": "B",
                                "expression": "A",
                                "reducer": "mean",
                                "settings": {"mode": ""},
                            },
                        },
                        {
                            "refId": "C",
                            "queryType": "",
                            "relativeTimeRange": {"from": 300, "to": 0},
                            "datasourceUid": "__expr__",
                            "model": {
                                "type": "threshold",
                                "refId": "C",
                                "expression": "B",
                                "conditions": [
                                    {
                                        "evaluator": {"params": [90], "type": "gt"},
                                        "operator": {"type": "and"},
                                        "query": {"params": ["B"]},
                                        "reducer": {"params": [], "type": "last"},
                                        "type": "query",
                                    }
                                ],
                            },
                        },
                    ],
                    "folderUID": folder_uid,
                    "for": "5m",
                    "orgID": org_id,
                    "ruleGroup": "default",
                    "noDataState": "NoData",
                    "execErrState": "Error",
                },
            ]

            for rule in rules:
                start = time.monotonic()
                response = await self.client.post(
                    url, headers=headers, json=rule, timeout=10.0
                )
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

    async def switch_org(self, org_id: int) -> None:
        """POST /api/user/using/{org_id} — switch admin context to org"""
        response = await self._request("POST", f"/api/user/using/{org_id}")
        response.raise_for_status()
