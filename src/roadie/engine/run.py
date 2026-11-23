"""Build a plan and tear down each step after every step that takes it."""

import contextvars
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values

from roadie.engine.display import announce, format_elapsed, format_step, report_built
from roadie.engine.enforce import (
    built_files,
    declared_files,
    listed_project_files,
    undeclared_reads,
)
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.fingerprint import fingerprint
from roadie.engine.inputs import run_scope
from roadie.engine.lock import HeldLock, acquire_run_lock
from roadie.engine.parameters import (
    ParameterValues,
    bind_parameters,
    current_step,
    reset_parameters,
)
from roadie.engine.prefix import parallel_output, reset_prefix, set_prefix, unprefixed
from roadie.engine.process import (
    TraceCollector,
    collect_traces,
    command_context,
    interrupt_on_terminate,
    interrupt_requested,
    request_stop,
    reset_interrupt,
    set_before_stop,
)
from roadie.engine.records import (
    artifact_dir,
    load_records,
    save_records,
    sweep_stale_state,
)
from roadie.engine.report import Report
from roadie.engine.root import project_root, use_project_root
from roadie.engine.step import Step
from roadie.system import tracing_error

_records = threading.Lock()


class Run:
    def __init__(
        self,
        *,
        locked: bool = False,
        environments: dict[int, dict[str, str]] | None = None,
    ) -> None:
        self._values: dict[int, object] = {}
        self._environments: dict[int, dict[str, str]] = environments or {}
        self.reused: set[int] = set()
        self.locked = locked
        self._artifacts: dict[int, list[str]] = {}
        self._artifact_lock = threading.Lock()

    # (spec) Step values
    def environment(self, step: Step[Any]) -> dict[str, str]:
        """Return the environment-file values the run read for `step`."""
        return self._environments.get(id(step), {})

    # (spec) Step values
    def __getitem__[T](self, step: Step[T]) -> T:
        if id(step) not in self._values:
            raise RoadieError(
                f"{step._label()} is not available in this run"
            )
        return cast(T, self._values[id(step)])

    # (spec) Artifacts
    def artifact(self, step: Step[Any], suffix: str) -> Path:
        root = project_root()
        stem = step.variable or step._label()
        destination = root / "target" / "roadie" / artifact_dir(step.build_dir, root) / f"{stem}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._artifact_lock:
            names = self._artifacts.setdefault(id(step), [])
            if destination.name not in names:
                names.append(destination.name)
        return destination

    def _artifact_names(self, step: Step[Any]) -> list[str]:
        with self._artifact_lock:
            return list(self._artifacts.get(id(step), ()))


@dataclass(frozen=True)
class _Plan:
    steps: tuple[Step[Any], ...]
    requested: frozenset[int]
    leading: int
    jobs: int
    dry: bool
    prerequisites: dict[int, set[int]]
    blockers: dict[int, set[int]]

    @classmethod
    def build(
        cls,
        steps: Sequence[Step[Any]],
        *,
        requested: set[int],
        leading: int,
        jobs: int,
        dry: bool,
    ) -> "_Plan":
        present = {id(step) for step in steps}
        prerequisites = {
            id(step): {id(item) for item in step.dependencies() if id(item) in present}
            for step in steps
        }
        return cls(
            steps=tuple(steps),
            requested=frozenset(requested),
            leading=leading,
            jobs=jobs,
            dry=dry,
            prerequisites=prerequisites,
            blockers=_blockers(steps, requested),
        )

    def next_ready(self, finished: set[int], running: set[int]) -> Step[Any] | None:
        if any(type(item).asks for item in self.steps if id(item) in running):
            return None
        prefix_done = all(id(step) in finished for step in self.steps[: self.leading])
        for index, step in enumerate(self.steps):
            if id(step) in finished or id(step) in running:
                continue
            if type(step).asks and running:
                continue
            if index < self.leading:
                if any(id(earlier) not in finished for earlier in self.steps[:index]):
                    continue
            elif not prefix_done:
                continue
            if (
                id(step) in self.requested
                and type(step).once_per_run
                and any(
                    id(other) not in finished
                    and other is not step
                    and not self._depends_on(id(other), id(step), set())
                    for other in self.steps
                )
            ):
                continue
            if self.prerequisites[id(step)].issubset(finished):
                return step
        return None

    def _depends_on(self, step_id: int, target: int, seen: set[int]) -> bool:
        if step_id in seen:
            return False
        seen.add(step_id)
        dependencies = self.prerequisites.get(step_id, set())
        if target in dependencies:
            return True
        return any(
            self._depends_on(dependency, target, seen) for dependency in dependencies
        )


