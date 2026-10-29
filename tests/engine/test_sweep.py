from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support import execute, isolate_repo

import roadie.engine.run as run_mod
from roadie.build import Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _note(name: str = "note") -> Step[str]:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            path = run.artifact(self, ".txt")
            path.write_text(name)
            return name

    note = Note()
    note.name = note.variable = name
    note.build_variables = frozenset({name})
    return note


# (spec) Artifacts
def test_a_sweep_waits_a_day_and_then_drops_unreferenced_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    state = root / "target" / "roadie"
    state.mkdir(parents=True)
    (state / "gone.txt").write_text("old")
    (root / "build.py").write_text("# present\n")
    execute([_note()])
    stamp = state / "swept"
    assert stamp.is_file()
    assert (state / "gone.txt").is_file()
    assert (state / "note.txt").is_file()

    (state / "again.txt").write_text("new")
    execute([_note()])
    assert (state / "again.txt").is_file()

    stamp.write_text(_stamp(datetime.now(UTC) - timedelta(hours=24, minutes=1)))
    (state / "unnamed.txt").write_text("drop")
    execute([_note()])
    assert not (state / "gone.txt").exists()
    assert not (state / "again.txt").exists()
    assert not (state / "unnamed.txt").exists()
    assert (state / "note.txt").read_text() == "note"
    assert (state / "build.record.json").is_file()


# (spec) Artifacts
def test_sweep_drops_unnamed_artifacts_and_dead_build_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    (root / "build.py").write_text("# present\n")
    state = root / "target" / "roadie"
    execute([_note()])

    class Scratch(Step[str]):
        def build(self, run: Run) -> str:
            run.artifact(self, ".txt").write_text("temp")
            return "temp"

    unnamed = Scratch()
    unnamed.name = "scratch"
    state.mkdir(parents=True, exist_ok=True)
    (state / "swept").write_text(_stamp(datetime.now(UTC) - timedelta(days=2)))
    services = state / "services"
    services.mkdir()
    (services / "build.record.json").write_text('{"version":1,"entries":{}}')
    (services / "api.txt").write_text("stale")
    outside = state / "_outside" / "abc123"
    outside.mkdir(parents=True)
    (outside / "build.record.json").write_text(
        '{"entries":{"kept":{"fingerprint":"f","output":"o","value":"v"}},"version":1}'
    )
    (outside / "kept.txt").write_text("keep")
    (outside / "orphan.txt").write_text("drop")

    execute([_note(), unnamed])
    assert not (state / f"Scratch-{unnamed.serial}.txt").exists()
    assert not services.exists()
    assert (outside / "kept.txt").is_file()
    assert not (outside / "orphan.txt").exists()
    assert (outside / "build.record.json").is_file()


# (spec) Artifacts
def test_a_failed_run_does_not_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    (root / "build.py").write_text("# present\n")
    state = root / "target" / "roadie"
    state.mkdir(parents=True)
    (state / "gone.txt").write_text("old")
    written = _stamp(datetime.now(UTC) - timedelta(days=2))
    (state / "swept").write_text(written)

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = "boom"
    with pytest.raises(StepFailed, match="nope"):
        execute([boom])
    assert (state / "gone.txt").is_file()
    assert (state / "swept").read_text() == written


# (spec) Artifacts
# (spec) One run at a time
def test_the_sweep_runs_while_the_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    (root / "build.py").write_text("# present\n")
    seen: list[bool] = []
    real = run_mod.sweep_stale_state

    def wrapped(project: Path) -> None:
        seen.append((project / "target" / "roadie" / "run.lock").is_file())
        real(project)

    monkeypatch.setattr(run_mod, "sweep_stale_state", wrapped)
    execute([_note()])
    assert seen == [True]
