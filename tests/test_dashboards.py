import json
from pathlib import Path
from typing import Any

import pytest

from beacon import dashboards
from beacon.dashboards import Grafana, GrafanaError

REPOSITORY = Path(__file__).parent.parent / "grafana" / "dashboards"


class FakeGrafana(Grafana):
    def __init__(self) -> None:
        super().__init__("http://grafana:3000", "secret")
        self.folders: set[str] = set()
        self.boards: dict[str, dict[str, Any]] = {}
        self.fail_saves = False

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        if path.startswith("/api/folders"):
            if method == "POST":
                self.folders.add(body["uid"])
                return 200, {}
            return (200, {}) if path.rsplit("/", 1)[1] in self.folders else (404, None)
        if path.startswith("/api/search"):
            return 200, [{"uid": uid} for uid in self.boards]
        if path.startswith("/api/dashboards/uid/"):
            board = self.boards.get(path.rsplit("/", 1)[1])
            return (200, {"dashboard": dict(board, id=7, version=3)}) if board else (404, None)
        if path == "/api/dashboards/db":
            if self.fail_saves:
                return 400, None
            self.boards[body["dashboard"]["uid"]] = body["dashboard"]
            return 200, {}
        raise AssertionError(f"{method} {path}")


def test_import_creates_the_folder_and_every_dashboard_of_the_repository():
    grafana = FakeGrafana()

    created = dashboards.import_missing(grafana, REPOSITORY)

    assert grafana.folders == {dashboards.FOLDER_UID}
    assert len(created) == len(list(REPOSITORY.glob("*.json"))) > 0


def test_import_keeps_a_dashboard_edited_in_the_ui():
    grafana = FakeGrafana()
    dashboards.import_missing(grafana, REPOSITORY)
    grafana.boards["beacon-network"]["title"] = "Edited"

    assert dashboards.import_missing(grafana, REPOSITORY) == []
    assert grafana.boards["beacon-network"]["title"] == "Edited"


def test_import_stops_on_a_refused_dashboard():
    grafana = FakeGrafana()
    grafana.fail_saves = True

    with pytest.raises(GrafanaError):
        dashboards.import_missing(grafana, REPOSITORY)


def test_export_and_save_round_trip_without_instance_fields(tmp_path):
    grafana = FakeGrafana()
    dashboards.import_missing(grafana, REPOSITORY)
    (tmp_path / "deleted-in-ui.json").write_text("{}")

    dashboards.save(json.loads(json.dumps(dashboards.export(grafana))), tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        p.name for p in REPOSITORY.glob("*.json")
    )
    saved = json.loads((tmp_path / "beacon-network.json").read_text())
    assert "id" not in saved and "version" not in saved


def test_every_dashboard_is_editable_and_named_by_its_uid():
    for path in REPOSITORY.glob("*.json"):
        board = json.loads(path.read_text())
        assert path.stem == board["uid"] and board["editable"] is True
