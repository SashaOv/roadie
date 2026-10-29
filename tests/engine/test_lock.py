import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support import Stopped, execute, isolate_repo, write_build

from roadie import cli
from roadie.build import Service, Step
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.run import Run


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plant(path: Path, pid: int, started: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "started": started}, sort_keys=True))


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    try:
        os.kill(process.pid, 0)
    except ProcessLookupError:
        return process.pid
    pytest.fail(f"pid {process.pid} was reused")


def _note() -> Step[str]:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            return "v"

    note = Note()
    note.name = note.variable = "note"
    return note


# (spec) One run at a time
def test_a_run_takes_the_lock_and_releases_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    seen: list[bool] = []

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            seen.append(lock.is_file())
            return "v"

    note = Note()
    note.name = note.variable = "note"
    execute([note])
    assert seen == [True]
    assert not lock.exists()


# (spec) One run at a time
def test_a_live_lock_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    started = _stamp(datetime.now(UTC))
    _plant(lock, os.getpid(), started)
    with pytest.raises(
        RoadieError,
        match=f"another roadie run holds {lock} \\(pid {os.getpid()}, started {started}\\)",
    ):
        execute([_note()])
    assert lock.is_file()


# (spec) One run at a time
def test_a_lock_whose_process_is_gone_is_taken_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    _plant(lock, _dead_pid(), _stamp(datetime.now(UTC)))
    assert execute([_note()]) == "v"
    assert not lock.exists()


# (spec) One run at a time
def test_a_lock_older_than_a_day_is_taken_over_and_a_younger_one_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    _plant(lock, os.getpid(), _stamp(datetime.now(UTC) - timedelta(hours=24, minutes=1)))
    assert execute([_note()]) == "v"
    assert not lock.exists()

    _plant(lock, os.getpid(), _stamp(datetime.now(UTC) - timedelta(hours=23, minutes=59)))
    with pytest.raises(RoadieError, match="another roadie run holds"):
        execute([_note()])


# (spec) One run at a time
def test_an_unreadable_lock_is_taken_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("not json")
    assert execute([_note()]) == "v"
    assert not lock.exists()


# (spec) One run at a time
def test_a_failed_run_releases_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)

    lock = root / "target" / "roadie" / "run.lock"
    seen: list[bool] = []

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            seen.append(lock.is_file())
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = "boom"
    with pytest.raises(StepFailed, match="nope"):
        execute([boom])
    assert seen == [True]
    assert not lock.exists()


# (spec) One run at a time
# (spec) Requested result
def test_the_lock_is_released_before_the_result_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    seen: list[tuple[str, bool]] = []

    class Server(Service):
        def build(self, run: Run) -> Stopped:
            seen.append(("build", lock.is_file()))
            return Stopped()

        def serve(self, value: Stopped) -> None:
            seen.append(("serve", lock.is_file()))
            lock.write_text("held by a later run")

    execute([Server()])
    assert seen == [("build", True), ("serve", False)]
    assert lock.read_text() == "held by a later run"


# (spec) One run at a time
# (spec) Command line
def test_dry_run_takes_no_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    write_build(root, "class Note(Step[str]):\n    def build(self, run):\n        return 'v'\nitem = Note()\n")
    lock = root / "target" / "roadie" / "run.lock"
    cli.run(root, dry_run=True)
    assert not lock.exists()
    assert "# run" in capsys.readouterr().out

    _plant(lock, os.getpid(), _stamp(datetime.now(UTC)))
    cli.run(root, dry_run=True)
    assert "item = Note()  # run" in capsys.readouterr().out
    assert lock.is_file()
