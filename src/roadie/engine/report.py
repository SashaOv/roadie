"""Append a run report: one JSON object per line, flushed as it happens."""

import json
import shutil
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from roadie.engine.root import project_root


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# (spec) Run report
class Report:
    """Append JSON events to one file. A write or open failure warns once."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self._failed = False
        try:
            self._file = path.open("a", encoding="utf-8", newline="\n")
        except OSError as error:
            self._fail(error)

    def run_start(self, results: list[str], jobs: int) -> None:
        self._emit("run-start", results=results, jobs=jobs)

    def step_start(self, step: str, running: list[str]) -> None:
        self._emit("step-start", step=step, running=running)

    def step_end(self, step: str, outcome: str, elapsed: float, running: list[str]) -> None:
        self._emit(
            "step-end",
            step=step,
            outcome=outcome,
            elapsed=elapsed,
            running=running,
        )

    def run_end(self, outcome: str, elapsed: float) -> None:
        self._emit("run-end", outcome=outcome, elapsed=elapsed)

    def close(self) -> None:
        with self._lock:
            handle = self._file
            self._file = None
        if handle is not None:
            handle.close()

    def _emit(self, event: str, **fields: object) -> None:
        with self._lock:
            if self._failed or self._file is None:
                return
            try:
                usage = shutil.disk_usage(project_root())
                payload: dict[str, object] = {
                    "event": event,
                    "time": _timestamp(),
                    "free": usage.free,
                }
                if event == "run-start":
                    payload["total"] = usage.total
                payload.update(fields)
                self._file.write(json.dumps(payload) + "\n")
                self._file.flush()
            except OSError as error:
                self._fail(error)

    def _fail(self, error: OSError) -> None:
        if self._failed:
            return
        self._failed = True
        print(
            f"cannot write report {self._path}: {error}; continuing without it",
            file=sys.stderr,
            flush=True,
        )