class _Live:
    """The steps a run has built and not yet torn down, in start order."""

    def __init__(self, run: Run, blockers: dict[int, set[int]]) -> None:
        self._run = run
        self._blockers = blockers
        self._steps: list[Step[Any]] = []

    def add(self, step: Step[Any], value: object) -> None:
        self._run._values[id(step)] = value
        self._steps.append(step)

    def replace(self, step: Step[Any], value: object) -> None:
        self._run._values[id(step)] = value

    def take_due(self, finished: set[int]) -> list[tuple[Step[Any], object]]:
        due = [step for step in self._steps if self._blockers[id(step)].issubset(finished)]
        for step in due:
            self._steps.remove(step)
        return [(step, self._run._values.pop(id(step))) for step in reversed(due)]

    def take_all(self) -> list[tuple[Step[Any], object]]:
        expired = [(step, self._run._values.pop(id(step))) for step in reversed(self._steps)]
        self._steps.clear()
        return expired

    def serving(self) -> list[Step[Any]]:
        return [step for step in self._steps if step.serves]

    def __contains__(self, step: Step[Any]) -> bool:
        return id(step) in self._run._values


def execute(
    steps: Sequence[Step[Any]],
    *,
    root: Path,
    locked: bool = False,
    env: Sequence[Path] = (),
    dry: bool = False,
    jobs: int = 1,
    targets: Sequence[Step[Any]] | None = None,
    parameters: ParameterValues | None = None,
    leading: int = 0,
    report: Path | None = None,
    enforce: bool = False,
) -> object:
    if jobs < 1:
        raise RoadieError("--jobs must be at least 1")
    if enforce and dry:
        raise RoadieError("--enforce does not apply to --dry")
    if enforce:
        refusal = tracing_error()
        if refusal is not None:
            raise RoadieError(refusal)
    _one_requested(targets if targets is not None else steps[-1:])
    with use_project_root(root):
        writer = Report(report) if report is not None else None
        try:
            held = acquire_run_lock(root)
            try:
                run = Run(locked=locked, environments=_environments(steps, env))
                if targets is None:
                    requested = {id(steps[-1])} if steps else set()
                    names = [steps[-1]._label()] if steps else []
                else:
                    requested = {id(step) for step in targets}
                    names = [step._label() for step in targets]
                plan = _Plan.build(
                    steps, requested=requested, leading=leading, jobs=jobs, dry=dry
                )
                live = _Live(run, plan.blockers)
                scheduler = _Scheduler(plan, run, live, run_lock=held, report=writer, enforce=enforce)
                return _run_steps(scheduler, parameters, names)
            finally:
                held.release()
        finally:
            if writer is not None:
                writer.close()


