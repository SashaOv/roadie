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
        if run.locked and _tracked(destination):
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


def _tracked(path: Path) -> bool:
    repository = next(
        (parent for parent in path.parents if (parent / ".git").exists()), None
    )
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
