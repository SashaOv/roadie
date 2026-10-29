from pathlib import Path

import pytest
from support import Stopped, execute, isolate_repo

from roadie.build import Group, Service, Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run


def _note(label: str = "v") -> Step[str]:
    class Note(Step[str]):
        def __init__(self, text: str) -> None:
            self.text = text

        def build(self, run: Run) -> str:
            return self.text

    note = Note(label)
    note.name = note.variable = "note"
    note.build_variables = frozenset({"note"})
    return note


# (spec) Reusing a step
def test_a_built_step_is_shown_as_code_and_a_reused_step_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    note = _note()
    execute([note])
    assert "build note = Note(text='v')" in capsys.readouterr().out
    execute([note])
    output = capsys.readouterr().out
    assert "reused note" in output
    assert "build note" not in output


# (spec) Reusing a step
def test_an_unnamed_step_is_shown_without_a_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    note = _note()
    note.name = None
    execute([note])
    assert "build Note(text='v')" in capsys.readouterr().out


# (spec) Reusing a step
def test_a_group_prints_no_build_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    note = _note()
    group = Group(note)
    group.name = "all"
    execute([note, group])
    output = capsys.readouterr().out
    assert "build note = Note(text='v')" in output
    assert "build all" not in output
    assert "build Group" not in output


# (spec) Reusing a step
# (spec) Deferred steps
def test_a_deferred_step_is_shown_when_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)

    class Server(Service):
        def build(self, run: Run) -> Stopped:
            return Stopped()

    class After(Step[str]):
        def __init__(self, server: Server) -> None:
            self.server = server

        def build(self, run: Run) -> str:
            return "v"

    server = Server()
    server.name = "server"
    after = After(server)
    after.name = after.variable = "after"
    after.build_variables = frozenset({"after"})
    execute([server, after])
    output = capsys.readouterr().out
    assert "build server = Server()" in output
    assert output.index("build server = Server()") < output.index("build after = After(")

    execute([server, after])
    output = capsys.readouterr().out
    assert "reused after" in output
    assert "build server" not in output
    assert "not started server" in output


# (spec) Reusing a step
def test_a_failed_step_was_shown_before_it_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = "boom"
    with pytest.raises(StepFailed, match="nope"):
        execute([boom])
    assert "build boom = Boom()" in capsys.readouterr().out
