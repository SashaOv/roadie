"""Build a plan and tear down each step after its last consumer."""

import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values

from roadie.cargo import cargo_snapshot
from roadie.errors import RoadieError, StepFailed
from roadie.fingerprint import fingerprint
from roadie.inputs import digest
from roadie.output import publish
from roadie.process import interrupt_on_terminate
from roadie.records import artifact_dir, load_records, save_records
from roadie.root import project_root, use_project_root
from roadie.step import Binary, File, Service, Step, Tests
from roadie.testing import TestResults


class Run:
    def __init__(self) -> None:
        self._values: dict[int, object] = {}
        self.reused: set[int] = set()

    # (spec) Step values
    def __getitem__[T](self, step: Step[T]) -> T:
        if id(step) not in self._values:
            raise RoadieError(
                f"{step.name or type(step).__name__} is not available in this run"
            )
        return cast(T, self._values[id(step)])

    # (spec) Artifacts
    def artifact(self, step: Step[Any], suffix: str) -> Path:
        root = project_root()
        stem = step.variable or f"{type(step).__name__}-{step.serial}"
        destination = root / "target" / "roadie" / artifact_dir(step.build_dir, root) / f"{stem}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination


def execute(
    steps: Sequence[Step[Any]],
    *,
    root: Path,
    locked: bool = False,
    env: Sequence[Path] = (),
) -> object:
    with use_project_root(root):
        return _run_steps(steps, locked=locked, env=env)


def _run_steps(
    steps: Sequence[Step[Any]], *, locked: bool = False, env: Sequence[Path] = ()
) -> object:
    _apply_env(steps, env)
    run = Run()
    live: list[Step[Any]] = []
    last_use = {id(step): index for index, step in enumerate(steps)}
    for index, step in enumerate(steps):
        for dependency in step.dependencies():
            last_use[id(dependency)] = max(last_use.get(id(dependency), index), index)
    requested = id(steps[-1]) if steps else None
    _hold_service_dependencies(steps, last_use, requested)
    result: object = None
    outputs: dict[int, str] = {}
    started: set[int] = set()
    with cargo_snapshot(), interrupt_on_terminate():
        try:
            for index, step in enumerate(steps):
                try:
                    step_fingerprint = fingerprint(step, outputs=outputs)
                    if isinstance(step, Service) and id(step) != requested:
                        # A service's output fingerprint is its step fingerprint,
                        # so a consumer can be reused without the service running.
                        outputs[id(step)] = step_fingerprint
                    else:
                        restored = _restore(step, step_fingerprint)
                        if restored is not None:
                            value = restored
                            run.reused.add(id(step))
                            print(f"reused {step.name or type(step).__name__}", flush=True)
                        else:
                            _start_services(step, run, live, started)
                            value = step.build(run)
                            if isinstance(step, Service):
                                started.add(id(step))
                        run._values[id(step)] = value
                        live.append(step)
                        if restored is None and isinstance(step, File):
                            value = publish(step, cast(Path, value), locked=locked)
                            run._values[id(step)] = value
                        if isinstance(step, Tests):
                            _report_tests(step, value)
                        _remember(step, step_fingerprint, value)
                        outputs[id(step)] = _output_fingerprint(
                            step, value, step_fingerprint, outputs
                        )
                        result = value
                        if isinstance(step, Service) and id(step) == requested:
                            _await_requested_service(step, value)
                except (KeyboardInterrupt, StepFailed):
                    raise
                except BaseException as error:
                    raise StepFailed(step.name or type(step).__name__, _reason(error)) from error
                expired = [item for item in live if last_use[id(item)] == index]
                _teardown(run, live, expired)
            for step in steps:
                if isinstance(step, Service) and id(step) not in started:
                    name = step.name or type(step).__name__
                    print(f"not started {name}: no step that takes it runs", flush=True)
            return result
        finally:
            _teardown(run, live, list(live))


