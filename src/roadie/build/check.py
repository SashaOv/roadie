import shlex
from collections.abc import Iterable
from typing import TYPE_CHECKING

from roadie.build.tests import TestFailure, TestResults, Tests
from roadie.engine.inputs import Input
from roadie.engine.many import one_or_many
from roadie.engine.process import OUTPUT_TAIL
from roadie.engine.step import Attribute, Step

if TYPE_CHECKING:
    from roadie.engine.run import Run


type CheckInputs = Input | Step[object] | Iterable[Input | Step[object]]


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

    command: Attribute[tuple[str, ...]]
    inputs: Attribute[tuple[Input | Step[object], ...]]

    def __init__(
        self,
        *command: str,
        inputs: CheckInputs = (),
    ) -> None:
        self.command = command
        self.inputs = normalize_check_inputs(inputs, owner=type(self).__name__)

    def build(self, run: "Run") -> TestResults:
        completed = self._cmd(
            *self.command, capture_output=True, show_output=True, check=False
        )
        if completed.returncode == 0:
            return TestResults()
        output = f"{completed.stdout or ''}{completed.stderr or ''}"
        failure = TestFailure(shlex.join(self.command), output[-OUTPUT_TAIL:])
        return TestResults(failures=(failure,))


def normalize_check_inputs(inputs: CheckInputs, *, owner: str) -> tuple[Input | Step[object], ...]:
    return one_or_many(
        inputs, (Input, Step), owner=owner, parameter="inputs", takes="inputs or steps"
    )
