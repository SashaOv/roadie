"""Steps that ask the user at a terminal."""

import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar

from roadie.build.tests import TestFailure, TestResults, Tests
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.inputs import Input
from roadie.engine.many import one_or_many
from roadie.engine.step import Step

if TYPE_CHECKING:
    from roadie.engine.run import Run

_YES = frozenset({"y", "yes"})
_NO = frozenset({"n", "no"})


# (spec) Step kinds
class UserTask(Tests):
    """Ask the user to run checks; the value is `TestResults`.

    `instructions` is a string. Each check is a string (`UserTask takes
    checks as strings: got <type>`), and there is at least one (`UserTask
    takes at least one check`). `inputs` takes inputs and steps, as `Check`
    does: a step there is a dependency, and its output fingerprint is part
    of this step's. Another kind fails with `UserTask inputs takes inputs or
    steps: got <type>`.

    Building prints the instructions, then asks each check, `<check>? [y/n] `.
    `y` or `yes` passes it. `n` or `no` asks `What failed? ` for a reason that
    is not empty, which becomes that check's failure. Any other answer asks
    the check again. A failed check fails the step as any `Tests` failure
    does, so it records nothing. When every check passes, the record keeps
    the step and it is reused, without asking, until its fingerprint changes.

    Standard input that is not a terminal fails the step with `<name> needs
    the user at a terminal`. An end of input fails it with `<name>: no answer
    from the user`. A user step asks only while no other step is running, and
    no step starts while it asks. `--dry` asks nothing.
    """

    asks: ClassVar[bool] = True

    def __init__(
        self,
        instructions: str,
        *checks: str,
        inputs: Input | Step[object] | Iterable[Input | Step[object]] = (),
    ) -> None:
        if not isinstance(instructions, str):
            raise RoadieError(
                f"UserTask takes instructions as a string: got {type(instructions).__name__}"
            )
        if not checks:
            raise RoadieError("UserTask takes at least one check")
        for check in checks:
            if not isinstance(check, str):
                raise RoadieError(
                    f"UserTask takes checks as strings: got {type(check).__name__}"
                )
        self.instructions = instructions
        self.checks = checks
        self.inputs: tuple[Input | Step[object], ...] = one_or_many(
            inputs,
            (Input, Step),
            owner="UserTask",
            parameter="inputs",
            takes="inputs or steps",
        )

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        named: dict[str, object] = {}
        if self.inputs:
            named["inputs"] = self.inputs
        return [self.instructions, *self.checks], named

    def build(self, run: "Run") -> TestResults:
        label = self.name or type(self).__name__
        if not sys.stdin.isatty():
            raise StepFailed(label, f"{label} needs the user at a terminal")
        print(self.instructions, flush=True)
        failures: list[TestFailure] = []
        for check in self.checks:
            while True:
                print(f"{check}? [y/n] ", end="", flush=True)
                answer = _answer(label).lower()
                if answer in _YES:
                    break
                if answer in _NO:
                    failures.append(TestFailure(check, _reason(label)))
                    break
        return TestResults(failures=tuple(failures))


def _answer(label: str) -> str:
    line = sys.stdin.readline()
    if line == "":
        raise StepFailed(label, f"{label}: no answer from the user")
    return line.strip()


def _reason(label: str) -> str:
    while True:
        print("What failed? ", end="", flush=True)
        reason = _answer(label)
        if reason:
            return reason
