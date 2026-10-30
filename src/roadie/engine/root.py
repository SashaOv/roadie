"""The project root for one load and the runs that follow it."""

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from roadie.engine.errors import RoadieError

_project_root: ContextVar[Path | None] = ContextVar("roadie_project_root", default=None)


# (spec) Project root
def find_project_root(directory: Path) -> Path:
    """Git top level of `directory`, or `directory` when it is not a work tree."""
    if not directory.exists():
        raise RoadieError(f"directory does not exist: {directory}")
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RoadieError(f"cannot run git to find the project root: {error}") from error
    if completed.returncode != 0:
        return directory.resolve()
    return Path(completed.stdout.strip()).resolve()


def project_root() -> Path:
    root = _project_root.get()
    if root is None:
        raise RoadieError("no project root: run steps through roadie's load and execute")
    return root


def to_stored(path: Path) -> str:
    """`path` relative to the project root when inside it, else absolute."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def from_stored(stored: str) -> Path:
    path = Path(stored)
    return path if path.is_absolute() else project_root() / path


@contextmanager
def use_project_root(path: Path) -> Iterator[None]:
    token = _project_root.set(path)
    try:
        yield
    finally:
        _project_root.reset(token)
