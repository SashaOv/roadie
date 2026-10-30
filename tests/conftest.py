"""Session fixtures for the Roadie suite."""

import os
from collections.abc import Iterator

import pytest


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
