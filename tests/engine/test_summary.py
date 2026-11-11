"""Markdown summary of the last run in a report."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from support import run_cli

from roadie import cli

_GIB = 1024**3

_FINISHED = """\
# Run

Free space was 10.0 GiB at the start, 7.0 GiB at the lowest, and 7.0 GiB at the end.

1 built, 1 reused, 1 failed, 1 interrupted.

## Slowest

- `compile` 2:05
- `test` 0:10
- `pack` 0:04
- `lint` 0:01

## Disk growth

Growth includes the steps running alongside.

- `compile` 2.0 GiB
- `test` 1.0 GiB
- `lint` 0.0 GiB
- `pack` 0.0 GiB
"""


def _event(kind: str, free: int, **fields: object) -> dict[str, Any]:
    return {"event": kind, "time": "2026-11-10T00:00:00Z", "free": free, **fields}


def _sample() -> list[dict[str, Any]]:
    return [
        _event("run-start", 10 * _GIB, total=100 * _GIB, results=["pair"], jobs=1),
        _event("step-start", 10 * _GIB, step="compile", running=[]),
        _event("step-end", 8 * _GIB, step="compile", outcome="built", elapsed=125, running=[]),
        _event("step-start", 8 * _GIB, step="lint", running=[]),
        _event("step-end", 8 * _GIB, step="lint", outcome="reused", elapsed=1, running=[]),
        _event("step-start", 8 * _GIB, step="test", running=[]),
        _event("step-end", 7 * _GIB, step="test", outcome="failed", elapsed=10, running=[]),
        _event("step-start", 7 * _GIB, step="pack", running=[]),
        _event("step-end", 7 * _GIB, step="pack", outcome="interrupted", elapsed=4, running=[]),
        _event("run-end", 7 * _GIB, outcome="failed", elapsed=140),
    ]


def _write(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


# (spec) Run report
# (spec) Command line
def test_a_finished_run_renders_as_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report.json"
    _write(report, _sample())
    cli.run(summary=report)
    assert capsys.readouterr().out == _FINISHED


# (spec) Run report
def test_an_unfinished_run_says_it_did_not_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report.json"
    _write(report, _sample()[:-1])
    cli.run(summary=report)
    assert capsys.readouterr().out == _FINISHED + "\nThe run did not finish.\n"


# (spec) Run report
def test_a_file_of_two_runs_shows_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report.json"
    earlier = [
        _event("run-start", 3 * _GIB, total=9 * _GIB, results=["old"], jobs=1),
        _event("step-start", 3 * _GIB, step="old", running=[]),
        _event("step-end", 1 * _GIB, step="old", outcome="built", elapsed=9999, running=[]),
        _event("run-end", 1 * _GIB, outcome="succeeded", elapsed=9999),
    ]
    _write(report, earlier + _sample())
    cli.run(summary=report)
    output = capsys.readouterr().out
    assert output == _FINISHED
    assert "old" not in output


# (spec) Run report
def test_the_slowest_and_growth_lists_stop_at_ten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    free = 100 * _GIB
    events: list[dict[str, Any]] = [
        _event("run-start", free, total=200 * _GIB, results=["all"], jobs=1)
    ]
    for index in range(1, 12):
        name = f"s{index:02d}"
        growth = (12 - index) * _GIB
        events.append(_event("step-start", free, step=name, running=[]))
        free -= growth
        events.append(
            _event("step-end", free, step=name, outcome="built", elapsed=index, running=[])
        )
    events.append(_event("run-end", free, outcome="succeeded", elapsed=free))
    report = tmp_path / "report.json"
    _write(report, events)
    cli.run(summary=report)
    text = capsys.readouterr().out
    slow, growth = text.split("## Disk growth", maxsplit=1)
    slow_items = [line for line in slow.splitlines() if line.startswith("- ")]
    growth_items = [line for line in growth.splitlines() if line.startswith("- ")]
    assert slow_items[0] == "- `s11` 0:11"
    assert slow_items[-1] == "- `s02` 0:02"
    assert len(slow_items) == 10
    assert growth_items[0] == "- `s01` 11.0 GiB"
    assert growth_items[-1] == "- `s10` 2.0 GiB"
    assert len(growth_items) == 10


# (spec) Run report
def test_a_missing_report_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit, match=re.escape(f"report does not exist: {missing}")):
        cli.run(summary=missing)


# (spec) Run report
def test_a_line_that_is_not_an_event_names_the_line(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    _write(report, _sample()[:1])
    report.write_text(report.read_text() + "nope\n")
    with pytest.raises(SystemExit, match=re.escape(f"{report} line 2 is not a report event")):
        cli.run(summary=report)


# (spec) Run report
def test_a_report_with_no_run_names_the_path(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("")
    with pytest.raises(SystemExit, match=re.escape(f"report has no run: {report}")):
        cli.run(summary=report)


# (spec) Run report
def test_an_unreadable_report_names_the_reason(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=re.escape(f"cannot read report {tmp_path}:")):
        cli.run(summary=tmp_path)


# (spec) Command line
@pytest.mark.parametrize("extra", [["checks"], ["mode=1"], ["--dry"]])
def test_summary_takes_only_a_report_path(tmp_path: Path, extra: list[str]) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}\n")
    with pytest.raises(SystemExit, match="--summary takes only a report path"):
        run_cli(["--summary", str(report), *extra])
