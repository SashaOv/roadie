"""Build a plan and tear down each step after its last consumer."""

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values

from roadie.engine.display import announce, format_step
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.fingerprint import fingerprint
from roadie.engine.inputs import run_scope
from roadie.engine.lock import HeldLock, acquire_run_lock
from roadie.engine.process import interrupt_on_terminate
from roadie.engine.records import (
    artifact_dir,
    load_records,
    save_records,
    sweep_stale_state,
)
from roadie.engine.root import project_root, use_project_root
from roadie.engine.step import Step


class Run:
    def __init__(self, *, locked: bool = False) -> None:
        self._values: dict[int, object] = {}
        self.reused: set[int] = set()
        self.locked = locked

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
    dry: bool = False,
) -> object:
    with use_project_root(root):
        if dry:
            return _run_steps(steps, locked=locked, env=env, dry=True)
        held = acquire_run_lock(root)
        try:
            return _run_steps(steps, locked=locked, env=env, run_lock=held)
        finally:
            held.release()


def _run_steps(
    steps: Sequence[Step[Any]],
    *,
    locked: bool = False,
    env: Sequence[Path] = (),
    run_lock: HeldLock | None = None,
    dry: bool = False,
) -> object:
    _apply_env(steps, env)
    run = Run(locked=locked)
    live: list[Step[Any]] = []
    last_use = {id(step): index for index, step in enumerate(steps)}
    for index, step in enumerate(steps):
        for dependency in step.dependencies():
            last_use[id(dependency)] = max(last_use.get(id(dependency), index), index)
    requested = id(steps[-1]) if steps else None
    _hold_deferred_dependencies(steps, last_use, requested)
    result: object = None
    outputs: dict[int, str] = {}
    started: set[int] = set()
    marks: dict[int, str] = {}
    with run_scope(), interrupt_on_terminate():
        try:
            for index, step in enumerate(steps):
                try:
                    step_fingerprint = fingerprint(step, outputs=outputs)
                    if step.deferred and id(step) != requested:
                        # A deferred step's output fingerprint is its step
                        # fingerprint, so a consumer can be reused without it.
                        outputs[id(step)] = step_fingerprint
                        if dry:
                            marks[id(step)] = "not started"
                    else:
                        record = _matching(step, step_fingerprint)
                        if record is not None and dry:
                            run.reused.add(id(step))
                            marks[id(step)] = "reuse"
                            outputs[id(step)] = str(record["output"])
                        elif record is not None:
                            value = step.decode(record["value"])
                            run.reused.add(id(step))
                            print(f"reused {step.name or type(step).__name__}", flush=True)
                            result, output = _kept(
                                step, run, live, value, built=False,
                                step_fingerprint=step_fingerprint, requested=requested, run_lock=run_lock,
                            )
                            outputs[id(step)] = output
                        elif dry and _reuses_without_record(step, run):
                            run.reused.add(id(step))
                            marks[id(step)] = "reuse"
                            outputs[id(step)] = step_fingerprint
                        elif dry:
                            _dry_start_deferred(step, marks)
                            marks[id(step)] = "run"
                            outputs[id(step)] = "dry"
                        else:
                            _start_deferred(step, run, live, started)
                            announce(step)
                            value = step.build(run)
                            if step.deferred:
                                started.add(id(step))
                            result, output = _kept(
                                step, run, live, value, built=True,
                                step_fingerprint=step_fingerprint, requested=requested, run_lock=run_lock,
                            )
                            outputs[id(step)] = output
                except (KeyboardInterrupt, StepFailed):
                    raise
                except BaseException as error:
                    raise StepFailed(step.name or type(step).__name__, _reason(error)) from error
                if not dry:
                    expired = [item for item in live if last_use[id(item)] == index]
                    _teardown(run, live, expired)
            if dry:
                for step in steps:
                    print(f"{format_step(step)}  # {marks[id(step)]}", flush=True)
            else:
                for step in steps:
                    if step.deferred and id(step) != requested and id(step) not in started:
                        name = step.name or type(step).__name__
                        print(f"not started {name}: no step that takes it runs", flush=True)
            return result
        finally:
            if not dry:
                _teardown(run, live, list(live))


