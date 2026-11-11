"""Append a run report, and render the last run as Markdown."""

import json
import re
import shutil
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from roadie.engine.display import format_elapsed
from roadie.engine.errors import RoadieError
from roadie.engine.root import project_root


def _timestamp() -> str:
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# (spec) Run report
class Report:
    """Append JSON events to one file. A write or open failure warns once."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self._failed = False
        try:
            self._file = path.open("a", encoding="utf-8", newline="\n")
        except OSError as error:
            self._fail(error)

    def run_start(self, results: list[str], jobs: int) -> None:
        self._emit("run-start", results=results, jobs=jobs)

    def step_start(self, step: str) -> None:
        self._emit("step-start", step=step)

    def step_end(self, step: str, outcome: str, elapsed: float) -> None:
        self._emit("step-end", step=step, outcome=outcome, elapsed=elapsed)

    def run_end(self, outcome: str, elapsed: float) -> None:
        self._emit("run-end", outcome=outcome, elapsed=elapsed)

    def close(self) -> None:
        with self._lock:
            handle = self._file
            self._file = None
        if handle is not None:
            handle.close()

    def _emit(self, event: str, **fields: object) -> None:
        with self._lock:
            if self._failed or self._file is None:
                return
            try:
                usage = shutil.disk_usage(project_root())
                payload: dict[str, object] = {
                    "event": event,
                    "time": _timestamp(),
                    "free": usage.free,
                }
                if event == "run-start":
                    payload["total"] = usage.total
                payload.update(fields)
                self._file.write(json.dumps(payload) + "\n")
                self._file.flush()
            except OSError as error:
                self._fail(error)

    def _fail(self, error: OSError) -> None:
        if self._failed:
            return
        self._failed = True
        print(
            f"cannot write report {self._path}: {error}; continuing without it",
            file=sys.stderr,
            flush=True,
        )


_GIB = 1024**3
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_EVENTS = frozenset({"run-start", "step-start", "step-end", "run-end"})
_STEP_OUTCOMES = frozenset({"built", "reused", "failed", "interrupted"})
_RUN_OUTCOMES = frozenset({"succeeded", "failed", "interrupted"})


class _Measured:
    def __init__(self, name: str, start_free: int) -> None:
        self.name = name
        self.start_free = start_free
        self.end_free: int | None = None
        self.elapsed: float | None = None
        self.outcome: str | None = None


# (spec) Run report
def render_summary(path: Path) -> str:
    """Markdown for the last run in a report. The file is not a build file."""
    events = _read_events(path)
    starts = [index for index, event in enumerate(events) if event["event"] == "run-start"]
    if not starts:
        raise RoadieError(f"report has no run: {path}")
    run = events[starts[-1] :]
    finished = [event for event in run if event["event"] == "run-end"]
    end_free = finished[-1]["free"] if finished else run[-1]["free"]
    steps = _measured(run)
    counts: Counter[str] = Counter(_outcome(step) for step in steps)
    slowest = sorted(steps, key=_elapsed, reverse=True)[:10]
    growth = sorted(steps, key=_growth, reverse=True)[:10]
    lines = [
        "# Run",
        "",
        (
            f"Free space was {_gib(run[0]['free'])} GiB at the start, "
            f"{_gib(min(event['free'] for event in run))} GiB at the lowest, "
            f"and {_gib(end_free)} GiB at the end."
        ),
        "",
        (
            f"{counts['built']} built, {counts['reused']} reused, "
            f"{counts['failed']} failed, {counts['interrupted']} interrupted."
        ),
        "",
        "## Slowest",
        "",
    ]
    lines.extend(f"- `{step.name}` {format_elapsed(_elapsed(step))}" for step in slowest)
    if slowest:
        lines.append("")
    lines.extend(["## Disk growth", "", "Growth includes the steps running alongside."])
    if growth:
        lines.append("")
        lines.extend(f"- `{step.name}` {_gib(_growth(step))} GiB" for step in growth)
    text = "\n".join(lines)
    if not finished:
        text += "\n\nThe run did not finish."
    return text + "\n"


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RoadieError(f"report does not exist: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RoadieError(f"cannot read report {path}: {error}") from error
    events: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        event = _parse_event(line)
        if event is None:
            raise RoadieError(f"{path} line {number} is not a report event")
        events.append(event)
    return events


def _parse_event(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("event") not in _EVENTS:
        return None
    if not _time(value.get("time")) or not _whole(value.get("free")):
        return None
    event = value["event"]
    if event == "run-start":
        if not (
            _whole(value.get("total"))
            and _whole(value.get("jobs"))
            and _strings(value.get("results"))
        ):
            return None
    elif event == "step-start":
        if not isinstance(value.get("step"), str):
            return None
    elif event == "step-end":
        if not (
            isinstance(value.get("step"), str)
            and value.get("outcome") in _STEP_OUTCOMES
            and _number(value.get("elapsed"))
        ):
            return None
    elif not (value.get("outcome") in _RUN_OUTCOMES and _number(value.get("elapsed"))):
        return None
    return value


def _measured(run: list[dict[str, Any]]) -> list[_Measured]:
    pending: dict[str, list[_Measured]] = {}
    order: list[_Measured] = []
    for event in run:
        kind = event["event"]
        if kind == "step-start":
            step = _Measured(event["step"], event["free"])
            order.append(step)
            pending.setdefault(step.name, []).append(step)
        elif kind == "step-end":
            queue = pending.get(event["step"])
            step = queue.pop(0) if queue else _Measured(event["step"], event["free"])
            if not queue and step not in order:
                order.append(step)
            step.end_free = event["free"]
            step.elapsed = float(event["elapsed"])
            step.outcome = event["outcome"]
    return [step for step in order if step.elapsed is not None and step.end_free is not None]


def _outcome(step: _Measured) -> str:
    assert step.outcome is not None
    return step.outcome


def _elapsed(step: _Measured) -> float:
    assert step.elapsed is not None
    return step.elapsed


def _growth(step: _Measured) -> int:
    assert step.end_free is not None
    return step.start_free - step.end_free


def _gib(nbytes: int) -> str:
    return f"{nbytes / _GIB:.1f}"


def _time(value: object) -> bool:
    return isinstance(value, str) and _TIME.fullmatch(value) is not None


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
