"""Compare a generated file with the blob git would store."""

import subprocess
from pathlib import Path

from roadie.engine.errors import RoadieError


def repository(path: Path) -> Path | None:
    return next((parent for parent in path.parents if (parent / ".git").exists()), None)


def tracked(path: Path) -> bool:
    repository_path = repository(path)
    if repository_path is None:
        return False
    completed = subprocess.run(
        (
            "git",
            "--literal-pathspecs",
            "-C",
            str(repository_path),
            "ls-files",
            "-z",
            "--",
            path.relative_to(repository_path).as_posix(),
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


def tracked_files(directory: Path) -> list[Path]:
    """Tracked files that exist under `directory`."""
    repository_path = repository(directory)
    if repository_path is None or not directory.is_dir():
        return []
    completed = subprocess.run(
        (
            "git",
            "--literal-pathspecs",
            "-C",
            str(repository_path),
            "ls-files",
            "-z",
            "--",
            directory.relative_to(repository_path).as_posix(),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RoadieError(
            f"cannot check whether {directory} is tracked: {completed.stderr.strip()}"
        )
    names = [item for item in completed.stdout.split("\0") if item]
    return [repository_path / name for name in names if (repository_path / name).exists()]


def same_as_git_stores(destination: Path, built: Path) -> bool:
    """True when git would store the same blob for both under destination's path."""
    repository_path = repository(destination)
    if repository_path is None:
        return False
    relative = destination.relative_to(repository_path).as_posix()
    return _git_hash(repository_path, destination, relative) == _git_hash(
        repository_path, built, relative
    )


def _git_hash(repository_path: Path, file: Path, path: str) -> str | None:
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(repository_path),
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
