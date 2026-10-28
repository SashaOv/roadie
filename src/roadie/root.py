"""The project root for one load and the runs that follow it."""

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from roadie.errors import RoadieError

_project_root: ContextVar[Path | None] = ContextVar("roadie_project_root", default=None)


def find_project_root(directory: Path) -> Path:
    """Git top level of `directory`, or `directory` when it is not a work tree."""
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return directory.resolve()
    return Path(completed.stdout.strip()).resolve()


def project_root() -> Path:
    root = _project_root.get()
    if root is None:
        raise RoadieError("no project root: run steps through roadie's load and execute")
    return root


@contextmanager
def use_project_root(path: Path) -> Iterator[None]:
    token = _project_root.set(path)
    try:
        yield
    finally:
        _project_root.reset(token)
