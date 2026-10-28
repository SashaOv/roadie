"""Scope build-file settings to the file currently being loaded."""

import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from roadie.errors import RoadieError

_file_directory: ContextVar[Path | None] = ContextVar(
    "roadie_file_directory", default=None
)
_base_directory: ContextVar[Path | None] = ContextVar(
    "roadie_base_directory", default=None
)
_env_files: ContextVar[tuple[Path, ...]] = ContextVar("roadie_env_files", default=())


def base_directory() -> Path:
    return _base_directory.get() or Path.cwd()


def loading_directory() -> Path | None:
    """Directory of the build file currently being loaded, if any."""
    return _file_directory.get()


def configured_env() -> tuple[Path, ...]:
    """Environment files from ``config(env=...)`` for the build file being loaded."""
    return _env_files.get()


# (spec) Settings
def config(**settings: object) -> None:
    unknown = settings.keys() - {"base_dir", "env"}
    if unknown:
        raise RoadieError(
            f"unknown config settings: {', '.join(sorted(unknown))}; known settings: base_dir, env"
        )
    directory = _file_directory.get()
    if directory is None:
        raise RoadieError("config() works only in a build.py that Roadie loads")
    if "base_dir" in settings:
        value = settings["base_dir"]
        if not isinstance(value, (str, os.PathLike)):
            raise RoadieError("base_dir must be a directory path")
        _base_directory.set((directory / os.fspath(value)).resolve())
    if "env" in settings:
        _env_files.set(_env_paths(directory, settings["env"]))


def _env_paths(directory: Path, value: object) -> tuple[Path, ...]:
    if isinstance(value, (str, bytes, Path)) or not isinstance(value, Sequence):
        raise RoadieError("env must be a list of environment files")
    paths: list[Path] = []
    for item in value:
        if not isinstance(item, (str, os.PathLike)):
            raise RoadieError("env must be a list of environment files")
        paths.append((directory / os.fspath(item)).resolve())
    return tuple(paths)


@contextmanager
def file_settings(directory: Path, override: Path | None) -> Generator[None]:
    file_token = _file_directory.set(directory)
    base_token = _base_directory.set(override or directory)
    env_token = _env_files.set(())
    try:
        yield
    finally:
        _env_files.reset(env_token)
        _base_directory.reset(base_token)
        _file_directory.reset(file_token)
