"""Compare a generated file with the blob git would store."""

from pathlib import Path

from roadie.engine.errors import RoadieError
from roadie.system import run_query


def repository(path: Path) -> Path | None:
    return next((parent for parent in path.parents if (parent / ".git").exists()), None)


def tracked(path: Path) -> bool:
    repository_path = repository(path)
    if repository_path is None:
        return False
    return bool(_listed(repository_path, path))


def tracked_files(directory: Path) -> list[Path]:
    """Tracked files that exist under `directory`."""
    repository_path = repository(directory)
    if repository_path is None or not directory.is_dir():
        return []
    return [path for path in _listed(repository_path, directory) if path.exists()]


def _listed(repository_path: Path, path: Path) -> list[Path]:
    try:
        output = run_query(
            repository_path,
            "git",
            "--literal-pathspecs",
            "-C",
            str(repository_path),
            "ls-files",
            "-z",
            "--",
            path.relative_to(repository_path).as_posix(),
        )
    except OSError as error:
        raise RoadieError(
            f"cannot check whether {path} is tracked: {error}"
        ) from error
    return [repository_path / name for name in output.split("\0") if name]


def same_as_git_stores(destination: Path, built: Path) -> bool:
    """True when git would store the same blob for both under destination's path."""
    repository_path = repository(destination)
    if repository_path is None:
        return False
    relative = destination.relative_to(repository_path).as_posix()
    return _git_hash(repository_path, destination, relative) == _git_hash(
        repository_path, built, relative
    )


def _git_hash(repository_path: Path, file: Path, path: str) -> str:
    try:
        output = run_query(
            repository_path,
            "git",
            "-C",
            str(repository_path),
            "hash-object",
            f"--path={path}",
            "--",
            str(file),
        )
    except OSError as error:
        raise RoadieError(f"cannot compare {file} with git: {error}") from error
    hashed = output.strip()
    if not hashed:
        raise RoadieError(f"cannot compare {file} with git: git printed no hash")
    return hashed