# (spec) Deferred steps
def _hold_deferred_dependencies(
    steps: Sequence[Step[Any]], last_use: dict[int, int], requested: int | None
) -> None:
    """Keep a deferred step's dependencies until the step's last consumer."""

    def extend(step: Step[Any], index: int, seen: set[int]) -> None:
        for dependency in step.dependencies():
            if id(dependency) in seen:
                continue
            seen.add(id(dependency))
            if last_use.get(id(dependency), -1) < index:
                last_use[id(dependency)] = index
            extend(dependency, index, seen)

    for step in steps:
        if step.deferred and id(step) != requested:
            extend(step, last_use[id(step)], set())


def _reuses_without_record(step: Step[Any], run: Run) -> bool:
    """True when a run would print `reused` for a step that keeps no record."""
    if type(step).recorded or not type(step).reusable:
        return False
    return all(id(item) in run.reused for item in step.dependencies())


def _dry_start_deferred(step: Step[Any], marks: dict[int, str]) -> None:
    """Mark the deferred steps `step` would start, without building them."""
    for dependency in step.dependencies():
        if not dependency.deferred or marks.get(id(dependency)) == "run":
            continue
        _dry_start_deferred(dependency, marks)
        marks[id(dependency)] = "run"


def _kept(
    step: Step[Any],
    run: Run,
    live: list[Step[Any]],
    value: object,
    *,
    built: bool,
    step_fingerprint: str,
    requested: int | None,
    run_lock: HeldLock | None,
) -> tuple[object, str]:
    run._values[id(step)] = value
    live.append(step)
    if built:
        value = step.publish(run, value)
        run._values[id(step)] = value
    step.report(value)
    output = _output_fingerprint(step, value, step_fingerprint)
    _remember(step, step_fingerprint, output, value)
    if id(step) == requested:
        # A served result, such as a dev server, outlives the run and must not
        # keep the lock.
        if run_lock is not None:
            sweep_stale_state(project_root())
            run_lock.release()
        step.serve(value)
    return value, output


# (spec) Deferred steps
def _start_deferred(
    step: Step[Any], run: Run, live: list[Step[Any]], started: set[int]
) -> None:
    """Build the deferred steps `step` takes, just before `step` builds."""
    for dependency in step.dependencies():
        if not dependency.deferred or id(dependency) in started:
            continue
        _start_deferred(dependency, run, live, started)
        try:
            announce(dependency)
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


def _reusable(step: Step[Any]) -> bool:
    return bool(step.variable) and type(step).reusable and type(step).recorded


# (spec) Reusing a step
def _matching(step: Step[Any], step_fingerprint: str) -> dict[str, Any] | None:
    if not _reusable(step):
        return None
    record = load_records(step.build_dir, project_root()).get(step.variable or "")
    if not isinstance(record, dict) or record.get("fingerprint") != step_fingerprint:
        return None
    if not step.matches(record.get("value")):
        return None
    return record


# (spec) Records
def _remember(step: Step[Any], step_fingerprint: str, output: str, value: object) -> None:
    if not _reusable(step):
        return
    root = project_root()
    records = load_records(step.build_dir, root)
    if step.build_variables is not None:
        records = {key: item for key, item in records.items() if key in step.build_variables}
    assert step.variable is not None
    records[step.variable] = {
        "fingerprint": step_fingerprint,
        "output": output,
        "value": step.encode(value),
    }
    save_records(step.build_dir, records, root)


# (spec) Step hooks
def _output_fingerprint(step: Step[Any], value: object, step_fingerprint: str) -> str:
    if not type(step).recorded:
        return step_fingerprint
    return step.output_fingerprint(step_fingerprint, value)


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
