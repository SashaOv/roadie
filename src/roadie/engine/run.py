"""Build a plan and tear down each step after every step that takes it."""

import contextvars
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values

from roadie.engine.display import announce, format_elapsed, format_step, report_built
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.fingerprint import fingerprint
from roadie.engine.inputs import run_scope
from roadie.engine.lock import HeldLock, acquire_run_lock
from roadie.engine.prefix import parallel_output, reset_prefix, set_prefix, unprefixed
from roadie.engine.process import (
    interrupt_on_terminate,
    interrupt_requested,
    request_stop,
    reset_interrupt,
)
from roadie.engine.records import (
    artifact_dir,
    load_records,
    save_records,
    sweep_stale_state,
)
from roadie.engine.root import project_root, use_project_root
from roadie.engine.step import Step

_records = threading.Lock()


class Run:
    def __init__(self, *, locked: bool = False) -> None:
        self._values: dict[int, object] = {}
        self.reused: set[int] = set()
        self.locked = locked
        self.started_at = 0.0
        self.finish_reported = False

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


def _report_run(run: Run) -> None:
    if run.finish_reported:
        return
    run.finish_reported = True
    with unprefixed():
        print(f"finished in {format_elapsed(time.monotonic() - run.started_at)}", flush=True)


def execute(
    steps: Sequence[Step[Any]],
    *,
    root: Path,
    locked: bool = False,
    env: Sequence[Path] = (),
    dry: bool = False,
    jobs: int = 1,
    targets: Sequence[Step[Any]] | None = None,
) -> object:
    if jobs < 1:
        raise RoadieError("--jobs must be at least 1")
    with use_project_root(root):
        held = acquire_run_lock(root)
        try:
            return _run_steps(
                steps,
                locked=locked,
                env=env,
                run_lock=held,
                dry=dry,
                jobs=jobs,
                targets=targets,
            )
        finally:
            held.release()


def _run_steps(
    steps: Sequence[Step[Any]],
    *,
    locked: bool = False,
    env: Sequence[Path] = (),
    run_lock: HeldLock | None = None,
    dry: bool = False,
    jobs: int = 1,
    targets: Sequence[Step[Any]] | None = None,
) -> object:
    _apply_env(steps, env)
    run = Run(locked=locked)
    run.started_at = time.monotonic()
    live: list[Step[Any]] = []
    if targets is None:
        requested = {id(steps[-1])} if steps else set()
    else:
        requested = {id(step) for step in targets}
    blockers = _blockers(steps, requested)
    outputs: dict[int, str] = {}
    started: set[int] = set()
    marks: dict[int, str] = {}
    finished: set[int] = set()
    running: set[int] = set()
    failures: list[BaseException] = []
    interrupted: list[bool] = []
    present = {id(step) for step in steps}
    prerequisites = {
        id(step): {id(item) for item in step.dependencies() if id(item) in present}
        for step in steps
    }
    guards = {id(step): threading.Lock() for step in steps if step.deferred}
    schedule = threading.Condition()
    reset_interrupt()
    succeeded = False
    with run_scope(), interrupt_on_terminate():
        try:
            value = _schedule(
                steps,
                run=run,
                live=live,
                outputs=outputs,
                started=started,
                marks=marks,
                finished=finished,
                running=running,
                failures=failures,
                interrupted=interrupted,
                prerequisites=prerequisites,
                blockers=blockers,
                guards=guards,
                schedule=schedule,
                requested=requested,
                run_lock=run_lock,
                dry=dry,
                jobs=jobs,
            )
            if interrupted:
                raise KeyboardInterrupt
            if failures:
                raise failures[0]
            if dry:
                for step in steps:
                    print(f"{format_step(step)}  # {marks[id(step)]}", flush=True)
            else:
                for step in steps:
                    if step.deferred and id(step) not in requested and id(step) not in started:
                        name = step.name or type(step).__name__
                        print(f"not started {name}: no step that takes it runs", flush=True)
            if dry:
                return None
            succeeded = True
        finally:
            if not dry:
                with schedule:
                    expired = _take_live(run, live)
                _teardown(expired)
            reset_interrupt()
    if succeeded:
        _report_run(run)
    return value


