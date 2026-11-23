"""Session fixtures for the Roadie suite."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from roadie.system import tracing_error


def _already_traced() -> bool:
    """True when an outer strace already traces this process.

    A traced process cannot be traced again, so a tracing test inside
    an enforcement run cannot start its own strace.
    """
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in status.splitlines():
        if line.startswith("TracerPid:"):
            return line.split(":", 1)[1].strip() not in ("", "0")
    return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    reason = tracing_error()
    if reason is None and _already_traced():
        reason = "already traced by an outer strace"
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("tracing_only") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def git_without_machine_config(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Ignore system and global git config, as CI does.

    A test must not pass only because this machine sets
    `init.defaultBranch=main`.
    """
    config = tmp_path_factory.mktemp("git") / "config"
    config.write_text("")
    previous = {name: os.environ.get(name) for name in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")}
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["GIT_CONFIG_GLOBAL"] = str(config)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
