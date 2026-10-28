"""Inputs a step fingerprints without building them."""

import hashlib
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from forge.environment import repository_root


def digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class Input(ABC):
    @abstractmethod
    def fingerprint(self, base_dir: Path) -> str:
        """Hash of this input as seen from a step's base directory."""


def _entry_key(path: Path, root: Path | None) -> str:
    absolute = path if path.is_absolute() else path.absolute()
    if root is None:
        return str(absolute)
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        return str(absolute)
    return relative.as_posix() if relative.parts else "."


def _file_mark(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(base_dir: Path, raw: str | Path) -> Path:
    path = Path(raw)
    joined = path if path.is_absolute() else base_dir / path
    return Path(os.path.abspath(joined))


def _files_under(directory: Path) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()

    def walk(current: Path) -> None:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            children = list(current.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir():
                walk(child)
            else:
                files.append(child)

    walk(directory)
    return files


class Files(Input):
    def __init__(self, *paths: str | Path) -> None:
        self.paths = paths

    def __repr__(self) -> str:
        shown = ", ".join(
            repr(path if isinstance(path, str) else os.fspath(path)) for path in self.paths
        )
        return f"Files({shown})"

    def fingerprint(self, base_dir: Path) -> str:
        try:
            root = repository_root().resolve()
        except RuntimeError:
            root = None
        entries: dict[str, str] = {}
        for raw in self.paths:
            path = _resolve(base_dir, raw)
            if path.is_dir():
                for child in _files_under(path):
                    entries[_entry_key(child, root)] = _file_mark(child)
            else:
                entries[_entry_key(path, root)] = _file_mark(path)
        return digest(entries)
