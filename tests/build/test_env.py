import os
from pathlib import Path

import pytest
from support import execute, isolate_repo

from roadie.build import Env, RoadieError, Step
from roadie.engine.run import Run


# (spec) Environment variables
def test_env_has_no_names() -> None:
    with pytest.raises(RoadieError, match="Env takes at least one variable name"):
        Env()


# (spec) Environment variables
def test_env_rejects_a_name_that_is_not_a_string() -> None:
    with pytest.raises(RoadieError, match=r"Env takes variable names: got int"):
        Env("MODE", 1)  # type: ignore[arg-type]


# (spec) Environment variables
def test_env_fingerprints_each_variable_and_unset_differs_from_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("TARGET", "local")
    first = Env("MODE", "TARGET").fingerprint(Path("."))
    monkeypatch.setenv("TARGET", "ci")
    assert Env("MODE", "TARGET").fingerprint(Path(".")) != first
    monkeypatch.setenv("MODE", "")
    empty = Env("MODE").fingerprint(Path("."))
    monkeypatch.delenv("MODE")
    assert "MODE" not in os.environ
    assert Env("MODE").fingerprint(Path(".")) != empty


# (spec) Environment variables
# (spec) Reusing a step
def test_a_step_reruns_when_its_env_input_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    monkeypatch.delenv("MODE", raising=False)
    built: list[str] = []

    class Bundle(Step[str]):
        def __init__(self) -> None:
            self.mode = Env("MODE")

        def build(self, run: Run) -> str:
            built.append(os.environ.get("MODE", "unset"))
            return "v"

    step = Bundle()
    step.name = step.variable = "bundle"
    step.build_variables = frozenset({"bundle"})
    execute([step])
    monkeypatch.setenv("MODE", "prod")
    execute([step])
    assert built == ["unset", "prod"]
