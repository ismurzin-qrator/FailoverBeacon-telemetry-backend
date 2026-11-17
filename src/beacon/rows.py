from typing import Any

from beacon.mapping import CONVERTED, expected_columns

INT_MIN = -(2**63)
UINT_MAX = 2**64 - 1


class UnstorableValue(Exception):
    pass


def _schema_columns(schema_name: str, table: str) -> dict[str, str]:
    columns = expected_columns(schema_name) | CONVERTED.get(table, {})
    return {name: _preferred(types) for name, types in columns.items()}


def _preferred(types: set[str]) -> str:
    return next((t for t in types if "LowCardinality" in t), next(iter(types)))


EVENT_COLUMNS = _schema_columns("event", "events")
METADATA_COLUMNS = _schema_columns("upload-metadata", "uploads")


def flatten(obj: dict[str, Any], columns: dict[str, str]) -> list[Any]:
    values: dict[str, Any] = {}
    _walk(obj, "", columns, values)
    row = []
    for name, kind in columns.items():
        if name not in values:
            row.append(_empty(kind))
        elif kind == "Float64":
            row.append(float(values[name]))
        else:
            row.append(values[name])
    return row


def _walk(obj: dict[str, Any], prefix: str, columns: dict[str, str], out: dict[str, Any]) -> None:
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            _walk(value, f"{name}_", columns, out)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for column, kind in columns.items():
                if column.startswith(f"{name}."):
                    field = column[len(name) + 1 :]
                    element = kind.removeprefix("Array(")[:-1]
                    out[column] = [_value(item.get(field, _empty(element))) for item in value]
        else:
            out[name] = _value(value)


def _value(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and not INT_MIN <= value <= UINT_MAX:
        raise UnstorableValue(f"{value} exceeds 64 bits")
    return value


def _empty(kind: str) -> Any:
    if kind.startswith("Nullable("):
        return None
    if kind.startswith("Array("):
        return []
    if kind.endswith("String)") or kind == "String":
        return ""
    raise KeyError(f"a required {kind} column has no value")
