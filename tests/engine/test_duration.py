from pathlib import Path

import pytest
from support import FakeDeployment, execute, isolate_repo

import roadie.engine.run as run_module
from roadie.build import Group, Service, Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run


def _clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    now = {"t": 0.0}
    monkeypatch.setattr(run_module.time, "monotonic", lambda: now["t"])
    return now


def _named(step: Step[object], name: str) -> Step[object]:
    step.name = step.variable = name
    step.build_variables = frozenset({name})
    return step


# (spec) Reusing a step
@pytest.mark.parametrize(
    ("elapsed", "text"),
    [(3.9, "0:03"), (12 * 60 + 45.9, "12:45"), (75 * 60, "75:00")],
)
def test_a_built_step_reports_rounded_down_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], elapsed: float, text: str
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    now = _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            now["t"] = elapsed
            return "v"

    execute([_named(Note(), "note")])
    output = capsys.readouterr().out
    assert f"built note in {text}" in output
    assert f"finished in {text}" in output.splitlines()[-1]


# (spec) Reusing a step
def test_an_unnamed_step_reports_its_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            return "v"

    execute([Note()])
    assert "built Note in 0:00" in capsys.readouterr().out.splitlines()


# (spec) Reusing a step
def test_a_reused_step_and_a_group_print_no_built_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            return "v"

    note = _named(Note(), "note")
    group = Group(note)
    group.name = "all"
    execute([note, group])
    capsys.readouterr()
    execute([note, group])
    lines = capsys.readouterr().out.splitlines()
    assert "reused note" in lines
    assert "reused all" in lines
    assert not any(line.startswith("built ") for line in lines)
    assert lines[-1].startswith("finished in ")


# (spec) Reusing a step
# (spec) Failures
def test_a_failed_step_prints_no_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    _clock(monkeypatch)

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    with pytest.raises(StepFailed, match="boom failed: nope"):
        execute([_named(Boom(), "boom")])
    output = capsys.readouterr().out
    assert "built " not in output
    assert "finished in " not in output


# (spec) Reusing a step
def test_a_step_that_fails_while_reporting_prints_no_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    now = _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            now["t"] = 4
            return "v"

        def report(self, value: str) -> None:
            raise RuntimeError("late")

    with pytest.raises(StepFailed, match="note failed: late"):
        execute([_named(Note(), "note")])
    output = capsys.readouterr().out
    assert "built " not in output
    assert "finished in " not in output


# (spec) Reusing a step
def test_a_dry_run_prints_no_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            return "v"

    execute([_named(Note(), "note")], dry=True)
    output = capsys.readouterr().out
    assert "built " not in output
    assert "finished in " not in output
    assert "# run" in output


# (spec) Reusing a step
# (spec) Deferred steps
def test_a_deferred_step_times_its_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    now = _clock(monkeypatch)

    class Server(Service):
        def build(self, run: Run) -> FakeDeployment:
            now["t"] = 4
            return FakeDeployment()

    class After(Step[str]):
        def __init__(self, server: Server) -> None:
            self.server = server

        def build(self, run: Run) -> str:
            now["t"] = 9
            return "v"

    server = Server()
    server.name = "server"
    after = _named(After(server), "after")
    execute([server, after])
    lines = capsys.readouterr().out.splitlines()
    assert "built server in 0:04" in lines
    assert "built after in 0:05" in lines
    assert lines[-1] == "finished in 0:09"


# (spec) Reusing a step
# (spec) Requested result
def test_finished_is_printed_before_the_result_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    now = _clock(monkeypatch)

    class Up(Service):
        def build(self, run: Run) -> FakeDeployment:
            now["t"] = 2
            return FakeDeployment()

    execute([_named(Up(), "server")])
    output = capsys.readouterr().out
    assert output.index("finished in 0:02") < output.index("server is up:")


# (spec) Reusing a step
# (spec) Parallel steps
def test_a_parallel_built_line_is_prefixed_and_the_total_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_repo(monkeypatch, tmp_path)
    now = _clock(monkeypatch)

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            now["t"] = 1
            return "v"

    execute([_named(Note(), "note")], jobs=2)
    lines = capsys.readouterr().out.splitlines()
    assert "[note] built note in 0:01" in lines
    assert "finished in 0:01" in lines
    assert not any(line.startswith("[") and "finished in " in line for line in lines)