# (spec) Requested services
def _await_requested_service(step: Step[Any], value: object) -> None:
    """Keep a requested service up until Ctrl-C, SIGTERM, or the process exits."""
    name = step.name or type(step).__name__
    url = getattr(value, "url", "")
    print(f"{name} is up: {url} (Ctrl-C to stop)", flush=True)
    process = getattr(value, "process", None)
    if process is None:
        return
    try:
        status = process.wait()
    except KeyboardInterrupt:
        return
    if status == 0:
        print(f"{name} exited", flush=True)
        return
    raise StepFailed(name, f"exited with status {status}")


def _hold_service_dependencies(
    steps: Sequence[Step[Any]], last_use: dict[int, int], requested: int | None
) -> None:
    """Keep a deferred service's dependencies until the service's last consumer."""

    def extend(step: Step[Any], index: int, seen: set[int]) -> None:
        for dependency in step.dependencies():
            if id(dependency) in seen:
                continue
            seen.add(id(dependency))
            if last_use.get(id(dependency), -1) < index:
                last_use[id(dependency)] = index
            extend(dependency, index, seen)

    for step in steps:
        if isinstance(step, Service) and id(step) != requested:
            extend(step, last_use[id(step)], set())


def _start_services(
    step: Step[Any], run: Run, live: list[Step[Any]], started: set[int]
) -> None:
    """Start deferred services `step` takes, just before `step` builds."""
    for dependency in step.dependencies():
        if not isinstance(dependency, Service) or id(dependency) in started:
            continue
        _start_services(dependency, run, live, started)
        try:
            value = dependency.build(run)
        except (KeyboardInterrupt, StepFailed):
            raise
        except BaseException as error:
            raise StepFailed(dependency.name or type(dependency).__name__, _reason(error)) from error
        run._values[id(dependency)] = value
        live.append(dependency)
        started.add(id(dependency))


# (spec) Environment files
def _apply_env(steps: Sequence[Step[Any]], env: Sequence[Path]) -> None:
    """Read env files once. ``config(env=...)`` applies first, then ``--env``."""
    cli_paths = tuple(_resolve_env_path(path) for path in env)
    cache: dict[Path, dict[str, str]] = {}

    def read(path: Path) -> dict[str, str]:
        if path not in cache:
            if not path.is_file():
                raise RoadieError(f"environment file does not exist: {path}")
            loaded = dotenv_values(path)
            cache[path] = {key: value for key, value in loaded.items() if value is not None}
        return cache[path]

    for path in cli_paths:
        read(path)
    for step in steps:
        merged: dict[str, str] = {}
        for path in (*step.env_files, *cli_paths):
            merged.update(read(path))
        step.env_values = merged


def _resolve_env_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def reuse_markers(
    steps: Sequence[Step[Any]], *, root: Path, env: Sequence[Path] = ()
) -> list[bool]:
    """True when `--dry` can promise the step will not run."""
    with use_project_root(root):
        return _reuse_markers(steps, env=env)


def _reuse_markers(steps: Sequence[Step[Any]], *, env: Sequence[Path] = ()) -> list[bool]:
    _apply_env(steps, env)
    marks: dict[int, bool] = {}
    outputs: dict[int, str] = {}
    ordered: list[bool] = []
    with cargo_snapshot():
        for step in steps:
            step_fingerprint = fingerprint(step, outputs=outputs)
            dependencies_reused = all(marks.get(id(item), False) for item in step.dependencies())
            record = _matching(step, step_fingerprint) if dependencies_reused else None
            mark = dependencies_reused if not type(step).recorded else record is not None
            marks[id(step)] = mark
            ordered.append(mark)
            if not type(step).recorded:
                members = getattr(step, "steps", ())
                outputs[id(step)] = digest([outputs[id(item)] for item in members])
            elif record is not None:
                outputs[id(step)] = str(record["output"])
            else:
                outputs[id(step)] = "pending"
    return ordered


