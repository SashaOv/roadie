import sys
import time
from collections.abc import Sequence
from typing import Any

from roadie.engine.display import format_elapsed, format_step
from roadie.engine.prefix import unprefixed
from roadie.engine.report import Report
from roadie.engine.step import Step


# (spec) Run report
class Progress:
    def __init__(self, report: Report | None) -> None:
        self._report = report
        self._began: dict[int, float] = {}
        self._started_at = time.monotonic()
        self._reported_run = False
        self._finished = False

    def run_start(self, names: list[str], jobs: int) -> None:
        if self._report is not None:
            self._report.run_start(names, jobs)
            self._reported_run = True

    def run_end(self, outcome: str) -> None:
        if self._report is not None and self._reported_run:
            self._report.run_end(outcome, time.monotonic() - self._started_at)

    def finished(self) -> None:
        if self._finished:
            return
        self._finished = True
        with unprefixed():
            print(f"finished in {format_elapsed(time.monotonic() - self._started_at)}", flush=True)

    def stopping(self, names: Sequence[str]) -> None:
        line = f"interrupted: stopping {', '.join(names)}" if names else "interrupted"
        with unprefixed():
            print(line, flush=True)

    def building(self, step: Step[Any]) -> float | None:
        if not type(step).recorded:
            return None
        print(f"build {format_step(step)}", flush=True)
        self._step_start(step)
        return time.monotonic()

    def built(self, step: Step[Any], elapsed: float | None) -> None:
        if elapsed is not None:
            sys.stdout.flush()
            print(f"built {step._label()} in {format_elapsed(elapsed)}", flush=True)

    def reusing(self, step: Step[Any]) -> None:
        self._step_start(step)

    def reused(self, step: Step[Any]) -> None:
        self._step_start(step)
        print(f"reused {step._label()}", flush=True)

    def completed(self, step: Step[Any], *, reused: bool, missing: Sequence[str]) -> None:
        if missing:
            print(
                f"{step._label()} reads files it does not declare: {', '.join(missing)}",
                flush=True,
            )
            self.failed(step)
        else:
            self._step_end(step, "reused" if reused else "built")

    def failed(self, step: Step[Any]) -> None:
        self._step_end(step, "failed")

    def interrupted(self, step: Step[Any]) -> None:
        self._step_end(step, "interrupted")

    def not_started(self, step: Step[Any]) -> None:
        print(f"not started {step._label()}: no step that takes it runs", flush=True)

    def _step_start(self, step: Step[Any]) -> None:
        if self._report is None or id(step) in self._began:
            return
        self._began[id(step)] = time.monotonic()
        self._report.step_start(step._label())

    def _step_end(self, step: Step[Any], outcome: str) -> None:
        if self._report is None:
            return
        began = self._began.pop(id(step), None)
        if began is not None:
            self._report.step_end(step._label(), outcome, time.monotonic() - began)


class DryProgress(Progress):
    def __init__(self) -> None:
        super().__init__(None)
        self._marks: dict[int, str] = {}

    def building(self, step: Step[Any]) -> None:
        self._marks[id(step)] = "run"

    def reused(self, step: Step[Any]) -> None:
        self._marks[id(step)] = "reuse"

    def not_started(self, step: Step[Any]) -> None:
        self._marks[id(step)] = "not started"

    def render_plan(self, steps: Sequence[Step[Any]]) -> None:
        for step in steps:
            print(f"{format_step(step)}  # {self._marks[id(step)]}", flush=True)
