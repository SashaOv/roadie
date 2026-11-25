import itertools
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from inspect import get_annotations as inspect_annotations
from pathlib import Path
from threading import RLock
from types import MemberDescriptorType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    ForwardRef,
    Self,
    TypeVar,
    cast,
    get_args,
    get_origin,
)

from pydantic import TypeAdapter

from roadie.engine.config import base_directory, loading_directory
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest
from roadie.engine.process import Command, RunningProcess

if sys.version_info >= (3, 14):
    from annotationlib import Format, get_annotations

if TYPE_CHECKING:
    from roadie.engine.run import Run

_AttributeT = TypeVar("_AttributeT")
_ATTRIBUTE_MARK = object()
Attribute = Annotated[_AttributeT, _ATTRIBUTE_MARK]

_serials = itertools.count()
_fields_lock = RLock()


@dataclass(frozen=True)
class _FieldNames:
    names: dict[str, bool | None]
    slots: tuple[str, ...]
    reserved: str | None
    error: str | None


# (spec) Dependencies and attributes
class Step[T](ABC):
    _adapter: ClassVar[TypeAdapter[Any] | None]
    _roadie_fields: ClassVar[_FieldNames]
    _roadie_local_fields: ClassVar[_FieldNames]
    _roadie_annotation_globals: ClassVar[dict[str, Any]]
    reusable: ClassVar[bool] = True
    recorded: ClassVar[bool] = True
    deferred: ClassVar[bool] = False
    once_per_run: ClassVar[bool] = False
    asks: ClassVar[bool] = False
    serves: ClassVar[bool] = False
    serial: int
    base_dir: Path
    build_dir: Path | None
    variable: str | None
    build_variables: frozenset[str] | None
    name: str | None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if loading_directory() is not None:
            # runpy removes the build module from sys.modules after loading.
            module = sys.modules.get(cls.__module__)
            if module is not None:
                cls._roadie_annotation_globals = vars(module)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        instance = super().__new__(cls)
        instance.serial = next(_serials)
        instance.name = None
        instance.variable = None
        instance.build_variables = None
        instance.base_dir = base_directory()
        instance.build_dir = loading_directory()
        return instance

    def attributes(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name, marked in _fields_for(type(self)).names.items()
            if marked
        }

    def resolve(self, path: str | Path) -> Path:
        return (self.base_dir / path).resolve()

    def _label(self) -> str:
        """Return the step's name, or its class name when it has none."""
        return self.name or type(self).__name__

    def dependencies(self) -> Iterator["Step[Any]"]:
        for value in self.attributes().values():
            if isinstance(value, Step):
                yield value
            elif isinstance(value, tuple):
                yield from (item for item in value if isinstance(item, Step))

    # (spec) Step hooks
    @abstractmethod
    def build(self, run: "Run") -> T:
        """See docs/spec.md, "Dependencies and attributes"."""

    def teardown(self, value: T) -> None:
        pass

    def publish(self, run: "Run", value: T) -> T:
        """See docs/spec.md, "Step hooks"."""
        return value

    def report(self, run: "Run", value: T) -> None:
        """See docs/spec.md, "Step hooks"."""

    def serve(self, run: "Run", value: T) -> None:
        """See docs/spec.md, "Step hooks"."""

    def encode(self, value: T) -> object:
        """See docs/spec.md, "Step hooks"."""
        adapter = _value_adapter(type(self))
        if adapter is None:
            return value
        return adapter.dump_python(value, mode="json")

    def matches(self, recorded: object) -> bool:
        """See docs/spec.md, "Step hooks"."""
        return True

    def decode(self, recorded: object) -> T:
        adapter = _value_adapter(type(self))
        if adapter is None:
            return cast(T, recorded)
        return cast(T, adapter.validate_python(recorded))

    def output_fingerprint(self, fingerprint: str, value: T) -> str:
        """See docs/spec.md, "Step hooks"."""
        return digest(value)

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        """See docs/spec.md, "Dry run output"."""
        validate_attributes(self)
        named = {key: value for key, value in self.attributes().items() if value is not None}
        return [], named

    # (spec) Step helpers
    # (spec) Enforcing declared inputs
    def _cmd(
        self,
        *args: str,
        capture_output: bool = False,
        show_output: bool = False,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """See docs/spec.md, "Step helpers" and "Enforcing declared inputs"."""
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
            environment=environment,
            label=self._label(),
        )


# (spec) Reusing a step
class Composite(Step[tuple[object, ...]]):
    recorded = False
    steps: Attribute[tuple[Step[Any], ...]]

    def __init__(self, steps: tuple[Step[Any], ...]) -> None:
        self.steps = steps

    def build(self, run: "Run") -> tuple[object, ...]:
        return tuple(run[step] for step in self.steps)

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        return list(self.steps), {}


