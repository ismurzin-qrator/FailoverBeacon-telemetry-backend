import base64
import http.client
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FOLDER_UID = "beacon"
FOLDER_TITLE = "Beacon"


class GrafanaError(Exception):
    pass


class Grafana:
    def __init__(self, url: str, password: str) -> None:
        parts = urlsplit(url)
        self._host = parts.hostname or "grafana"
        self._port = parts.port or 3000
        token = base64.b64encode(f"admin:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    @staticmethod
    def from_env() -> "Grafana":
        password = Path(os.environ["GRAFANA_PASSWORD_FILE"]).read_text().strip()
        return Grafana(os.environ.get("GRAFANA_URL", "http://grafana:3000"), password)

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        connection = http.client.HTTPConnection(self._host, self._port, timeout=30)
        try:
            payload = None if body is None else json.dumps(body)
            connection.request(method, path, body=payload, headers=self._headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def wait(self, seconds: int = 60) -> None:
        for _ in range(seconds):
            try:
                if self.request("GET", "/api/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(1)
        raise GrafanaError("Grafana is not up")


def export(grafana: Grafana) -> dict[str, dict[str, Any]]:
    status, found = grafana.request("GET", "/api/search?type=dash-db&limit=5000")
    if status != 200:
        raise GrafanaError(f"search: HTTP {status}")
    dashboards = {}
    for item in found:
        status, body = grafana.request("GET", f"/api/dashboards/uid/{item['uid']}")
        if status != 200:
            raise GrafanaError(f"{item['uid']}: HTTP {status}")
        dashboard = body["dashboard"]
        dashboard.pop("id", None)
        dashboard.pop("version", None)
        dashboards[dashboard["uid"]] = dashboard
    return dashboards


def save(dashboards: dict[str, dict[str, Any]], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for stale in set(directory.glob("*.json")) - {directory / f"{uid}.json" for uid in dashboards}:
        stale.unlink()
    for uid, dashboard in dashboards.items():
        text = json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n"
        (directory / f"{uid}.json").write_text(text)


def import_missing(grafana: Grafana, directory: Path) -> list[str]:
    if grafana.request("GET", f"/api/folders/{FOLDER_UID}")[0] == 404:
        status, _ = grafana.request(
            "POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE}
        )
        if status != 200:
            raise GrafanaError(f"folder: HTTP {status}")
    created = []
    for path in sorted(directory.glob("*.json")):
        dashboard = json.loads(path.read_text())
        if grafana.request("GET", f"/api/dashboards/uid/{dashboard['uid']}")[0] == 200:
            continue
        body = {"dashboard": dashboard, "folderUid": FOLDER_UID, "overwrite": False}
        status, _ = grafana.request("POST", "/api/dashboards/db", body)
        if status != 200:
            raise GrafanaError(f"{path.name}: HTTP {status}")
        created.append(dashboard["title"])
    return created
