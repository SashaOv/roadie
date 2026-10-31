"""Parameter values supplied to steps for one run."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest
from roadie.engine.step import Step

_values: ContextVar[dict[int, dict[int, str | None]] | None] = ContextVar(
    "roadie_parameter_values", default=None
)
_current: ContextVar[Step[Any] | None] = ContextVar("roadie_parameter_step", default=None)


# (spec) Parameters
class Parameter(Input):
    """An input whose value is chosen for the run."""

    def __init__(self, name: str, *, default: str | None, required: bool) -> None:
        self.name = name
        self.default = default
        self.required = required

    @property
    def value(self) -> str | None:
        table = _values.get()
        step = _current.get()
        if table is None or step is None:
            raise RoadieError(f"parameter {self.name} has no value outside a run")
        slot = table.get(id(step))
        if slot is None or id(self) not in slot:
            raise RoadieError(f"parameter {self.name} has no value outside a run")
        return slot[id(self)]

    def fingerprint(self, base_dir: Path) -> str:
        return digest(self.value)


@dataclass(frozen=True)
class ParameterValues:
    run_wide: dict[str, str]
    own: dict[int, dict[str, str]]


def iter_parameters(step: Step[Any]) -> Iterator[Parameter]:
    def walk(value: object) -> Iterator[Parameter]:
        if isinstance(value, Parameter):
            yield value
        elif isinstance(value, tuple):
            for item in value:
                yield from walk(item)

    for value in step.attributes().values():
        yield from walk(value)


def parameter_names(step: Step[Any]) -> list[str]:
    names: list[str] = []
    for param in iter_parameters(step):
        if param.name not in names:
            names.append(param.name)
    return names


def bind_parameters(
    steps: Sequence[Step[Any]], values: ParameterValues
) -> Token[dict[int, dict[int, str | None]] | None]:
    """Resolve each step's parameters and keep them for the rest of the run."""
    known: list[str] = []
    found: dict[int, list[Parameter]] = {}
    for step in steps:
        params = list(iter_parameters(step))
        found[id(step)] = params
        for param in params:
            if param.name not in known:
                known.append(param.name)
    for name in values.run_wide:
        if name not in known:
            if known:
                listed = ", ".join(known)
                raise RoadieError(f"unknown parameter {name}; the plan takes: {listed}")
            raise RoadieError(f"unknown parameter {name}; the plan takes no parameters")
    steps_by_id = {id(step): step for step in steps}
    for step_id, own in values.own.items():
        step = steps_by_id[step_id]
        names = parameter_names(step)
        label = step.name or type(step).__name__
        for name in own:
            if name not in names:
                if names:
                    raise RoadieError(
                        f"{label} takes no parameter {name}; it takes: {', '.join(names)}"
                    )
                raise RoadieError(f"{label} takes no parameters")
    table: dict[int, dict[int, str | None]] = {}
    for step in steps:
        slot: dict[int, str | None] = {}
        own = values.own.get(id(step), {})
        label = step.name or type(step).__name__
        for param in found[id(step)]:
            if param.name in own:
                slot[id(param)] = own[param.name]
            elif param.name in values.run_wide:
                slot[id(param)] = values.run_wide[param.name]
            elif param.required:
                raise RoadieError(f"{label} needs parameter {param.name}")
            else:
                slot[id(param)] = param.default
        table[id(step)] = slot
    return _values.set(table)


def reset_parameters(token: Token[dict[int, dict[int, str | None]] | None]) -> None:
    _values.reset(token)


@contextmanager
def current_step(step: Step[Any]) -> Iterator[None]:
    token = _current.set(step)
    try:
        yield
    finally:
        _current.reset(token)
