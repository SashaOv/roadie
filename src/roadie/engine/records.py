"""Per-build-file record of the last successful step fingerprints."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
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
    return load_record_at(record_path(build_dir, root))


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


_SWEEP_AFTER = timedelta(hours=24)
_ROOT_FILES = {"run.lock", "swept"}


# (spec) Artifacts
def sweep_stale_state(root: Path) -> None:
    """Drop unreferenced artifacts and dead record directories, at most daily."""
    state = root / "target" / "roadie"
    if not state.is_dir():
        return
    stamp = state / "swept"
    if not stamp.is_file():
        _write_stamp(stamp)
        return
    if not _sweep_due(stamp):
        return
    records = sorted(
        state.rglob("build.record.json"),
        key=lambda path: len(path.relative_to(state).parts),
        reverse=True,
    )
    for record in records:
        directory = record.parent
        if directory.is_dir():
            _sweep_directory(root, state, directory)
    _write_stamp(stamp)


def _sweep_due(stamp: Path) -> bool:
    try:
        parsed = datetime.fromisoformat(stamp.read_text().strip())
    except (OSError, UnicodeError, ValueError):
        return True
    if parsed.tzinfo is None:
        return True
    return datetime.now(UTC) - parsed.astimezone(UTC) > _SWEEP_AFTER


def _write_stamp(stamp: Path) -> None:
    stamp.parent.mkdir(parents=True, exist_ok=True)
    text = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    stamp.write_text(text + "\n")


def _sweep_directory(root: Path, state: Path, directory: Path) -> None:
    build = _build_file(root, state, directory)
    if build is not None and not build.is_file():
        _drop_record(state, directory)
        return
    names = set(load_record_at(directory / "build.record.json"))
    for path in list(directory.iterdir()):
        if (
            path.is_file()
            and path.name not in {"build.record.json", *_ROOT_FILES}
            and path.stem not in names
        ):
            path.unlink()


def _build_file(root: Path, state: Path, directory: Path) -> Path | None:
    relative = directory.relative_to(state)
    if relative.parts[:1] == ("_outside",):
        return None
    if relative == Path("."):
        return root / "build.py"
    return root / relative / "build.py"


def _drop_record(state: Path, directory: Path) -> None:
    if directory == state:
        for path in list(directory.iterdir()):
            if path.is_file() and path.name not in _ROOT_FILES:
                path.unlink()
        return
    for path in list(directory.iterdir()):
        if path.is_file():
            path.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
        parent = directory.parent
        while parent != state and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def load_record_at(path: Path) -> dict[str, Any]:
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


def _unreadable(path: Path, reason: str) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved not in _reported_unreadable:
        _reported_unreadable.add(resolved)
        print(f"ignoring unreadable record {path}: {reason}", flush=True)
    return {}
