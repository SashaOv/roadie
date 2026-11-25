"""A test step that runs pytest and reads its JUnit report."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from roadie.build.check import CheckInputs, normalize_check_inputs
from roadie.build.tests import TestFailure, TestResults, Tests
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input
from roadie.engine.process import OUTPUT_TAIL
from roadie.engine.step import Attribute, Step

if TYPE_CHECKING:
    from roadie.engine.run import Run


# (spec) Step kinds
class Pytest(Tests):
    """Run pytest; the value is `TestResults`.

    Runs `<interpreter> -m pytest --junitxml=<artifact> <args>` in the base
    directory, using the interpreter that is running Roadie. Each `testcase`
    with a `failure` or `error` child becomes a `TestFailure` named
    `<classname>::<name>`; the message is that element's `message`. Exit
    status 0 and 1 are read from the report. Any other status fails the step
    with `pytest exited with status <n>: <last 2000 characters of standard
    error>`, or without the colon when standard error is empty. `inputs` is
    inputs and steps, one or any iterable, stored as a tuple. A step there is
    a dependency, and its output fingerprint is part of the check's. A value
    of the wrong kind fails with `Pytest inputs takes inputs or steps: got
    <type>`. Recording and reuse are those of a test step.
    """

    args: Attribute[tuple[str, ...]]
    inputs: Attribute[tuple[Input | Step[object], ...]]

    def __init__(
        self, *args: str, inputs: CheckInputs = ()
    ) -> None:
        self.args = args
        self.inputs = normalize_check_inputs(inputs, owner=type(self).__name__)

    def build(self, run: "Run") -> TestResults:
        report = run.artifact(self, ".xml")
        completed = self._cmd(
            sys.executable,
            "-m",
            "pytest",
            f"--junitxml={report}",
            *self.args,
            capture_output=True,
            show_output=True,
            check=False,
        )
        if completed.returncode not in (0, 1):
            raise RoadieError(_status_failure(completed.returncode, completed.stderr or ""))
        return TestResults(failures=_junit_failures(report))


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
    tail = stderr[-OUTPUT_TAIL:]
    reason = f"pytest exited with status {status}"
    if tail:
        reason = f"{reason}: {tail}"
    return reason
