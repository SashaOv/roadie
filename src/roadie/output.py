"""Publish file artifacts and protect tracked outputs in locked runs."""

import difflib
import subprocess
from pathlib import Path

from roadie.errors import RoadieError
from roadie.step import File


def publish(step: File, artifact: Path, *, locked: bool) -> Path:
    if step.output is None:
        return artifact
    destination = step.resolve(step.output)
    content = artifact.read_bytes()
    previous = destination.read_bytes() if destination.exists() else None
    if previous == content:
        return destination
    if locked and _tracked(destination):
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
            f"Cannot check whether {path} is tracked: {completed.stderr.strip()}"
        )
    return bool(completed.stdout)