def _reusable(step: Step[Any]) -> bool:
    return bool(step.variable) and type(step).reusable and type(step).recorded


# (spec) Reusing a step
def _matching(step: Step[Any], step_fingerprint: str) -> dict[str, Any] | None:
    if not _reusable(step):
        return None
    record = load_records(step.build_dir, project_root()).get(step.variable or "")
    if not isinstance(record, dict) or record.get("fingerprint") != step_fingerprint:
        return None
    if not _valid(step, record):
        return None
    return record


def _restore(step: Step[Any], step_fingerprint: str) -> object | None:
    record = _matching(step, step_fingerprint)
    if record is None:
        return None
    return _value(step, record)


def _valid(step: Step[Any], record: dict[str, Any]) -> bool:
    if isinstance(step, (File, Binary)):
        stored = record.get("value")
        content = record.get("content")
        if not isinstance(stored, str) or not isinstance(content, str):
            return False
        path = _stored_path(stored)
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == content
    return True


def _value(step: Step[Any], record: dict[str, Any]) -> object:
    if isinstance(step, (File, Binary)):
        return _stored_path(str(record["value"]))
    if isinstance(step, Tests):
        payload = record.get("value")
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        return TestResults((), tuple(str(message) for message in messages))
    return record.get("value")


# (spec) Records
def _remember(step: Step[Any], step_fingerprint: str, value: object) -> None:
    if not _reusable(step):
        return
    root = project_root()
    records = load_records(step.build_dir, root)
    if step.build_variables is not None:
        records = {key: item for key, item in records.items() if key in step.build_variables}
    assert step.variable is not None
    records[step.variable] = _entry(step, step_fingerprint, value)
    save_records(step.build_dir, records, root)


def _entry(step: Step[Any], step_fingerprint: str, value: object) -> dict[str, Any]:
    if isinstance(step, (File, Binary)):
        path = Path(cast(Path, value))
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "fingerprint": step_fingerprint,
            "output": content if isinstance(step, File) else step_fingerprint,
            "content": content,
            "value": _relative(path),
        }
    if isinstance(step, Tests):
        if not isinstance(value, TestResults):
            raise RoadieError("A Tests step must return TestResults")
        return {
            "fingerprint": step_fingerprint,
            "output": step_fingerprint,
            "content": None,
            "value": {"messages": list(value.messages)},
        }
    return {
        "fingerprint": step_fingerprint,
        "output": digest(value),
        "content": None,
        "value": value,
    }


# (spec) Output fingerprints
def _output_fingerprint(
    step: Step[Any], value: object, step_fingerprint: str, outputs: Mapping[int, str]
) -> str:
    members = getattr(step, "steps", None)
    if not type(step).recorded and isinstance(members, tuple):
        return digest([outputs[id(member)] for member in members])
    if isinstance(step, File):
        return hashlib.sha256(Path(cast(Path, value)).read_bytes()).hexdigest()
    if isinstance(step, (Binary, Service, Tests)):
        return step_fingerprint
    return digest(value)


def _report_tests(step: Step[Any], value: object) -> None:
    if not isinstance(value, TestResults):
        raise RoadieError("A Tests step must return TestResults")
    for message in value.messages:
        print(message, flush=True)
    if value.failures:
        reason = "\n".join(
            f"{failure.name}: {failure.message}" for failure in value.failures
        )
        raise StepFailed(step.name or type(step).__name__, reason)


def _stored_path(stored: str) -> Path:
    path = Path(stored)
    if path.is_absolute():
        return path
    return project_root() / path


def _relative(path: Path) -> str:
    resolved = path.resolve()
    root = project_root().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    return relative.as_posix()


def _reason(error: BaseException) -> str:
    if isinstance(error, SystemExit) and isinstance(error.code, str):
        return error.code
    return str(error).strip() or type(error).__name__


# (spec) Teardown
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
