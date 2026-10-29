"""Steps that run tests, and the results they build."""

from dataclasses import dataclass

from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.step import Step


@dataclass(frozen=True)
class TestFailure:
    name: str
    message: str


@dataclass(frozen=True)
class TestResults:
    failures: tuple[TestFailure, ...] = ()
    messages: tuple[str, ...] = ()


# (spec) Step kinds
class Tests(Step[TestResults]):
    """A step whose value is `TestResults`: failures and log messages.

    Roadie prints the messages; any failure fails the step with one
    `<name>: <message>` line per failure. The record keeps the messages, so a
    reused step prints the messages of its last pass; a failed step records
    nothing, so failures are never replayed. The output fingerprint is the
    step's own fingerprint.
    """

    def report(self, value: TestResults) -> None:
        if not isinstance(value, TestResults):
            raise RoadieError("A Tests step must return TestResults")
        for message in value.messages:
            print(message, flush=True)
        if value.failures:
            reason = "\n".join(
                f"{failure.name}: {failure.message}" for failure in value.failures
            )
            raise StepFailed(self.name or type(self).__name__, reason)

    def encode(self, value: TestResults) -> object:
        return {"messages": list(value.messages)}

    def decode(self, recorded: object) -> TestResults:
        messages = recorded.get("messages", []) if isinstance(recorded, dict) else []
        return TestResults((), tuple(str(message) for message in messages))

    def output_fingerprint(self, fingerprint: str, value: TestResults) -> str:
        return fingerprint
