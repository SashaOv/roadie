"""Steps whose value is the path of a file they build, and a directory walk."""

import hashlib
import os
from pathlib import Path

from roadie.engine.errors import RoadieError
from roadie.engine.root import from_stored, to_stored
from roadie.engine.step import Step


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def link_target(path: Path) -> str:
    """The target text of a symbolic link, which is not followed."""
    try:
        return os.readlink(path)
    except OSError as error:
        raise RoadieError(f"cannot read {path}: {error}") from error


def content_digest(path: Path) -> str:
    """SHA-256 of a file. An unreadable file fails with `cannot read <path>: <reason>`."""
    try:
        return sha256(path)
    except OSError as error:
        raise RoadieError(f"cannot read {path}: {error}") from error


def walk_files(directory: Path) -> list[Path]:
    """Every file and symbolic link under `directory`.

    Symbolic links are not followed, so a link to a directory is not
    descended. A directory reached twice counts once, and empty directories
    do not count. An unreadable directory fails with `cannot read <path>:
    <reason>`.
    """
    files: list[Path] = []
    seen: set[Path] = set()

    def walk(current: Path) -> None:
        try:
            resolved = current.resolve()
        except OSError as error:
            raise RoadieError(f"cannot read {current}: {error}") from error
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            children = list(current.iterdir())
        except OSError as error:
            raise RoadieError(f"cannot read {current}: {error}") from error
        for child in children:
            if child.is_symlink() or not child.is_dir():
                files.append(child)
            else:
                walk(child)

    walk(directory)
    return files


# (spec) Step kinds
class PathStep(Step[Path]):
    """A step whose value is a file path.

    The record keeps the path, relative to the project root when inside it,
    and the SHA-256 of the file; the step is reused only while the file still
    exists with that content.
    """

    def encode(self, value: Path) -> object:
        return {"path": to_stored(value), "sha256": sha256(value)}

    def matches(self, recorded: object) -> bool:
        if not isinstance(recorded, dict):
            return False
        stored, content = recorded.get("path"), recorded.get("sha256")
        if not isinstance(stored, str) or not isinstance(content, str):
            return False
        path = from_stored(stored)
        return path.is_file() and sha256(path) == content

    def decode(self, recorded: object) -> Path:
        assert isinstance(recorded, dict)
        return from_stored(str(recorded["path"]))
