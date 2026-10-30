"""A test step that runs pytest and reads its JUnit report."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from roadie.build.tests import TestFailure, TestResults, Tests
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input

if TYPE_CHECKING:
    from roadie.engine.run import Run

_OUTPUT_TAIL = 2000


def _junit_failures(report: Path) -> tuple[TestFailure, ...]:
    failures: list[TestFailure] = []
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        label = f"{case.get('classname', '')}::{case.get('name', '')}"
        for child in case:
            if child.tag not in {"failure", "error"}:
                continue
            failures.append(TestFailure(label, child.get("message", "")))
    return tuple(failures)


def _status_failure(status: int, stderr: str) -> str:
    tail = stderr[-_OUTPUT_TAIL:]
    reason = f"pytest exited with status {status}"
    if tail:
        reason = f"{reason}: {tail}"
    return reason


# (spec) Step kinds
class Pytest(Tests):
    """Run pytest; the value is `TestResults`.

    Runs `<interpreter> -m pytest --junitxml=<artifact> <args>` in the base
    directory, using the interpreter that is running Roadie. Each `testcase`
    with a `failure` or `error` child becomes a `TestFailure` named
    `<classname>::<name>`; the message is that element's `message`. Exit
    status 0 and 1 are read from the report. Any other status fails the step
    with `pytest exited with status <n>: <last 2000 characters of standard
    error>`, or without the colon when standard error is empty. `inputs` are
    fingerprinted. Recording and reuse are those of a test step.
    """

    def __init__(self, *args: str, inputs: tuple[Input, ...] = ()) -> None:
        self.args = args
        self.inputs = inputs

    def build(self, run: "Run") -> TestResults:
        report = run.artifact(self, ".xml")
        completed = self._cmd(
            sys.executable,
            "-m",
            "pytest",
            f"--junitxml={report}",
            *self.args,
            capture_output=True,
            check=False,
        )
        if completed.returncode not in (0, 1):
            raise RoadieError(_status_failure(completed.returncode, completed.stderr or ""))
        return TestResults(failures=_junit_failures(report))
