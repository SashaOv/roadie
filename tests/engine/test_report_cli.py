"""The report scenario, run through the roadie command."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from support import git_init, posix_only, write_build


def _roadie() -> Path:
    parent = Path(sys.executable).parent
    for name in ("roadie", "roadie.exe"):
        candidate = parent / name
        if candidate.is_file():
            return candidate
    raise AssertionError(f"roadie is not installed next to {sys.executable}")


def _source(ready_a: Path, ready_b: Path, ready_slow: Path) -> str:
    return f"""\
import time
from pathlib import Path

class Side(Step[str]):
    def __init__(self, label: str, ready: str, peer: str) -> None:
        self.label = label
        self.ready = ready
        self.peer = peer

    def build(self, run: Run) -> str:
        Path(self.ready).write_text(self.label)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if Path(self.peer).is_file():
                return self.label
            time.sleep(0.05)
        raise RuntimeError("peer did not start")

class Slow(Step[None]):
    def __init__(self, ready: str) -> None:
        self.ready = ready

    def build(self, run: Run) -> None:
        Path(self.ready).write_text("ready")
        time.sleep(60)

a = Side("a", {str(ready_a)!r}, {str(ready_b)!r})
b = Side("b", {str(ready_b)!r}, {str(ready_a)!r})
slow = Slow({str(ready_slow)!r})
"""


def _summary(root: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_roadie()), "--summary", str(report)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_roadie()), "-f", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _runs(path: Path) -> list[list[dict[str, Any]]]:
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if event["event"] == "run-start":
            current = [event]
            runs.append(current)
        else:
            current.append(event)
    return runs


# (spec) Run report
# (spec) Command line
def test_the_report_scenario_runs_through_the_roadie_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    report = root / "report.json"
    git_init(root)
    write_build(root, _source(root / "a.ready", root / "b.ready", root / "slow.ready"))

    first = _cli(root, "--jobs", "2", "--report", str(report), "a", "b")
    assert first.returncode == 0, first.stderr
    runs = _runs(report)
    assert len(runs) == 1
    kinds = [event["event"] for event in runs[0]]
    assert kinds[0] == "run-start"
    assert kinds[-1] == "run-end"
    assert runs[0][0]["results"] == ["a", "b"]
    assert runs[0][0]["jobs"] == 2
    first_end = kinds.index("step-end")
    assert kinds[:first_end].count("step-start") == 2
    assert {event["outcome"] for event in runs[0] if event["event"] == "step-end"} == {"built"}

    second = _cli(root, "--jobs", "2", "--report", str(report), "a", "b")
    assert second.returncode == 0, second.stderr
    runs = _runs(report)
    assert len(runs) == 2
    assert {event["outcome"] for event in runs[1] if event["event"] == "step-end"} == {"reused"}
    assert runs[1][-1]["event"] == "run-end"

    summary = _summary(root, report)
    assert summary.returncode == 0, summary.stderr
    assert "0 built, 2 reused, 0 failed, 0 interrupted." in summary.stdout
    assert "`a`" in summary.stdout
    assert "`b`" in summary.stdout
    assert "The run did not finish." not in summary.stdout

    report_dir = root / "report-dir"
    report_dir.mkdir()
    warned = _cli(root, "--report", str(report_dir), "a")
    assert warned.returncode == 0, warned.stderr
    assert warned.stderr.count("cannot write report") == 1
    assert "continuing without it" in warned.stderr


# (spec) Run report
@posix_only
def test_sigkill_during_a_slow_step_leaves_an_unfinished_report(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ready = root / "slow.ready"
    report = root / "report.json"
    git_init(root)
    write_build(root, _source(root / "a.ready", root / "b.ready", ready))
    process = subprocess.Popen(
        [str(_roadie()), "-f", str(root), "--jobs", "1", "--report", str(report), "slow"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.is_file():
            if process.poll() is not None:
                out, err = process.communicate()
                pytest.fail(f"slow step exited early ({process.returncode}): {out}{err}")
            time.sleep(0.05)
        assert ready.is_file()
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    events = [json.loads(line) for line in report.read_text().splitlines()]
    assert events[-1]["event"] == "step-start"
    assert events[-1]["step"] == "slow"
    assert all(event["event"] != "run-end" for event in events)
    summary = _summary(root, report)
    assert summary.returncode == 0, summary.stderr
    assert "The run did not finish." in summary.stdout
