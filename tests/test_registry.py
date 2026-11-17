import copy
import json
from pathlib import Path

import pytest

from beacon import cli
from beacon.registry import Registry, RegistryError
from tests.helpers import COMMUNITY_KEY, SHOP_KEY, registry

EXAMPLE = json.loads((Path(__file__).parent.parent / "registry.example.json").read_text())


def test_a_key_names_its_project():
    project = registry().project_for_key(SHOP_KEY)
    assert project is not None and project.id == "example-shop"
    assert project.accepts_package("com.example.shop")
    assert not project.accepts_package("com.other.app")


def test_unknown_and_disabled_keys_name_no_project():
    data = copy.deepcopy(EXAMPLE)
    data["projects"][0]["keys"][0]["enabled"] = False
    parsed = Registry.parse(data)
    assert parsed.project_for_key(SHOP_KEY) is None
    assert parsed.project_for_key("z" * 26) is None


def test_a_star_accepts_any_package():
    project = registry().project_for_key(COMMUNITY_KEY)
    assert project is not None and project.accepts_package("org.any.app")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(beacon_url="http://beacon.example.com"), "beacon_url"),
        (
            lambda d: d["projects"][0]["keys"].append(dict(d["projects"][1]["keys"][0])),
            "appears twice",
        ),
        (lambda d: d["projects"].append(dict(d["projects"][0])), "appears twice"),
        (lambda d: d["projects"][0].update(packages=["shop"]), "packages"),
        (lambda d: d["projects"][0]["keys"][0].update(key="SHORT"), "key"),
    ],
)
def test_an_invalid_registry_is_refused(change, message):
    data = copy.deepcopy(EXAMPLE)
    change(data)
    with pytest.raises(RegistryError, match=message):
        Registry.parse(data)


def test_keys_add_creates_a_project_and_prints_its_url(tmp_path, capsys):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(EXAMPLE))

    assert cli.main(["keys", "add", "--registry", str(path), "--project", "acme", "--name", "Acme",
                     "--package", "com.acme.app"]) == 0  # fmt: skip

    url = capsys.readouterr().out.strip()
    key = url.rsplit("/", 1)[1]
    assert url == f"https://beacon.example.com/v1/beacon/{key}"
    project = Registry.load(path).project_for_key(key)
    assert project is not None and project.id == "acme" and project.packages == {"com.acme.app"}


def test_keys_add_adds_a_second_key_to_a_project(tmp_path, capsys):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(EXAMPLE))

    assert cli.main(["keys", "add", "--registry", str(path), "--project", "example-shop"]) == 0

    key = capsys.readouterr().out.strip().rsplit("/", 1)[1]
    parsed = Registry.load(path)
    assert parsed.project_for_key(key) == parsed.project_for_key(SHOP_KEY)


@pytest.mark.parametrize(
    "args",
    [
        ["--project", "acme", "--name", "Acme"],
        ["--project", "acme", "--name", "Acme", "--package", "shop"],
        ["--project", "example-shop", "--name", "Renamed"],
    ],
)
def test_keys_add_leaves_the_file_unchanged_on_an_error(tmp_path, args):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(EXAMPLE))

    assert cli.main(["keys", "add", "--registry", str(path), *args]) == 1

    assert json.loads(path.read_text()) == EXAMPLE


def test_keys_disable_turns_a_key_off(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(EXAMPLE))

    assert cli.main(["keys", "disable", "--registry", str(path), SHOP_KEY]) == 0
    assert cli.main(["keys", "disable", "--registry", str(path), "z" * 26]) == 1

    assert Registry.load(path).project_for_key(SHOP_KEY) is None


def test_new_keys_are_128_random_bits_in_base32():
    keys = {cli.new_key() for _ in range(100)}
    assert len(keys) == 100
    assert all(len(k) == 26 and set(k) <= set("abcdefghijklmnopqrstuvwxyz234567") for k in keys)
