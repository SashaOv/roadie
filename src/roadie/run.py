"""Run a result's steps in plan order and stop every service at the end."""

import sys
from collections.abc import Mapping, Sequence

from wright.graph import Result, Service, WrightError, current_result
from wright.process import interrupt_on_terminate


class StepFailed(WrightError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} failed: {reason}")


def execute(steps: Sequence[Result]) -> object:
    """Run `steps` and stop every service, including when a step fails or is interrupted."""
    values: dict[int, object] = {}
    services: list[object] = []
    with interrupt_on_terminate():
        try:
            for step in steps:
                token = current_result.set(step)
                try:
                    value = _call(step, _resolve(step.inputs, values))
                    values[id(step)] = value
                    if isinstance(step, Service):
                        services.append(value)
                finally:
                    current_result.reset(token)
            if not steps:
                return None
            return values[id(steps[-1])]
        finally:
            _stop_services(services)


def _resolve(
    inputs: Mapping[str, object], values: Mapping[int, object]
) -> dict[str, object]:
    resolved: dict[str, object] = {}
    for key, value in inputs.items():
        if isinstance(value, Result):
            resolved[key] = values[id(value)]
        else:
            resolved[key] = value
    return resolved


def _call(step: Result, inputs: Mapping[str, object]) -> object:
    try:
        return step.implementation(**inputs)
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        raise StepFailed(step.name or step.step, _reason(error)) from error


def _reason(error: BaseException) -> str:
    if isinstance(error, SystemExit) and isinstance(error.code, str):
        return error.code
    text = str(error).strip()
    return text or error.__class__.__name__


def _stop_services(services: list[object]) -> None:
    errors: list[Exception] = []
    while services:
        value = services.pop()
        stop = getattr(value, "stop", None)
        try:
            if not callable(stop):
                raise WrightError("a service produced nothing that can be stopped")
            stop()
        except Exception as error:  # noqa: BLE001 — keep stopping the other services
            errors.append(error)
    if errors and sys.exc_info()[1] is None:
        raise errors[0]