def _run_steps(
    scheduler: "_Scheduler",
    parameters: ParameterValues | None,
    names: list[str],
) -> object:
    plan = scheduler.plan
    steps = plan.steps
    live = scheduler.live
    report = scheduler.report
    dry = plan.dry
    requested = plan.requested
    reset_interrupt()
    succeeded = False
    outcome = "succeeded"
    reported_run = False
    previous_before_stop = set_before_stop(scheduler.announce_interrupt)
    try:
        with run_scope(), interrupt_on_terminate():
            bound = None
            try:
                bound = bind_parameters(
                    steps,
                    parameters if parameters is not None else ParameterValues({}, {}),
                )
                if report is not None:
                    report.run_start(names, plan.jobs)
                    reported_run = True
                value = scheduler.schedule()
                if scheduler.interrupted:
                    outcome = "interrupted"
                    raise KeyboardInterrupt
                if scheduler.failures:
                    outcome = "failed"
                    raise scheduler.failures[0]
                if scheduler.undeclared:
                    outcome = "failed"
                    names = [
                        step._label()
                        for step in steps
                        if id(step) in scheduler.undeclared
                    ]
                    raise RoadieError(
                        f"undeclared reads in {len(names)} steps: {', '.join(names)}"
                    )
                if dry:
                    for step in steps:
                        print(f"{format_step(step)}  # {scheduler.marks[id(step)]}", flush=True)
                else:
                    for step in steps:
                        if not step.deferred or id(step) in requested | scheduler.started:
                            continue
                        name = step._label()
                        print(f"not started {name}: no step that takes it runs", flush=True)
                if dry:
                    return None
                succeeded = True
            except KeyboardInterrupt:
                outcome = "interrupted"
                scheduler.announce_interrupt()
                raise
            except BaseException:
                if outcome == "succeeded":
                    outcome = "failed"
                raise
            finally:
                try:
                    if not dry:
                        with scheduler.condition:
                            expired = live.take_all()
                        _teardown(expired)
                except KeyboardInterrupt:
                    outcome = "interrupted"
                    raise
                except BaseException:
                    if outcome == "succeeded":
                        outcome = "failed"
                    raise
                finally:
                    if report is not None and reported_run:
                        report.run_end(outcome, time.monotonic() - scheduler.started_at)
                    if bound is not None:
                        reset_parameters(bound)
                    reset_interrupt()
    except KeyboardInterrupt:
        scheduler.announce_interrupt()
        raise
    finally:
        set_before_stop(previous_before_stop)
    if succeeded:
        scheduler._report_run()
    return value


