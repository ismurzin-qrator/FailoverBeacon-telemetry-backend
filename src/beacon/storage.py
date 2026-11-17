import hashlib
from importlib.resources import files

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from beacon.config import DATABASE, Settings
from beacon.registry import Registry


class MigrationError(Exception):
    pass


def connect(settings: Settings) -> Client:
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )


def migrate(client: Client) -> list[str]:
    client.command(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    client.database = DATABASE
    client.command(
        "CREATE TABLE IF NOT EXISTS schema_migrations"
        " (name String, sha256 String, applied_at DateTime('UTC') DEFAULT now())"
        " ENGINE = MergeTree ORDER BY name"
    )
    applied: dict[str, str] = {
        name: digest
        for name, digest in client.query("SELECT name, sha256 FROM schema_migrations").result_rows
    }
    available = {
        f.name: f.read_text()
        for f in files("beacon").joinpath("migrations").iterdir()
        if f.name.endswith(".sql")
    }
    if missing := sorted(set(applied) - set(available)):
        raise MigrationError(f"applied migrations are missing: {', '.join(missing)}")
    done = []
    for name in sorted(available):
        digest = hashlib.sha256(available[name].encode()).hexdigest()
        if name in applied:
            if applied[name] != digest:
                raise MigrationError(f"{name} changed after it was applied")
            continue
        client.command(available[name])
        client.insert("schema_migrations", [[name, digest]], column_names=["name", "sha256"])
        done.append(name)
    return done


def copy_registry(client: Client, registry: Registry) -> None:
    client.database = DATABASE
    client.command("TRUNCATE TABLE projects")
    client.insert(
        "projects",
        [[p.id, p.name, p.packages is None, sorted(p.packages or ())] for p in registry.projects],
        column_names=["project_id", "name", "any_package", "packages"],
    )
