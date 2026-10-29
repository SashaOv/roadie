"""One run at a time for a project root."""

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ValidationError

from roadie.engine.errors import RoadieError

_STALE_AFTER = timedelta(hours=24)


class _LockFile(BaseModel):
    pid: int
    started: str


class HeldLock:
    def __init__(self, path: Path, token: bytes) -> None:
        self.path = path
        self._token = token
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            current = self.path.read_bytes()
        except FileNotFoundError:
            return
        if current != self._token:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


def lock_path(root: Path) -> Path:
    return root / "target" / "roadie" / "run.lock"


# (spec) Command line
def clean_project(root: Path) -> None:
    """Take the run lock, delete `target/roadie/`, and return."""
    held = acquire_run_lock(root)
    try:
        shutil.rmtree(root / "target" / "roadie")
    finally:
        held.release()


# (spec) One run at a time
def acquire_run_lock(root: Path) -> HeldLock:
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        token = _token()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _take_over(path):
                continue
            holder = _read(path)
            if holder is None:
                continue
            raise RoadieError(
                f"another roadie run holds {path} (pid {holder.pid}, started {holder.started})"
            )
        else:
            with os.fdopen(fd, "wb") as file:
                file.write(token)
            return HeldLock(path, token)


def _token() -> bytes:
    started = _stamp(datetime.now(UTC))
    payload = {"pid": os.getpid(), "started": started}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read(path: Path) -> _LockFile | None:
    try:
        return _LockFile.model_validate(json.loads(path.read_text()))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
        return None


def _take_over(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return True
    holder = _parse(raw)
    if holder is not None and not _stale(holder):
        return False
    try:
        if path.read_bytes() != raw:
            return False
        path.unlink()
    except FileNotFoundError:
        return True
    return True


def _parse(raw: bytes) -> _LockFile | None:
    try:
        return _LockFile.model_validate(json.loads(raw))
    except (UnicodeError, json.JSONDecodeError, ValidationError):
        return None


def _stale(holder: _LockFile) -> bool:
    if not _process_alive(holder.pid):
        return True
    started = _parse_time(holder.started)
    if started is None:
        return True
    return datetime.now(UTC) - started > _STALE_AFTER


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes

    # WinDLL exists on Windows; the other platforms never call this.
    library = getattr(ctypes, "WinDLL", None)
    if library is None:
        return False
    kernel = library("kernel32", use_last_error=True)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    kernel.CloseHandle(handle)
    return True
