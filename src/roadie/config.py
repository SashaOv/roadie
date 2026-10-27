"""Scope build-file settings to the file currently being loaded."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from wright.errors import WrightError

_file_directory: ContextVar[Path | None] = ContextVar(
    "wright_file_directory", default=None
)
_base_directory: ContextVar[Path | None] = ContextVar(
    "wright_base_directory", default=None
)


def base_directory() -> Path:
    return _base_directory.get() or Path.cwd()


def loading_directory() -> Path | None:
    """Directory of the build file currently being loaded, if any."""
    return _file_directory.get()


def config(**settings: object) -> None:
    unknown = settings.keys() - {"base_dir"}
    if unknown:
        raise WrightError(
            f"unknown config settings: {', '.join(sorted(unknown))}; known settings: base_dir"
        )
    directory = _file_directory.get()
    if directory is None:
        raise WrightError("config() works only in a build.py that Wright loads")
    if "base_dir" in settings:
        value = settings["base_dir"]
        if not isinstance(value, (str, os.PathLike)):
            raise WrightError("base_dir must be a directory path")
        _base_directory.set((directory / value).resolve())


@contextmanager
def file_settings(directory: Path, override: Path | None) -> Generator[None]:
    file_token = _file_directory.set(directory)
    base_token = _base_directory.set(override or directory)
    try:
        yield
    finally:
        _base_directory.reset(base_token)
        _file_directory.reset(file_token)
