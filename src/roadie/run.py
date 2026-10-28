"""Build a plan and tear down each step after its last consumer."""

import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from forge.environment import repository_root

from wright.cargo import cargo_snapshot
from wright.errors import StepFailed, WrightError
from wright.fingerprint import fingerprint
from wright.inputs import digest
from wright.output import publish
from wright.process import interrupt_on_terminate
from wright.records import artifact_dir, load_records, save_records
from wright.step import Binary, File, Service, Step, Tests
from wright.testing import TestResults


class Run:
    def __init__(self) -> None:
        self._values: dict[int, object] = {}
        self.reused: set[int] = set()

    def __getitem__[T](self, step: Step[T]) -> T:
        if id(step) not in self._values:
            raise WrightError(
                f"{step.name or type(step).__name__} is not available in this run"
            )
        return cast(T, self._values[id(step)])

    def artifact(self, step: Step[Any], suffix: str) -> Path:
        root = repository_root()
        stem = step.variable or f"{type(step).__name__}-{step.serial}"
        destination = root / "target" / "wright" / artifact_dir(step.build_dir, root) / f"{stem}{suffix}"
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
    outputs: dict[int, str] = {}
    with cargo_snapshot(), interrupt_on_terminate():
        try:
            for index, step in enumerate(steps):
                try:
                    step_fingerprint = fingerprint(step, outputs=outputs)
                    restored = _restore(step, step_fingerprint)
                    if restored is not None:
                        value = restored
                        run.reused.add(id(step))
                        print(f"reused {step.name or type(step).__name__}", flush=True)
                    else:
                        value = step.build(run)
                    run._values[id(step)] = value
                    live.append(step)
                    if restored is None and isinstance(step, File):
                        value = publish(step, cast(Path, value), locked=locked)
                        run._values[id(step)] = value
                    if isinstance(step, Tests):
                        _report_tests(step, value)
                    _remember(step, step_fingerprint, value)
                    outputs[id(step)] = _output_fingerprint(step, value, step_fingerprint, outputs)
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


def reuse_markers(steps: Sequence[Step[Any]]) -> list[bool]:
    """True when `--dry` can promise the step will not run."""
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


def _matching(step: Step[Any], step_fingerprint: str) -> dict[str, Any] | None:
    if not _reusable(step):
        return None
    record = load_records(step.build_dir, repository_root()).get(step.variable or "")
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


def _remember(step: Step[Any], step_fingerprint: str, value: object) -> None:
    if not _reusable(step):
        return
    root = repository_root()
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
            raise WrightError("A Tests step must return TestResults")
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
        raise WrightError("A Tests step must return TestResults")
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
    return repository_root() / path


def _relative(path: Path) -> str:
    resolved = path.resolve()
    root = repository_root().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    return relative.as_posix()


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