_RESERVED = frozenset(
    {*Step.__annotations__, *(name for name in vars(Step) if not name.startswith("_"))}
)
_HOOKS = frozenset(name for name in _RESERVED if callable(getattr(Step, name, None)))
_ENGINE_FIELDS = frozenset(Step.__annotations__)


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


def is_reserved(name: str) -> bool:
    return name in _RESERVED


def _fields_for(cls: type[Step[Any]]) -> _FieldNames:
    try:
        return cls.__dict__["_roadie_fields"]
    except KeyError:
        with _fields_lock:
            cached = cls.__dict__.get("_roadie_fields")
            if cached is not None:
                return cached
            names: dict[str, bool | None] = {}
            slots: list[str] = []
            error = reserved = None
            saved_globals = {
                base.__module__: base.__dict__["_roadie_annotation_globals"]
                for base in cls.__mro__
                if "_roadie_annotation_globals" in base.__dict__
            }
            for base in reversed(cls.__mro__):
                if base is Step or base is object:
                    continue
                fields = _local_fields(base, saved_globals.get(base.__module__))
                names.update(fields.names)
                slots.extend(fields.slots)
                if fields.reserved is not None:
                    reserved = fields.reserved
                if fields.error is not None:
                    error = fields.error
            cls._roadie_fields = _FieldNames(names, tuple(slots), reserved, error)
            if "_roadie_annotation_globals" in cls.__dict__:
                del cls._roadie_annotation_globals
            return cls._roadie_fields


def _local_fields(cls: type, saved_globals: dict[str, Any] | None) -> _FieldNames:
    cached = cls.__dict__.get("_roadie_local_fields")
    if cached is not None:
        return cached

    if sys.version_info >= (3, 14):
        try:
            hints = get_annotations(cls, format=Format.FORWARDREF)
        except Exception:  # noqa: BLE001 - annotations can raise arbitrary errors
            # Inspect fields separately when the annotation function fails.
            hints = get_annotations(cls, format=Format.STRING)
    else:
        hints = inspect_annotations(cls)

    module = sys.modules.get(cls.__module__)
    namespace = saved_globals if saved_globals is not None else (vars(module) if module else {})
    local = dict(vars(cls))
    local.update((param.__name__, param) for param in _type_params(cls))
    names: dict[str, bool | None] = {}
    error = None
    source = None
    for name, hint in hints.items():
        try:
            if sys.version_info >= (3, 14) and _contains_forward_ref(hint):
                # Re-evaluate the expression to distinguish an undefined name
                # from a quoted inner type, which need not be resolved.
                if source is None:
                    source = get_annotations(cls, format=Format.STRING)
                hint = source[name]
            if isinstance(hint, str):
                hint = eval(hint, namespace, local)
                if isinstance(hint, str):
                    hint = eval(hint, namespace, local)
            names[name] = (
                None if get_origin(hint) is ClassVar
                else get_origin(hint) is Annotated and any(mark is _ATTRIBUTE_MARK for mark in get_args(hint)[1:])
            )
        except Exception as cause:  # noqa: BLE001 - report the field with its error
            if error is None:
                error = f"{cls.__name__}.{name} annotation cannot be read: {cause}"

    slots = tuple(name for name, value in vars(cls).items() if isinstance(value, MemberDescriptorType))
    declared = getattr(cls, "__dataclass_fields__", None)
    if declared is None:
        declared = (name for name, marked in names.items() if marked is not None)
    reserved = next((name for name in declared if name in _RESERVED), None)
    fields = _FieldNames(names, slots, reserved, error)
    if hints or slots or issubclass(cls, Step):
        cls._roadie_local_fields = fields  # type: ignore[attr-defined]
    return fields


def _contains_forward_ref(hint: object) -> bool:
    return isinstance(hint, ForwardRef) or any(_contains_forward_ref(arg) for arg in get_args(hint))


# (spec) Dependencies and attributes
def validate_attributes(step: Step[Any]) -> None:
    fields = _fields_for(type(step))
    if fields.error is not None:
        raise RoadieError(fields.error)
    if fields.reserved is not None:
        _raise_reserved(step, fields.reserved)
    for name in (*vars(step), *(name for name in fields.slots if hasattr(step, name))):
        if name in _HOOKS:
            _raise_reserved(step, name)
        if name not in fields.names and name not in _ENGINE_FIELDS:
            raise RoadieError(f"{step._label()} attribute {name} is not declared in its class")
    for name, marked in fields.names.items():
        if marked is False and not name.startswith("_") and _holds_dependency(getattr(step, name, None)):
            raise RoadieError(f"{step._label()} field {name} holds a step but is not an Attribute")


def _holds_dependency(value: object) -> bool:
    return isinstance(value, (Step, Input)) or (
        isinstance(value, tuple) and any(_holds_dependency(item) for item in value)
    )


def _raise_reserved(step: Step[Any], name: str) -> None:
    raise RoadieError(f"{step._label()} attribute {name} is reserved by Roadie")
