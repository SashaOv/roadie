"""Scope build-file settings to the file currently being loaded."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from roadie.engine.errors import RoadieError
from roadie.engine.many import one_or_many

_file_directory: ContextVar[Path | None] = ContextVar(
    "roadie_file_directory", default=None
)
_base_directory: ContextVar[Path | None] = ContextVar(
    "roadie_base_directory", default=None
)
_build_file: ContextVar[Path | None] = ContextVar("roadie_build_file", default=None)
_root_file: ContextVar[Path | None] = ContextVar("roadie_root_file", default=None)
_root_env: ContextVar[tuple[Path, ...]] = ContextVar("roadie_root_env", default=())


def base_directory() -> Path:
    return _base_directory.get() or Path.cwd()


def loading_directory() -> Path | None:
    """Directory of the build file currently being loaded, if any."""
    return _file_directory.get()


def root_env() -> tuple[Path, ...]:
    """Environment files the root build file named with ``config(env=...)``."""
    return _root_env.get()


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
        paths: tuple[str | os.PathLike[str], ...] = one_or_many(
            settings["env"],
            (str, os.PathLike),
            owner="config",
            parameter="env",
            takes="paths",
        )
        build_file = _build_file.get()
        if build_file is None or build_file != _root_file.get():
            raise RoadieError(
                f"config env works only in the root build file: {build_file}"
            )
        _root_env.set(tuple((directory / os.fspath(item)).resolve() for item in paths))


@contextmanager
def file_settings(
    directory: Path,
    override: Path | None,
    *,
    build_file: Path,
    root_file: Path,
) -> Generator[None]:
    file_token = _file_directory.set(directory)
    base_token = _base_directory.set(override or directory)
    build_token = _build_file.set(build_file)
    root_token = _root_file.set(root_file)
    env_token = _root_env.set(()) if build_file == root_file else None
    try:
        yield
    finally:
        if env_token is not None:
            _root_env.reset(env_token)
        _root_file.reset(root_token)
        _build_file.reset(build_token)
        _base_directory.reset(base_token)
        _file_directory.reset(file_token)
