import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema_rs

_VALIDATOR = jsonschema_rs.validator_for(
    json.loads(files("beacon").joinpath("registry.schema.json").read_text())
)

ANY_PACKAGE = "*"


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    packages: frozenset[str] | None

    def accepts_package(self, package: str) -> bool:
        return self.packages is None or package in self.packages


@dataclass(frozen=True)
class Registry:
    beacon_url: str
    projects: tuple[Project, ...]
    _keys: dict[str, tuple[Project, bool]]

    def project_for_key(self, key: str) -> Project | None:
        found = self._keys.get(key)
        return found[0] if found and found[1] else None

    @staticmethod
    def load(path: Path) -> "Registry":
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise RegistryError(f"{path}: {e}") from e
        return Registry.parse(data, source=str(path))

    @staticmethod
    def parse(data: Any, source: str = "registry") -> "Registry":
        errors = [
            f"/{'/'.join(map(str, e.instance_path))}: {e.message}"
            for e in _VALIDATOR.iter_errors(data)
        ]
        if errors:
            raise RegistryError(f"{source}: " + "; ".join(errors))
        projects: list[Project] = []
        keys: dict[str, tuple[Project, bool]] = {}
        for entry in data["projects"]:
            if any(p.id == entry["id"] for p in projects):
                raise RegistryError(f"{source}: project {entry['id']} appears twice")
            project = Project(
                id=entry["id"],
                name=entry["name"],
                packages=None if entry["packages"] == ANY_PACKAGE else frozenset(entry["packages"]),
            )
            projects.append(project)
            for key in entry["keys"]:
                if key["key"] in keys:
                    raise RegistryError(f"{source}: key {key['key'][:4]}… appears twice")
                keys[key["key"]] = (project, key["enabled"])
        return Registry(data["beacon_url"], tuple(projects), keys)