def _schedule(
    steps: Sequence[Step[Any]],
    *,
    run: Run,
    live: list[Step[Any]],
    outputs: dict[int, str],
    started: set[int],
    marks: dict[int, str],
    finished: set[int],
    running: set[int],
    failures: list[BaseException],
    interrupted: list[bool],
    prerequisites: dict[int, set[int]],
    blockers: dict[int, set[int]],
    guards: dict[int, threading.Lock],
    schedule: threading.Condition,
    requested: set[int],
    run_lock: HeldLock | None,
    dry: bool,
    jobs: int,
) -> object:
    result: list[object] = []

    def loop(*, propagate: bool) -> None:
        token = None

        def end_prefix() -> None:
            nonlocal token
            if token is not None:
                sys.stdout.flush()
                reset_prefix(token)
                token = None

        while True:
            with schedule:
                if failures:
                    return
                step = _next_ready(steps, finished, running, prerequisites, requested)
                if step is None:
                    if running:
                        schedule.wait()
                        continue
                    if any(id(item) not in finished for item in steps):
                        raise RoadieError("cannot schedule the plan")
                    return
                running.add(id(step))
            token = set_prefix(step.name or type(step).__name__) if jobs > 1 else None
            try:
                try:
                    _execute_step(
                        step,
                        run=run,
                        live=live,
                        outputs=outputs,
                        started=started,
                        marks=marks,
                        guards=guards,
                        schedule=schedule,
                        requested=requested,
                        run_lock=run_lock,
                        dry=dry,
                    )
                except KeyboardInterrupt:
                    end_prefix()
                    request_stop()
                    with schedule:
                        running.discard(id(step))
                        finished.add(id(step))
                        interrupted.append(True)
                        schedule.notify_all()
                    if propagate:
                        raise
                    return
                except BaseException as error:  # noqa: BLE001 — SystemExit is a step failure
                    end_prefix()
                    failure = (
                        error
                        if isinstance(error, StepFailed)
                        else StepFailed(step.name or type(step).__name__, _reason(error))
                    )
                    with schedule:
                        names = [
                            item.name or type(item).__name__
                            for item in steps
                            if id(item) in running and item is not step
                        ]
                        running.discard(id(step))
                        finished.add(id(step))
                        if not failures:
                            if names:
                                print(f"waiting for {', '.join(names)}", flush=True)
                            failures.append(failure)
                        else:
                            print(failure, file=sys.stderr)
                        schedule.notify_all()
                    if propagate:
                        raise failure
                    return
                else:
                    with schedule:
                        running.discard(id(step))
                        finished.add(id(step))
                        if id(step) in requested and id(step) in run._values:
                            result.append(run._values[id(step)])
                        expired = [] if failures else _take_due(run, live, blockers, finished)
                        schedule.notify_all()
                    _teardown(expired)
            finally:
                end_prefix()

    with parallel_output() if jobs > 1 else nullcontext():
        if jobs == 1:
            loop(propagate=True)
            return result[-1] if result else None
        threads = [
            threading.Thread(
                target=contextvars.copy_context().run,
                args=(loop,),
                kwargs={"propagate": False},
            )
            for _ in range(jobs)
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            request_stop()
            for thread in threads:
                thread.join()
            raise
        if interrupted:
            raise KeyboardInterrupt
        return result[-1] if result else None


def _depends_on(
    step_id: int,
    target: int,
    prerequisites: dict[int, set[int]],
    seen: set[int],
) -> bool:
    if step_id in seen:
        return False
    seen.add(step_id)
    dependencies = prerequisites.get(step_id, set())
    if target in dependencies:
        return True
    return any(
        _depends_on(dependency, target, prerequisites, seen) for dependency in dependencies
    )


def _next_ready(
    steps: Sequence[Step[Any]],
    finished: set[int],
    running: set[int],
    prerequisites: dict[int, set[int]],
    requested: set[int],
) -> Step[Any] | None:
    for step in steps:
        if id(step) in finished or id(step) in running:
            continue
        if (
            id(step) in requested
            and type(step).once_per_run
            and any(
                id(other) not in finished
                and other is not step
                and not _depends_on(id(other), id(step), prerequisites, set())
                for other in steps
            )
        ):
            continue
        if prerequisites[id(step)].issubset(finished):
            return step
    return None


def _execute_step(
    step: Step[Any],
    *,
    run: Run,
    live: list[Step[Any]],
    outputs: dict[int, str],
    started: set[int],
    marks: dict[int, str],
    guards: dict[int, threading.Lock],
    schedule: threading.Condition,
    requested: set[int],
    run_lock: HeldLock | None,
    dry: bool,
) -> None:
    try:
        _run_one(
            step,
            run=run,
            live=live,
            outputs=outputs,
            started=started,
            marks=marks,
            guards=guards,
            schedule=schedule,
            requested=requested,
            run_lock=run_lock,
            dry=dry,
        )
    except KeyboardInterrupt:
        raise
    except StepFailed:
        if interrupt_requested():
            raise KeyboardInterrupt
        raise
    except BaseException as error:
        if interrupt_requested():
            raise KeyboardInterrupt from error
        raise StepFailed(step.name or type(step).__name__, _reason(error)) from error


def _run_one(
    step: Step[Any],
    *,
    run: Run,
    live: list[Step[Any]],
    outputs: dict[int, str],
    started: set[int],
    marks: dict[int, str],
    guards: dict[int, threading.Lock],
    schedule: threading.Condition,
    requested: set[int],
    run_lock: HeldLock | None,
    dry: bool,
) -> None:
    if step.deferred and id(step) not in requested:
        # A deferred step's output fingerprint is its step fingerprint, so a
        # consumer can be reused without it. The consumer builds it.
        step_fingerprint = fingerprint(step, outputs=outputs)
        with schedule:
            outputs[id(step)] = step_fingerprint
            if dry:
                marks[id(step)] = "not started"
        return
    step_fingerprint = fingerprint(step, outputs=outputs)
    record = _matching(step, step_fingerprint)
    if record is not None and dry:
        with schedule:
            run.reused.add(id(step))
            marks[id(step)] = "reuse"
            outputs[id(step)] = str(record["output"])
    elif record is not None:
        value = step.decode(record["value"])
        with schedule:
            run.reused.add(id(step))
        print(f"reused {step.name or type(step).__name__}", flush=True)
        _value, output = _kept(
            step, run, live, value, built=False,
            step_fingerprint=step_fingerprint, requested=requested, run_lock=run_lock,
            schedule=schedule,
        )
        with schedule:
            outputs[id(step)] = output
    elif dry and _reuses_without_record(step, run):
        with schedule:
            run.reused.add(id(step))
            marks[id(step)] = "reuse"
            outputs[id(step)] = step_fingerprint
    elif dry:
        _dry_start_deferred(step, marks)
        with schedule:
            marks[id(step)] = "run"
            outputs[id(step)] = "dry"
    else:
        _start_deferred(step, run, live, started, guards, schedule)
        announce(step)
        began = time.monotonic() if type(step).recorded else None
        value = step.build(run)
        elapsed = None if began is None else time.monotonic() - began
        _value, output = _kept(
            step, run, live, value, built=True,
            step_fingerprint=step_fingerprint, requested=requested, run_lock=run_lock,
            schedule=schedule, elapsed=elapsed,
        )
        with schedule:
            if step.deferred:
                started.add(id(step))
            outputs[id(step)] = output


def _blockers(steps: Sequence[Step[Any]], requested: set[int]) -> dict[int, set[int]]:
    present = {id(step) for step in steps}
    blockers = {id(step): {id(step)} for step in steps}
    for step in steps:
        for dependency in step.dependencies():
            if id(dependency) in present:
                blockers[id(dependency)].add(id(step))

    def extend(step: Step[Any], holders: set[int], seen: set[int]) -> None:
        for dependency in step.dependencies():
            if id(dependency) not in present or id(dependency) in seen:
                continue
            seen.add(id(dependency))
            blockers[id(dependency)].update(holders)
            extend(dependency, holders, seen)

    for step in steps:
        if step.deferred and id(step) not in requested:
            extend(step, set(blockers[id(step)]), set())
    return blockers


def _take_due(
    run: Run,
    live: list[Step[Any]],
    blockers: dict[int, set[int]],
    finished: set[int],
) -> list[tuple[Step[Any], object]]:
    due = [step for step in live if blockers[id(step)].issubset(finished)]
    for step in due:
        live.remove(step)
    return [(step, run._values.pop(id(step))) for step in reversed(due)]


def _take_live(run: Run, live: list[Step[Any]]) -> list[tuple[Step[Any], object]]:
    expired = [(step, run._values.pop(id(step))) for step in reversed(live)]
    live.clear()
    return expired


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
    requested: set[int],
    run_lock: HeldLock | None,
    schedule: threading.Condition,
    elapsed: float | None = None,
) -> tuple[object, str]:
    with schedule:
        run._values[id(step)] = value
        live.append(step)
    if built:
        value = step.publish(run, value)
        with schedule:
            run._values[id(step)] = value
    step.report(value)
    output = _output_fingerprint(step, value, step_fingerprint)
    _remember(step, step_fingerprint, output, value)
    if elapsed is not None:
        report_built(step, elapsed)
    if id(step) in requested and (
        type(step).serve is not Step.serve or requested.issubset({id(step)})
    ):
        # A served result, such as a dev server, outlives the run and must not
        # keep the lock. The total is printed first, so a server does not hide it.
        # With several results, release when the one that serves is ready, or
        # when the only requested result is kept.
        if run_lock is not None:
            sweep_stale_state(project_root())
            run_lock.release()
        if type(step).serve is not Step.serve:
            _report_run(run)
        step.serve(value)
    return value, output


