"""Per-build-file record of the last successful step fingerprints."""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

RECORD_VERSION = 1
_reported_unreadable: set[Path] = set()


class RecordEntry(BaseModel):
    fingerprint: str
    output: str
    value: Any


class RecordFile(BaseModel):
    version: int
    entries: dict[str, RecordEntry]


def artifact_dir(build_dir: Path | None, root: Path) -> Path:
    if build_dir is None:
        return Path()
    try:
        relative = build_dir.resolve().relative_to(root.resolve())
    except ValueError:
        digest = hashlib.sha256(str(build_dir.resolve()).encode()).hexdigest()[:12]
        return Path("_outside") / digest
    if relative == Path("."):
        return Path()
    return relative


# (spec) Records
def record_path(build_dir: Path | None, root: Path) -> Path:
    return root / "target" / "roadie" / artifact_dir(build_dir, root) / "build.record.json"


# (spec) Records
def load_records(build_dir: Path | None, root: Path) -> dict[str, Any]:
    path = record_path(build_dir, root)
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return _unreadable(path, str(error))
    try:
        loaded = RecordFile.model_validate(parsed)
    except ValidationError as error:
        return _unreadable(path, "; ".join(str(item["msg"]) for item in error.errors()))
    if loaded.version != RECORD_VERSION:
        return {}
    return {name: entry.model_dump() for name, entry in loaded.entries.items()}


# (spec) Records
def save_records(build_dir: Path | None, records: dict[str, Any], root: Path) -> None:
    path = record_path(build_dir, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = RecordFile(
        version=RECORD_VERSION,
        entries={name: RecordEntry.model_validate(entry) for name, entry in records.items()},
    )
    temporary = path.parent / f"{path.name}.tmp"
    temporary.write_text(
        json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    temporary.replace(path)


def _unreadable(path: Path, reason: str) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved not in _reported_unreadable:
        _reported_unreadable.add(resolved)
        print(f"ignoring unreadable record {path}: {reason}", flush=True)
    return {}
