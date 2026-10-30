import importlib.metadata
import platform
from pathlib import Path

import pytest

from roadie.build import PythonEnv, python_env
from roadie.engine.inputs import digest


def _installed() -> list[tuple[str, str]]:
    found = [
        (dist.name, dist.version)
        for dist in importlib.metadata.distributions()
        if dist.name
    ]
    found.sort()
    return found


def _fingerprint(python: str, distributions: list[tuple[str, str]]) -> str:
    return digest({"python": python, "distributions": distributions})


# (spec) Python environment
def test_python_env_fingerprints_the_interpreter_and_installed_distributions() -> None:
    expected = _fingerprint(platform.python_version(), _installed())
    env = PythonEnv()
    assert env.fingerprint(Path(".")) == expected
    assert env.fingerprint(Path("/tmp")) == expected
    assert "mypy" in {name for name, _version in _installed()}


class _Distribution:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version


# (spec) Python environment
def test_python_env_changes_when_mypy_or_the_interpreter_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = PythonEnv()
    baseline = env.fingerprint(Path("."))
    real = importlib.metadata.distributions

    def upgraded() -> list[object]:
        changed: list[object] = []
        for dist in real():
            if dist.name == "mypy":
                changed.append(_Distribution(dist.name, f"{dist.version}.next"))
            else:
                changed.append(dist)
        return changed

    monkeypatch.setattr(python_env.importlib.metadata, "distributions", upgraded)
    assert env.fingerprint(Path(".")) != baseline

    monkeypatch.undo()
    monkeypatch.setattr(python_env.platform, "python_version", lambda: "0.0.0")
    assert env.fingerprint(Path(".")) != baseline
