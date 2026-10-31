"""A test step that runs a command."""

import shlex
from collections.abc import Iterable
from typing import TYPE_CHECKING

from roadie.build.tests import TestFailure, TestResults, Tests
from roadie.engine.inputs import Input
from roadie.engine.many import one_or_many
from roadie.engine.step import Step

if TYPE_CHECKING:
    from roadie.engine.run import Run

_OUTPUT_TAIL = 2000


# (spec) Step kinds
class Check(Tests):
    """Run a command; the value is `TestResults`.

    The command runs in the step's base directory. Exit status 0 is a pass.
    Any other status is one failure whose name is the command, with the last
    2000 characters of its standard output followed by its standard error.
    `inputs` is inputs and steps, one or any iterable, stored as a tuple. A
    step there is a dependency, and its output fingerprint is part of the
    check's. A value of the wrong kind fails with `Check inputs takes inputs
    or steps: got <type>`. Recording and reuse are those of a test step.
    """

    def __init__(
        self,
        *command: str,
        inputs: Input | Step[object] | Iterable[Input | Step[object]] = (),
    ) -> None:
        self.command = command
        self.inputs: tuple[Input | Step[object], ...] = one_or_many(
            inputs, (Input, Step), owner="Check", parameter="inputs", takes="inputs or steps"
        )

    def build(self, run: "Run") -> TestResults:
        completed = self._cmd(*self.command, capture_output=True, check=False)
        if completed.returncode == 0:
            return TestResults()
        output = f"{completed.stdout or ''}{completed.stderr or ''}"
        failure = TestFailure(shlex.join(self.command), output[-_OUTPUT_TAIL:])
        return TestResults(failures=(failure,))
