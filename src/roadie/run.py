"""Build a plan and tear down each step after its last consumer."""

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from forge.environment import repository_root

from wright.errors import StepFailed, WrightError
from wright.output import publish
from wright.process import interrupt_on_terminate
from wright.step import File, Step, Tests
from wright.testing import TestResults


class Run:
    def __init__(self) -> None:
        self._values: dict[int, object] = {}

    def __getitem__[T](self, step: Step[T]) -> T:
        if id(step) not in self._values:
            raise WrightError(
                f"{step.name or type(step).__name__} is not available in this run"
            )
        return cast(T, self._values[id(step)])

    def artifact(self, step: Step[Any], suffix: str) -> Path:
        stem = (step.name or f"{type(step).__name__}_{step.serial}").replace(":", "_").replace("/", "_")
        destination = repository_root() / "target" / "wright" / f"{stem}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination


def execute(steps: Sequence[Step[Any]], *, locked: bool = False) -> object:
    run = Run()
    live: list[Step[Any]] = []
    last_use = {id(step): index for index, step in enumerate(steps)}
    for index, step in enumerate(steps):
        for dependency in step.dependencies():
            last_use[id(dependency)] = max(last_use[id(dependency)], index)
    result: object = None
    with interrupt_on_terminate():
        try:
            for index, step in enumerate(steps):
                try:
                    value = step.build(run)
                    run._values[id(step)] = value
                    live.append(step)
                    if isinstance(step, File):
                        value = publish(step, value, locked=locked)
                        run._values[id(step)] = value
                    if isinstance(step, Tests):
                        if not isinstance(value, TestResults):
                            raise WrightError("A Tests step must return TestResults")
                        for message in value.messages:
                            print(message, flush=True)
                        if value.failures:
                            reason = "\n".join(f"{failure.name}: {failure.message}" for failure in value.failures)
                            raise StepFailed(step.name or type(step).__name__, reason)
                except (KeyboardInterrupt, StepFailed):
                    raise
                except BaseException as error:
                    raise StepFailed(step.name or type(step).__name__, _reason(error)) from error
                result = value
                expired = [item for item in live if last_use[id(item)] == index]
                _teardown(run, live, expired)
            return result
        finally:
            _teardown(run, live, list(live))


def _reason(error: BaseException) -> str:
    if isinstance(error, SystemExit) and isinstance(error.code, str):
        return error.code
    return str(error).strip() or type(error).__name__


def _teardown(run: Run, live: list[Step[Any]], expired: list[Step[Any]]) -> None:
    errors: list[StepFailed] = []
    for step in reversed(expired):
        live[:] = [item for item in live if item is not step]
        value = run._values.pop(id(step))
        try:
            step.teardown(value)
        except Exception as error:  # noqa: BLE001 — finish releasing the other steps
            errors.append(
                StepFailed(
                    step.name or type(step).__name__, f"teardown: {_reason(error)}"
                )
            )
    if errors:
        if sys.exc_info()[1] is None:
            for failure in errors[1:]:
                print(failure, file=sys.stderr)
            raise errors[0]
        for failure in errors:
            print(failure, file=sys.stderr)
