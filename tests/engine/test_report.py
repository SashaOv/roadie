"""Run report: one JSON line per event, appended as the run goes."""

import _thread
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from support import FakeDeployment, execute, write_build

from roadie import cli
from roadie.build import Group, Service, Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run

_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class _Usage:
    total = 1000
    used = 400
    free = 600


def _events(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text().splitlines()
    assert lines and all(lines)
    return [json.loads(line) for line in lines]


def _patch_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("roadie.engine.report.shutil.disk_usage", lambda path: _Usage())


def _assert_common(event: dict[str, Any]) -> None:
    assert _TIME.match(event["time"])
    assert event["free"] == 600


def _pair() -> str:
    return (
        "class Side(Step[str]):\n"
        "    def __init__(self, label: str) -> None:\n"
        "        self.label = label\n"
        "    def build(self, run: Run) -> str:\n"
        "        return self.label\n\n"
        "a = Side('a')\n"
        "b = Side('b')\n"
        "pair = Group(a, b)\n"
    )


def _run_overlapping_pair(root: Path, report: Path) -> None:
    """Run `a` and `b` together. `b` stays in its build until `a` has ended."""
    ready = {"a": threading.Event(), "b": threading.Event()}

    class Side(Step[str]):
        def __init__(self, label: str) -> None:
            self.label = label

        def build(self, run: Run) -> str:
            ready[self.label].set()
            other = "b" if self.label == "a" else "a"
            assert ready[other].wait(10)
            if self.label == "b":
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if report.is_file() and any(
                        '"event": "step-end"' in line and '"step": "a"' in line
                        for line in report.read_text().splitlines()
                    ):
                        return self.label
                    time.sleep(0.05)
                raise AssertionError("a did not finish")
            return self.label

    a = Side("a")
    a.name = a.variable = "a"
    b = Side("b")
    b.name = b.variable = "b"
    pair = Group(a, b)
    pair.name = pair.variable = "pair"
    execute([a, b, pair], root=root, jobs=2, report=report, targets=[pair])


# (spec) Run report
# (spec) Command line
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_run_records_every_event_and_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    _patch_disk(monkeypatch)
    report = tmp_path / "report.json"
    if jobs == 1:
        write_build(tmp_path, _pair())
        cli.run(file=tmp_path, jobs=1, report=report)
    else:
        _run_overlapping_pair(tmp_path, report)
    events = _events(report)
    assert events[0]["event"] == "run-start"
    assert events[-1]["event"] == "run-end"
    for event in events:
        _assert_common(event)
    started = events[0]
    assert started["total"] == 1000
    assert started["results"] == ["pair"]
    assert started["jobs"] == jobs
    steps = [event for event in events if event["event"] in {"step-start", "step-end"}]
    assert {event["step"] for event in steps} == {"a", "b"}
    ends = [event for event in events if event["event"] == "step-end"]
    assert len(ends) == 2
    assert {event["outcome"] for event in ends} == {"built"}
    for event in steps:
        other = "b" if event["step"] == "a" else "a"
        if event["event"] == "step-end":
            assert event["outcome"] == "built"
            assert event["elapsed"] >= 0
            if jobs == 1:
                assert event["running"] == []
            elif event["step"] == "a":
                assert event["running"] == ["b"]
        else:
            assert event["running"] == ([other] if jobs > 1 else [])
    finished = events[-1]
    assert finished["outcome"] == "succeeded"
    assert finished["elapsed"] >= 0
    assert "pair" not in {event.get("step") for event in events}


# (spec) Run report
def test_a_reused_group_is_reported_and_a_fresh_one_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_disk(monkeypatch)
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    def build(self, run: Run) -> str:\n"
        "        return 'v'\n"
        "note = Note()\n"
        "bundle = Group(note)\n",
    )
    report = tmp_path / "report.json"
    cli.run(file=tmp_path, jobs=1, report=report)
    cli.run(file=tmp_path, jobs=1, report=report)
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in _events(report):
        if event["event"] == "run-start":
            current = [event]
            runs.append(current)
        else:
            current.append(event)
    assert len(runs) == 2
    first_steps = {event["step"] for event in runs[0] if "step" in event}
    assert first_steps == {"note"}
    assert [event["outcome"] for event in runs[0] if event["event"] == "step-end"] == ["built"]
    second_ends = {
        event["step"]: event["outcome"] for event in runs[1] if event["event"] == "step-end"
    }
    assert second_ends == {"note": "reused", "bundle": "reused"}


# (spec) Run report
# (spec) Deferred steps
def test_a_deferred_step_ends_when_its_start_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_disk(monkeypatch)
    now = {"t": 0.0}
    monkeypatch.setattr("roadie.engine.run.time.monotonic", lambda: now["t"])

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
    after = After(server)
    after.name = after.variable = "after"
    report = tmp_path / "report.json"
    execute([server, after], root=tmp_path, report=report)
    ends = {event["step"]: event for event in _events(report) if event["event"] == "step-end"}
    assert ends["server"]["outcome"] == "built"
    assert ends["server"]["elapsed"] == 4
    assert ends["server"]["running"] == ["after"]
    assert ends["after"]["outcome"] == "built"
    assert ends["after"]["elapsed"] == 5
    assert ends["after"]["running"] == []
    assert _events(report)[-1]["elapsed"] == 9


