"""Steps whose value is the path of a file they build."""

import hashlib
from pathlib import Path

from roadie.engine.root import from_stored, to_stored
from roadie.engine.step import Step


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
