"""Declare steps, the hooks Roadie calls on them, and the commands they run."""

import itertools
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Self,
    TypeVar,
    cast,
    get_args,
    get_origin,
)

from pydantic import TypeAdapter

from roadie.engine.config import base_directory, configured_env, loading_directory
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import digest
from roadie.engine.process import Command, RunningProcess

if TYPE_CHECKING:
    from roadie.engine.run import Run

_serials = itertools.count()


def _type_params(cls: type) -> tuple[Any, ...]:
    params = getattr(cls, "__type_params__", ())
    return params if isinstance(params, tuple) else ()


def _concrete_value_type(cls: type) -> Any:
    """The step's value type `T` when it is a concrete type, else None."""
    substitutions: dict[Any, Any] = {}

    def visit(current: type, seen: set[type]) -> None:
        if current in seen:
            return
        seen.add(current)
        for base in getattr(current, "__orig_bases__", ()):
            origin = get_origin(base)
            if not isinstance(origin, type):
                continue
            params = _type_params(origin)
            for param, arg in zip(params, get_args(base), strict=False):
                if isinstance(param, TypeVar) and param not in substitutions:
                    substitutions[param] = arg
            visit(origin, seen)
        for base in current.__bases__:
            visit(base, seen)

    visit(cls, set())
    params = _type_params(Step)
    if len(params) != 1 or not isinstance(params[0], TypeVar):
        return None
    if params[0] not in substitutions:
        return None
    arg = substitutions[params[0]]
    seen_vars: set[Any] = set()
    while isinstance(arg, TypeVar):
        if arg in seen_vars or arg not in substitutions:
            return None
        seen_vars.add(arg)
        arg = substitutions[arg]
    if arg is Any:
        return None
    if arg is None:
        return type(None)
    return arg


def _value_adapter(cls: "type[Step[Any]]") -> TypeAdapter[Any] | None:
    try:
        return cls.__dict__["_adapter"]
    except KeyError:
        value_type = _concrete_value_type(cls)
        adapter = None if value_type is None else TypeAdapter(value_type)
        cls._adapter = adapter
        return adapter


# (spec) Dependencies and attributes
class Step[T](ABC):
    _adapter: ClassVar[TypeAdapter[Any] | None]
    reusable: ClassVar[bool] = True
    recorded: ClassVar[bool] = True
    deferred: ClassVar[bool] = False
    once_per_run: ClassVar[bool] = False
    asks: ClassVar[bool] = False
    serial: int
    base_dir: Path
    build_dir: Path | None
    variable: str | None
    build_variables: frozenset[str] | None
    env_files: tuple[Path, ...]
    env_values: dict[str, str]
    name: str | None

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        instance = super().__new__(cls)
        instance.serial = next(_serials)
        instance.name = None
        instance.variable = None
        instance.build_variables = None
        instance.base_dir = base_directory()
        instance.build_dir = loading_directory()
        instance.env_files = configured_env()
        instance.env_values = {}
        return instance

    def attributes(self) -> dict[str, object]:
        return {
            key: value
            for key, value in vars(self).items()
            if key
            not in {
                "serial",
                "name",
                "base_dir",
                "build_dir",
                "variable",
                "build_variables",
                "env_files",
                "env_values",
            }
        }

    def resolve(self, path: str | Path) -> Path:
        return (self.base_dir / path).resolve()

    def _label(self) -> str:
        """Return the step's name, or its class name when it has none."""
        return self.name or type(self).__name__

    def _reports_reused(self, reused: set[int]) -> bool:
        """True when this step prints `reused` because every member was reused."""
        return False

    def dependencies(self) -> Iterator["Step[Any]"]:
        for value in self.attributes().values():
            if isinstance(value, Step):
                yield value
            elif isinstance(value, tuple):
                yield from (item for item in value if isinstance(item, Step))

    # (spec) Step hooks
    @abstractmethod
    def build(self, run: "Run") -> T:
        """Build this step from its dependencies' values."""

    def teardown(self, value: T) -> None:
        pass

    def publish(self, run: "Run", value: T) -> T:
        """Return the value of a step that just built, after acting on it."""
        return value

    def report(self, value: T) -> None:
        """Show a built or reused value; raise to fail the step."""

    def serve(self, value: T) -> None:
        """Keep the requested result in use before the run tears it down."""

    def encode(self, value: T) -> object:
        """Return the JSON value the step's record keeps."""
        adapter = _value_adapter(type(self))
        if adapter is None:
            return value
        return adapter.dump_python(value, mode="json")

    def matches(self, recorded: object) -> bool:
        """True when a recorded value can still be restored."""
        return True

    def decode(self, recorded: object) -> T:
        adapter = _value_adapter(type(self))
        if adapter is None:
            return cast(T, recorded)
        return cast(T, adapter.validate_python(recorded))

    def output_fingerprint(self, fingerprint: str, value: T) -> str:
        """Digest of what this step built, which its consumers' fingerprints include."""
        return digest(value)

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        """Positional and keyword arguments that `--dry` shows."""
        reject_reserved(self)
        named = {key: value for key, value in self.attributes().items() if value is not None}
        return [], named

    # (spec) Step helpers
    def _cmd(
        self,
        *args: str,
        capture_output: bool = False,
        show_output: bool = False,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self._command(args, environment).run(
            capture_output=capture_output, show_output=show_output, check=check
        )

    # (spec) Step helpers
    def _start_cmd(
        self,
        *args: str,
        port: int,
        environment: Mapping[str, str] | None = None,
        timeout: float = 120,
        health_path: str | None = None,
    ) -> RunningProcess:
        return self._command(args, environment).start(
            port=port, timeout=timeout, health_path=health_path
        )

    def _command(self, args: tuple[str, ...], environment: Mapping[str, str] | None) -> Command:
        return Command(
            args,
            cwd=self.base_dir,
            environment={**(environment or {}), **self.env_values},
            label=self._label(),
        )


def _reserved_names() -> frozenset[str]:
    names = set(Step.__annotations__)
    names.update(name for name in vars(Step) if not name.startswith("_"))
    return frozenset(names)


_RESERVED = _reserved_names()


def is_reserved(name: str) -> bool:
    """True when `name` is one Roadie keeps for itself on a step."""
    return name in _RESERVED
_HOOKS = frozenset(name for name in _RESERVED if callable(getattr(Step, name, None)))


# (spec) Dependencies and attributes
def reject_reserved(step: Step[Any]) -> None:
    """Fail when a step attribute uses a name Roadie keeps for itself."""
    for cls in type(step).mro():
        if cls is Step:
            break
        fields = getattr(cls, "__dataclass_fields__", None)
        if fields is not None:
            declared = fields
        else:
            declared = [
                name
                for name, hint in getattr(cls, "__annotations__", {}).items()
                if get_origin(hint) is not ClassVar
            ]
        for name in declared:
            if name in _RESERVED:
                _raise_reserved(step, name)
    for name in vars(step):
        if name in _HOOKS:
            _raise_reserved(step, name)


def _raise_reserved(step: Step[Any], name: str) -> None:
    raise RoadieError(f"{step._label()} attribute {name} is reserved by Roadie")