# (spec) Run report
# (spec) Failures
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_failed_step_is_failed_and_the_run_stays_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    _patch_disk(monkeypatch)

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = boom.variable = "boom"
    report = tmp_path / "report.json"
    with pytest.raises(StepFailed, match="boom failed: nope"):
        execute([boom], root=tmp_path, jobs=jobs, report=report)
    events = _events(report)
    assert [event["outcome"] for event in events if event["event"] == "step-end"] == ["failed"]
    assert events[-1]["event"] == "run-end"
    assert events[-1]["outcome"] == "failed"


# (spec) Run report
# (spec) Failures
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_records_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    _patch_disk(monkeypatch)
    root = tmp_path.resolve()
    pidfile = root / "child.pid"
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(sys.executable, "-c", script)

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                _thread.interrupt_main()
                return
            time.sleep(0.05)

    note = Note()
    note.name = note.variable = "note"
    report = root / "report.json"
    threading.Thread(target=interrupt_when_ready, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        execute([note], root=root, jobs=jobs, report=report)
    events = _events(report)
    assert [event["outcome"] for event in events if event["event"] == "step-end"] == ["interrupted"]
    assert events[-1]["outcome"] == "interrupted"


# (spec) Run report
def test_a_report_appends_and_is_closed_when_the_run_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_disk(monkeypatch)
    report = tmp_path / "report.json"
    report.write_text('{"kept": true}\n')
    opened: list[object] = []
    real_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)  # type: ignore[arg-type]
        if self == report:
            opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracking_open)
    write_build(tmp_path, "class Note(Step[str]):\n    def build(self, run: Run) -> str:\n        return 'v'\nnote = Note()\n")
    cli.run(file=tmp_path, jobs=1, report=report)
    cli.run(file=tmp_path, jobs=1, report=report)
    text = report.read_text()
    assert text.startswith('{"kept": true}\n')
    assert text.count('"event": "run-start"') == 2
    assert opened
    assert all(handle.closed for handle in opened)  # type: ignore[attr-defined]


# (spec) Run report
def test_an_open_failure_warns_once_and_leaves_the_run_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(tmp_path, "class Note(Step[str]):\n    def build(self, run: Run) -> str:\n        return 'v'\nnote = Note()\n")
    report = tmp_path / "report.json"
    real_open = Path.open

    def broken_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == report:
            raise OSError("permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", broken_open)
    cli.run(file=tmp_path, jobs=1, report=report)
    err = capsys.readouterr().err
    assert err.count("cannot write report") == 1
    assert err.strip() == f"cannot write report {report}: permission denied; continuing without it"
    assert not report.exists()

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = boom.variable = "boom"
    with pytest.raises(StepFailed, match="boom failed: nope"):
        execute([boom], root=tmp_path, report=report)
    assert capsys.readouterr().err.count("cannot write report") == 1


# (spec) Run report
def test_a_write_failure_partway_warns_once_and_leaves_the_run_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_disk(monkeypatch)
    write_build(tmp_path, "class Note(Step[str]):\n    def build(self, run: Run) -> str:\n        return 'v'\nnote = Note()\n")
    report = tmp_path / "report.json"
    real_open = Path.open

    def flaky_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)  # type: ignore[arg-type]
        if self != report:
            return handle
        original = handle.write
        state = {"n": 0}

        def write(data: str) -> int:
            state["n"] += 1
            if state["n"] > 1:
                raise OSError("disk full")
            return original(data)

        handle.write = write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", flaky_open)
    cli.run(file=tmp_path, jobs=1, report=report)
    err = capsys.readouterr().err
    assert err.count("cannot write report") == 1
    assert f"cannot write report {report}: disk full; continuing without it" in err
    assert len(_events(report)) == 1
    assert _events(report)[0]["event"] == "run-start"

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = boom.variable = "boom"
    failing = tmp_path / "failing.json"

    def flaky_fail(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)  # type: ignore[arg-type]
        if self != failing:
            return handle
        original = handle.write
        state = {"n": 0}

        def write(data: str) -> int:
            state["n"] += 1
            if state["n"] > 1:
                raise OSError("disk full")
            return original(data)

        handle.write = write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", flaky_fail)
    with pytest.raises(StepFailed, match="boom failed: nope"):
        execute([boom], root=tmp_path, report=failing)
    assert capsys.readouterr().err.count("cannot write report") == 1


# (spec) Command line
def test_report_refuses_dry_and_clean_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    def build(self, run: Run) -> str:\n"
        "        (self.base_dir / 'ran').write_text('yes')\n"
        "        return 'v'\n"
        "note = Note()\n",
    )
    state = tmp_path / "target" / "roadie"
    state.mkdir(parents=True)
    (state / "keep").write_text("sentinel")
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit, match="--report does not apply to --dry"):
        cli.run(file=tmp_path, report=report, dry_run=True)
    with pytest.raises(SystemExit, match="--report does not apply to --clean"):
        cli.run(file=tmp_path, report=report, clean=True)
    assert not report.exists()
    assert not (tmp_path / "ran").exists()
    assert (state / "keep").read_text() == "sentinel"