# (spec) Deferred steps
def _start_deferred(
    step: Step[Any],
    run: Run,
    live: list[Step[Any]],
    started: set[int],
    guards: dict[int, threading.Lock],
    schedule: threading.Condition,
) -> None:
    """Build the deferred steps `step` takes, just before `step` builds.

    A deferred step starts once. A consumer that needs it while it starts waits.
    """
    for dependency in step.dependencies():
        if not dependency.deferred:
            continue
        _start_one(dependency, run, live, started, guards, schedule)


def _start_one(
    dependency: Step[Any],
    run: Run,
    live: list[Step[Any]],
    started: set[int],
    guards: dict[int, threading.Lock],
    schedule: threading.Condition,
) -> None:
    _start_deferred(dependency, run, live, started, guards, schedule)
    with guards[id(dependency)]:
        if id(dependency) in started:
            return
        try:
            announce(dependency)
            began = time.monotonic() if type(dependency).recorded else None
            value = dependency.build(run)
            elapsed = None if began is None else time.monotonic() - began
        except (KeyboardInterrupt, StepFailed):
            raise
        except BaseException as error:
            raise StepFailed(
                dependency.name or type(dependency).__name__, _reason(error)
            ) from error
        if elapsed is not None:
            report_built(dependency, elapsed)
        with schedule:
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
    with _records:
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
    with _records:
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
def _teardown(expired: list[tuple[Step[Any], object]]) -> None:
    errors: list[StepFailed] = []
    for step, value in expired:
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
