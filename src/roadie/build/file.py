"""File steps, and publishing generated files into the source tree."""

import difflib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from roadie.build.path import PathStep, sha256
from roadie.engine.errors import RoadieError

if TYPE_CHECKING:
    from roadie.engine.run import Run


# (spec) Step kinds
# (spec) Generated files
@dataclass(eq=False, kw_only=True)
class File(PathStep):
    """A step that builds a file; its value is the file's path.

    With `output`, the built file is published as a generated file. The output
    fingerprint is the SHA-256 of the file, so a rebuilt file with unchanged
    content leaves its consumers reusable.
    """

    output: str | Path | None = None

    def publish(self, run: "Run", value: Path) -> Path:
        if self.output is None:
            return value
        destination = self.resolve(self.output)
        content = value.read_bytes()
        previous = destination.read_bytes() if destination.exists() else None
        if previous == content:
            return destination
        tracked = _tracked(destination)
        if (
            previous is not None
            and tracked
            and _same_as_git_stores(destination, value)
        ):
            return destination
        if run.locked and tracked:
            difference = difflib.diff_bytes(
                difflib.unified_diff,
                (previous or b"").splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=str(destination).encode(),
                tofile=f"{destination} (generated)".encode(),
            )
            print(b"".join(difference).decode(errors="replace"), flush=True)
            raise RoadieError(f"generated file is stale: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        print(f"updated {destination}", flush=True)
        return destination

    def output_fingerprint(self, fingerprint: str, value: Path) -> str:
        return sha256(value)


def _repository(path: Path) -> Path | None:
    return next(
        (parent for parent in path.parents if (parent / ".git").exists()), None
    )


def _tracked(path: Path) -> bool:
    repository = _repository(path)
    if repository is None:
        return False
    completed = subprocess.run(
        (
            "git",
            "--literal-pathspecs",
            "-C",
            str(repository),
            "ls-files",
            "-z",
            "--",
            path.relative_to(repository).as_posix(),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RoadieError(
            f"cannot check whether {path} is tracked: {completed.stderr.strip()}"
        )
    return bool(completed.stdout)


def _same_as_git_stores(destination: Path, built: Path) -> bool:
    """True when git would store the same blob for both under destination's path."""
    repository = _repository(destination)
    if repository is None:
        return False
    relative = destination.relative_to(repository).as_posix()
    return _git_hash(repository, destination, relative) == _git_hash(
        repository, built, relative
    )


def _git_hash(repository: Path, file: Path, path: str) -> str | None:
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "hash-object",
            f"--path={path}",
            "--",
            str(file),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