class _Scheduler:
    """Hold one run's scheduling state and build its steps."""

    def __init__(
        self,
        plan: _Plan,
        run: Run,
        live: _Live,
        *,
        run_lock: HeldLock | None,
        report: Report | None,
        enforce: bool = False,
    ) -> None:
        self.plan = plan
        self.run = run
        self.live = live
        self.enforce = enforce
        self.traces: dict[int, TraceCollector] = {}
        self.undeclared: dict[int, list[str]] = {}
        self._listed: set[Path] | None = None
        self.run_lock = run_lock
        self.condition = threading.Condition(threading.RLock())
        self.finished: set[int] = set()
        self.running: set[int] = set()
        self.failures: list[BaseException] = []
        self.interrupted = False
        self.outputs: dict[int, str] = {}
        self.started: set[int] = set()
        self.marks: dict[int, str] = {}
        self.guards = {
            id(step): threading.Lock() for step in plan.steps if step.deferred
        }
        self.result: list[object] = []
        self.report = report
        self._report_began: dict[int, float] = {}
        self.started_at = time.monotonic()
        self.finish_reported = False
        self._announced = False
        self._announce_lock = threading.RLock()

    def _report_start(self, step: Step[Any]) -> None:
        if self.report is None or id(step) in self._report_began:
            return
        self._report_began[id(step)] = time.monotonic()
        self.report.step_start(step._label())

    def _report_end(self, step: Step[Any], outcome: str) -> None:
        if self.report is None or id(step) not in self._report_began:
            return
        began = self._report_began.pop(id(step))
        self.report.step_end(step._label(), outcome, time.monotonic() - began)

    def _listed_files(self) -> set[Path]:
        if self._listed is None:
            self._listed = listed_project_files(project_root())
        return self._listed

    def _check(self, step: Step[Any]) -> list[str]:
        """Undeclared reads of `step`, or none when enforcement is off."""
        if not self.enforce:
            return []
        collector = self.traces.get(id(step))
        if collector is None or not collector.reads:
            return []
        with self.condition:
            values = dict(self.run._values)
        return undeclared_reads(
            reads=collector.reads,
            written=collector.written,
            declared=declared_files(step),
            built=built_files(values, step),
            listed=self._listed_files(),
            root=project_root(),
        )

    def _record_undeclared(self, step: Step[Any], missing: list[str]) -> None:
        print(
            f"{step._label()} reads files it does not declare: {', '.join(missing)}",
            flush=True,
        )
        with self.condition:
            self.undeclared[id(step)] = missing

    def _report_run(self) -> None:
        if self.finish_reported:
            return
        self.finish_reported = True
        with unprefixed():
            print(
                f"finished in {format_elapsed(time.monotonic() - self.started_at)}",
                flush=True,
            )

    def announce_interrupt(self) -> None:
        with self._announce_lock:
            if self._announced:
                return
            self._announced = True
            with self.condition:
                running_now = set(self.running)
                names = [
                    step._label() for step in self.plan.steps if id(step) in running_now
                ]
                names.extend(
                    step._label()
                    for step in self.live.serving()
                    if id(step) not in running_now
                )
        line = f"interrupted: stopping {', '.join(names)}" if names else "interrupted"
        with unprefixed():
            print(line, flush=True)

    def schedule(self) -> object:
        jobs = self.plan.jobs
        with parallel_output() if jobs > 1 else nullcontext():
            if jobs == 1:
                self._work(propagate=True)
                return self.result[-1] if self.result else None
            threads = [
                threading.Thread(
                    target=contextvars.copy_context().run,
                    args=(self._work,),
                    kwargs={"propagate": False},
                )
                for _ in range(jobs)
            ]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    while thread.is_alive():
                        thread.join(0.1)
            except KeyboardInterrupt:
                request_stop()
                for thread in threads:
                    while thread.is_alive():
                        thread.join(0.1)
                raise
            if self.interrupted:
                raise KeyboardInterrupt
            return self.result[-1] if self.result else None

    def _work(self, *, propagate: bool) -> None:
        """Claim and build ready steps until none is left or one fails."""
        token = None

        def end_prefix() -> None:
            nonlocal token
            if token is not None:
                sys.stdout.flush()
                reset_prefix(token)
                token = None

        while True:
            with self.condition:
                if self.failures:
                    return
                step = self.plan.next_ready(self.finished, self.running)
                if step is None:
                    if self.running:
                        self.condition.wait()
                        continue
                    if any(id(item) not in self.finished for item in self.plan.steps):
                        raise RoadieError("cannot schedule the plan")
                    return
                self.running.add(id(step))
                self.condition.notify_all()
                # Claim the other ready steps before this one starts, so both
                # step-start times are written before either step runs.
                while (
                    not self.failures
                    and len(self.running) < self.plan.jobs
                    and self.plan.next_ready(self.finished, self.running) is not None
                ):
                    self.condition.wait()
                if self.failures:
                    self.running.discard(id(step))
                    self.condition.notify_all()
                    return
            token = set_prefix(step._label()) if self.plan.jobs > 1 else None
            try:
                try:
                    self._execute_step(step)
                except KeyboardInterrupt:
                    self._report_end(step, "interrupted")
                    end_prefix()
                    request_stop()
                    with self.condition:
                        self.running.discard(id(step))
                        self.finished.add(id(step))
                        self.interrupted = True
                        self.condition.notify_all()
                    if propagate:
                        raise
                    return
                except BaseException as error:  # noqa: BLE001 — SystemExit is a step failure
                    self._report_end(step, "failed")
                    end_prefix()
                    failure = (
                        error
                        if isinstance(error, StepFailed)
                        else StepFailed(step._label(), _reason(error))
                    )
                    with self.condition:
                        names = [
                            item._label()
                            for item in self.plan.steps
                            if id(item) in self.running and item is not step
                        ]
                        self.running.discard(id(step))
                        self.finished.add(id(step))
                        if not self.failures:
                            if names:
                                print(f"waiting for {', '.join(names)}", flush=True)
                            self.failures.append(failure)
                        else:
                            print(failure, file=sys.stderr)
                        self.condition.notify_all()
                    if propagate:
                        raise failure
                    return
                else:
                    with self.condition:
                        self.running.discard(id(step))
                        self.finished.add(id(step))
                        if id(step) in self.plan.requested and step in self.live:
                            self.result.append(self.run[step])
                        expired = [] if self.failures else self.live.take_due(self.finished)
                        self.condition.notify_all()
                    _teardown(expired)
            finally:
                end_prefix()

    def _execute_step(self, step: Step[Any]) -> None:
        try:
            self._run_one(step)
        except KeyboardInterrupt:
            raise
        except StepFailed:
            if interrupt_requested():
                raise KeyboardInterrupt
            raise
        except BaseException as error:
            if interrupt_requested():
                raise KeyboardInterrupt from error
            raise StepFailed(step._label(), _reason(error)) from error

    def _invoke(self, step: Step[Any]) -> tuple[object, float | None]:
        announce(step)
        if type(step).recorded:
            self._report_start(step)
        began = time.monotonic() if type(step).recorded else None
        collector = TraceCollector() if self.enforce else None
        if collector is not None:
            self.traces[id(step)] = collector
        try:
            with (
                current_step(step),
                command_context(
                    inherit=_inherits_stdin(step, self.plan.requested),
                    environment=self.run.environment(step),
                ),
                collect_traces(collector) if collector is not None else nullcontext(),
            ):
                value = step.build(self.run)
        except BaseException as error:
            # A program can fail because Ctrl-C stopped it; that step was interrupted.
            stopped = isinstance(error, KeyboardInterrupt) or interrupt_requested()
            self._report_end(step, "interrupted" if stopped else "failed")
            if isinstance(error, (KeyboardInterrupt, StepFailed)):
                raise
            raise StepFailed(step._label(), _reason(error)) from error
        return value, None if began is None else time.monotonic() - began

    def _run_one(self, step: Step[Any]) -> None:
        step_fingerprint = fingerprint(step, outputs=self.outputs, env=self.run.environment(step))
        if step.deferred and id(step) not in self.plan.requested:
            # A deferred step's output fingerprint is its step fingerprint, so a
            # consumer can be reused without it. The consumer builds it.
            with self.condition:
                self.outputs[id(step)] = step_fingerprint
                if self.plan.dry:
                    self.marks[id(step)] = "not started"
            return
        record = None if self.enforce else _matching(step, step_fingerprint)
        if record is not None and self.plan.dry:
            with self.condition:
                self.run.reused.add(id(step))
                self.marks[id(step)] = "reuse"
                self.outputs[id(step)] = str(record["output"])
        elif record is not None:
            value = step.decode(record["value"])
            with self.condition:
                self.run.reused.add(id(step))
            self._report_start(step)
            print(f"reused {step._label()}", flush=True)
            _value, output = self._kept(
                step, value, built=False, step_fingerprint=step_fingerprint
            )
            with self.condition:
                self.outputs[id(step)] = output
        elif self.plan.dry and _reuses_without_record(step, self.run):
            with self.condition:
                self.run.reused.add(id(step))
                self.marks[id(step)] = "reuse"
                self.outputs[id(step)] = step_fingerprint
        elif self.plan.dry:
            _dry_start_deferred(step, self.marks)
            with self.condition:
                self.marks[id(step)] = "run"
                self.outputs[id(step)] = "dry"
        else:
            self._start_deferred(step)
            member_reused = step._reports_reused(self.run.reused)
            if member_reused:
                self._report_start(step)
            value, elapsed = self._invoke(step)
            _value, output = self._kept(
                step, value, built=True,
                step_fingerprint=step_fingerprint, elapsed=elapsed,
            )
            with self.condition:
                if step.deferred:
                    self.started.add(id(step))
                self.outputs[id(step)] = output

    def _kept(
        self, step: Step[Any], value: object, *, built: bool,
        step_fingerprint: str, elapsed: float | None = None,
    ) -> tuple[object, str]:
        with self.condition:
            self.live.add(step, value)
        if built:
            value = step.publish(self.run, value)
            with self.condition:
                self.live.replace(step, value)
        step.report(value)
        missing = self._check(step) if built else []
        output = _output_fingerprint(step, value, step_fingerprint)
        if not missing:
            _remember(step, step_fingerprint, output, value, run=self.run, built=built)
        if elapsed is not None:
            report_built(step, elapsed)
        if missing:
            self._record_undeclared(step, missing)
            self._report_end(step, "failed")
        elif not built or step._reports_reused(self.run.reused):
            self._report_end(step, "reused")
        elif elapsed is not None:
            self._report_end(step, "built")
        if id(step) in self.plan.requested and (
            step.serves or self.plan.requested.issubset({id(step)})
        ):
            # A served result, such as a dev server, outlives the run and must not
            # keep the lock. The total is printed first, so a server does not hide it.
            # With several results, release when the one that serves is ready, or
            # when the only requested result is kept.
            if self.run_lock is not None:
                sweep_stale_state(project_root())
                self.run_lock.release()
            if step.serves:
                self._report_run()
            step.serve(value)
        return value, output

    def _start_deferred(self, step: Step[Any]) -> None:
        """Build the deferred steps `step` takes, just before `step` builds.

        A deferred step starts once. A consumer that needs it while it starts waits.
        """
        for dependency in step.dependencies():
            if not dependency.deferred:
                continue
            self._start_one(dependency)

    def _start_one(self, dependency: Step[Any]) -> None:
        self._start_deferred(dependency)
        with self.guards[id(dependency)]:
            if id(dependency) in self.started:
                return
            value, elapsed = self._invoke(dependency)
            with self.condition:
                self.live.add(dependency, value)
                self.started.add(id(dependency))
            if elapsed is not None:
                report_built(dependency, elapsed)
            missing = self._check(dependency)
            if missing:
                self._record_undeclared(dependency, missing)
                self._report_end(dependency, "failed")
            else:
                self._report_end(dependency, "built")


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


