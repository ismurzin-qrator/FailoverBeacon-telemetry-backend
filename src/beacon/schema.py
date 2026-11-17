import hashlib
from pathlib import Path

COPY = Path(__file__).parent / "telemetry-schema"


def verify_copy() -> list[str]:
    listed = {}
    for line in (COPY / "SHA256SUMS").read_text().splitlines():
        digest, path = line.split("  ", 1)
        listed[path] = digest
    problems = []
    for path, digest in sorted(listed.items()):
        target = COPY / path
        if not target.is_file():
            problems.append(f"{path}: missing")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            problems.append(f"{path}: edited")
    present = {
        p.relative_to(COPY).as_posix()
        for p in COPY.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
    }
    problems += [f"{path}: not in SHA256SUMS" for path in sorted(present - set(listed))]
    return problems
