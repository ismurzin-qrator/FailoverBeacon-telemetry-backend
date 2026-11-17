import argparse
import asyncio
import base64
import datetime
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from clickhouse_connect.driver.exceptions import ClickHouseError

from beacon import dashboards, geo, mapping, replay, schema, storage
from beacon.clickhouse import ClickHouse
from beacon.config import Settings
from beacon.registry import ANY_PACKAGE, Registry, RegistryError


def new_key() -> str:
    return base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=").lower()


def add_key(data: dict[str, Any], project_id: str, name: str | None, packages: list[str]) -> str:
    project = next((p for p in data["projects"] if p["id"] == project_id), None)
    if project is None:
        if not name or not packages:
            raise RegistryError(f"project {project_id} is new: give --name and --package")
        project = {
            "id": project_id,
            "name": name,
            "packages": ANY_PACKAGE if packages == [ANY_PACKAGE] else packages,
            "keys": [],
        }
        data["projects"].append(project)
    elif name or packages:
        raise RegistryError(f"project {project_id} exists: edit its name and packages in the file")
    key = new_key()
    project["keys"].append(
        {"key": key, "enabled": True, "added": datetime.date.today().isoformat()}
    )
    return key


def disable_key(data: dict[str, Any], key: str) -> str:
    for project in data["projects"]:
        for entry in project["keys"]:
            if entry["key"] == key:
                entry["enabled"] = False
                return str(project["id"])
    raise RegistryError("no such key in the registry")


def server_command(group: str) -> int:
    if group == "schema":
        problems = schema.verify_copy()
        for problem in problems:
            print(f"schema copy: {problem}", file=sys.stderr)
        return 1 if problems else 0
    try:
        settings = Settings.from_env()
        if group == "geo":
            for name in geo.update(settings.geo_dir, datetime.date.today()):
                print(f"installed {name}")
            return 0
        if group == "replay":
            for line in asyncio.run(_replay(settings)):
                print(line)
            return 0
        client = storage.connect(settings)
        if group == "backup":
            print(f"wrote {storage.backup(client)} to the backups volume")
            return 0
        if group == "migrate":
            for name in storage.migrate(client):
                print(f"applied {name}")
            problems = mapping.check(client)
            for problem in problems:
                print(f"mapping: {problem}", file=sys.stderr)
            return 1 if problems else 0
        registry = Registry.load(settings.registry)
        storage.copy_registry(client, registry)
    except (ClickHouseError, storage.MigrationError, RegistryError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"copied {len(registry.projects)} projects")
    return 0


def dashboards_command(action: str, directory: Path) -> int:
    try:
        if action == "save":
            dashboards.save(json.load(sys.stdin), directory)
            return 0
        grafana = dashboards.Grafana.from_env()
        grafana.wait()
        if action == "export":
            json.dump(dashboards.export(grafana), sys.stdout)
            return 0
        for title in dashboards.import_missing(grafana, directory):
            print(f"created {title}")
    except (dashboards.GrafanaError, OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


async def _replay(settings: Settings) -> list[str]:
    clickhouse = ClickHouse(settings)
    try:
        return await replay.replay(await clickhouse.client())
    finally:
        await clickhouse.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="beacon")
    commands = parser.add_subparsers(dest="group", required=True)
    commands.add_parser("migrate", help="apply new migrations, then check the schema mapping")
    commands.add_parser("backup", help="back up the database to the backups volume")
    registry_command = commands.add_parser("registry", help="the registry copies in ClickHouse")
    registry_command.add_subparsers(dest="action", required=True).add_parser(
        "load", help="rewrite the copies from BEACON_REGISTRY"
    )
    geo_command = commands.add_parser("geo", help="the DB-IP Lite databases")
    geo_command.add_subparsers(dest="action", required=True).add_parser(
        "update", help="install this month's databases into BEACON_GEO_DIR"
    )
    commands.add_parser("replay", help="store uploads kept as schema_newer")
    schema_command = commands.add_parser("schema", help="the vendored telemetry schema")
    schema_command.add_subparsers(dest="action", required=True).add_parser(
        "check", help="compare the copy with its SHA256SUMS"
    )
    board_actions = commands.add_parser("dashboards", help="Grafana dashboards").add_subparsers(
        dest="action", required=True
    )
    board_import = board_actions.add_parser("import", help="create the dashboards Grafana lacks")
    board_import.add_argument("dir", type=Path, nargs="?", default=Path("/etc/beacon/dashboards"))
    board_actions.add_parser("export", help="print every dashboard as one JSON object")
    board_save = board_actions.add_parser("save", help="write an export from stdin into a dir")
    board_save.add_argument("dir", type=Path)
    keys = commands.add_parser("keys", help="issue and disable project keys")
    default = os.environ.get("BEACON_REGISTRY")
    registry_file: dict[str, Any] = {"type": Path, "default": default, "required": default is None}
    actions = keys.add_subparsers(dest="action", required=True)

    add = actions.add_parser("add", help="add a key to a project, creating the project if needed")
    add.add_argument("--registry", **registry_file)
    add.add_argument("--project", required=True, help="project ID")
    add.add_argument("--name", help="project name, for a new project")
    add.add_argument(
        "--package",
        action="append",
        default=[],
        help=f"app package, repeatable, for a new project; {ANY_PACKAGE} accepts any",
    )

    disable = actions.add_parser("disable", help="disable a key; its apps get 410 after deploy")
    disable.add_argument("--registry", **registry_file)
    disable.add_argument("key")

    args = parser.parse_args(argv)
    if args.group == "dashboards":
        return dashboards_command(args.action, getattr(args, "dir", Path()))
    if args.group != "keys":
        return server_command(args.group)
    try:
        data = json.loads(args.registry.read_text())
        if args.action == "add":
            key = add_key(data, args.project, args.name, args.package)
            registry = Registry.parse(data, source=str(args.registry))
            message = f"{registry.beacon_url}/v1/beacon/{key}"
        else:
            project_id = disable_key(data, args.key)
            Registry.parse(data, source=str(args.registry))
            message = f"disabled a key of {project_id}"
    except (OSError, ValueError, RegistryError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.registry.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