# (spec) Deferred steps
def _inherits_stdin(step: Step[Any], requested: frozenset[int]) -> bool:
    """A service keeps the terminal only when it is a requested result."""
    return not step.serves or id(step) in requested


# (spec) Environment files
def _environments(
    steps: Sequence[Step[Any]], env: Sequence[Path]
) -> dict[int, dict[str, str]]:
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
    environments: dict[int, dict[str, str]] = {}
    for step in steps:
        merged: dict[str, str] = {}
        for path in (*step.env_files, *cli_paths):
            merged.update(read(path))
        environments[id(step)] = merged
    return environments


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
def _remember(
    step: Step[Any],
    step_fingerprint: str,
    output: str,
    value: object,
    *,
    run: Run,
    built: bool,
) -> None:
    if not _reusable(step):
        return
    root = project_root()
    with _records:
        records = load_records(step.build_dir, root)
        assert step.variable is not None
        previous = records.get(step.variable)
        if step.build_variables is not None:
            records = {key: item for key, item in records.items() if key in step.build_variables}
        if built:
            artifacts = run._artifact_names(step)
        else:
            stored = previous.get("artifacts") if isinstance(previous, dict) else None
            artifacts = (
                [name for name in stored if isinstance(name, str)]
                if isinstance(stored, list)
                else []
            )
        records[step.variable] = {
            "fingerprint": step_fingerprint,
            "output": output,
            "value": step.encode(value),
            "artifacts": artifacts,
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
                    step._label(), f"teardown: {_reason(error)}"
                )
            )
    if errors:
        if sys.exc_info()[1] is None:
            for failure in errors[1:]:
                print(failure, file=sys.stderr)
            raise errors[0]
        for failure in errors:
            print(failure, file=sys.stderr)


def _one_requested(targets: Sequence[Step[Any]]) -> None:
    limited: list[Step[Any]] = []
    for step in targets:
        if type(step).once_per_run and all(item is not step for item in limited):
            limited.append(step)
    if len(limited) < 2:
        return
    labels = [step._label() for step in limited]
    if len(labels) == 2:
        shown = f"{labels[0]} and {labels[1]}"
    else:
        shown = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    raise RoadieError(f"{shown} can each be requested only once per run; request one of them")
