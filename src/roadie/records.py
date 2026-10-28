"""Per-build-file record of the last successful step fingerprints."""

import hashlib
import json
from pathlib import Path
from typing import Any


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


def record_path(build_dir: Path | None, root: Path) -> Path:
    return root / "target" / "roadie" / artifact_dir(build_dir, root) / "build.record.json"


def load_records(build_dir: Path | None, root: Path) -> dict[str, Any]:
    path = record_path(build_dir, root)
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text())
    if not isinstance(loaded, dict):
        return {}
    return loaded


def save_records(build_dir: Path | None, records: dict[str, Any], root: Path) -> None:
    path = record_path(build_dir, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f"{path.name}.tmp"
    temporary.write_text(json.dumps(records, sort_keys=True, separators=(",", ":")))
    temporary.replace(path)
