"""Declare steps, the hooks Roadie calls on them, and the commands they run."""

import itertools
import os
import shlex
import subprocess
import threading
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
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.inputs import digest
from roadie.engine.lock import RUN_PID_VARIABLE
from roadie.engine.prefix import current_prefix, output_streams, read_lines
from roadie.engine.process import (
    RunningProcess,
    interrupt_requested,
    track_process,
    untrack_process,
)

if TYPE_CHECKING:
    from roadie.engine.run import Run

_serials = itertools.count()
_value_adapters: dict[type, TypeAdapter[Any] | None] = {}


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


def _value_adapter(cls: type) -> TypeAdapter[Any] | None:
    try:
        return _value_adapters[cls]
    except KeyError:
        value_type = _concrete_value_type(cls)
        adapter = None if value_type is None else TypeAdapter(value_type)
        _value_adapters[cls] = adapter
        return adapter


# (spec) Dependencies and attributes
class Step[T](ABC):
    reusable: ClassVar[bool] = True
    recorded: ClassVar[bool] = True
    deferred: ClassVar[bool] = False
    once_per_run: ClassVar[bool] = False
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
        environment: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Print `+ <command>` and run the command in the step's base directory.

        Its environment is the process environment, then `environment`, then
        the step's environment files, then `ROADIE_RUN_PID` set to this
        process's PID. It inherits standard input. Standard output and
        standard error are shown as they arrive, each line prefixed with
        `[<name>] ` when the run uses more than one job. With
        `capture_output`, standard output is also returned, unprefixed.
        Unless `check` is false, a non-zero exit fails the step with
        `<name> failed: <command> exited with status <n>: <last 2000
        characters of standard error>`.
        """
        print("+", shlex.join(args), flush=True)
        prefix = current_prefix()
        out, err = output_streams()
        process = subprocess.Popen(
            args,
            cwd=self.base_dir,
            env=self._command_env(environment),
            stdout=subprocess.PIPE if capture_output or prefix else None,
            stderr=subprocess.PIPE,
            text=True,
        )
        track_process(process, session=False)
        readers: list[threading.Thread] = []
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            assert process.stderr is not None
            readers.append(read_lines(process.stderr.readline, err, prefix, stderr_parts))
            if process.stdout is not None:
                readers.append(read_lines(process.stdout.readline, out, prefix, stdout_parts))
            try:
                process.wait()
                for reader in readers:
                    reader.join()
                stdout = "".join(stdout_parts) if capture_output else None
                stderr = "".join(stderr_parts)
            except KeyboardInterrupt:
                if process.poll() is None:
                    process.terminate()
                process.wait()
                raise
        finally:
            for reader in readers:
                reader.join()
            untrack_process(process)
        if interrupt_requested():
            raise KeyboardInterrupt
        completed = subprocess.CompletedProcess(
            args,
            process.returncode if process.returncode is not None else 1,
            stdout,
            stderr,
        )
        if check:
            self._check_cmd(completed)
        return completed

    def _check_cmd(self, completed: subprocess.CompletedProcess[str]) -> None:
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[-2000:]
            reason = f"{shlex.join(completed.args)} exited with status {completed.returncode}"
            raise StepFailed(
                self.name or type(self).__name__,
                f"{reason}: {detail}" if detail else reason,
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
        """Print `+ <command>`, start a server, and return it once it is ready.

        The command runs in the step's base directory with the environment of
        `_cmd`, inherits standard input, and starts in a new session (POSIX)
        so its whole process tree can be signalled. When the run uses more
        than one job, each line the server prints is shown as it arrives,
        prefixed with `[<name>] `. The result holds the
        process and the URL `http://127.0.0.1:<port>`; it is ready once the
        port accepts connections, or, with `health_path`, once
        `GET <url><health_path>` answers 200.

        If the process exits first, the step fails with `<process> exited with
        status <n> before becoming ready`; if `timeout` seconds pass, with
        `<process> did not start listening on port <port> within <timeout>s`
        (or `did not become ready at <url> within <timeout>s`). `<process>` is
        the executable's file name and up to two following arguments that are
        not options. In both cases the process tree is stopped.
        """
        print("+", shlex.join(args), flush=True)
        return RunningProcess.start(
            args,
            port=port,
            environment=self._command_env(environment),
            timeout=timeout,
            cwd=self.base_dir,
            health_path=health_path,
        )

    def _command_env(self, environment: Mapping[str, str] | None) -> dict[str, str]:
        # Process environment, then the step's settings, then env files.
        # This run's PID is last, so a nested roadie can tell who started it.
        merged = {**os.environ, **(environment or {}), **self.env_values}
        merged[RUN_PID_VARIABLE] = str(os.getpid())
        return merged


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
    label = step.name or type(step).__name__
    raise RoadieError(f"{label} attribute {name} is reserved by Roadie")
