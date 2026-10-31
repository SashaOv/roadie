"""One run at a time for a project root."""

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ValidationError

from roadie.engine.errors import RoadieError

_LOCK_BYTES = 1
RUN_PID_VARIABLE = "ROADIE_RUN_PID"


class _LockFile(BaseModel):
    pid: int
    started: str


class HeldLock:
    def __init__(self, path: Path, file: BinaryIO) -> None:
        self.path = path
        self._file = file
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        _unlock(self._file)
        self._file.close()


def lock_path(root: Path) -> Path:
    return root / "target" / "roadie" / "run.lock"


# (spec) Command line
def clean_project(root: Path) -> None:
    """Take the run lock, delete `target/roadie/` except the lock, and return."""
    held = acquire_run_lock(root)
    try:
        state = root / "target" / "roadie"
        for child in list(state.iterdir()):
            if child.name == "run.lock":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    finally:
        held.release()


# (spec) One run at a time
def acquire_run_lock(root: Path) -> HeldLock:
    """Lock `run.lock`, waiting when another run holds it.

    The operating system releases the lock when the holder exits. The file
    stays, so the next run locks the same file.
    """
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        if not _lock(file, blocking=False):
            _refuse_parent(path)
            _announce(path)
            _lock(file, blocking=True)
        _write_token(file)
    except BaseException:
        file.close()
        raise
    return HeldLock(path, file)


def _refuse_parent(path: Path) -> None:
    holder = _read(path)
    parent = _parent_run_pid()
    if holder is None or parent is None or holder.pid != parent:
        return
    raise RoadieError(
        f"{path} is held by the roadie run that started this one (pid {holder.pid})"
    )


def _parent_run_pid() -> int | None:
    raw = os.environ.get(RUN_PID_VARIABLE)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _announce(path: Path) -> None:
    holder = _read(path)
    if holder is None:
        print(f"waiting for another roadie run: {path}", flush=True)
        return
    print(
        f"waiting for another roadie run: {path} (pid {holder.pid}, started {holder.started})",
        flush=True,
    )


def _write_token(file: BinaryIO) -> None:
    started = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {"pid": os.getpid(), "started": started}
    file.seek(0)
    file.truncate()
    file.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    file.flush()


def _read(path: Path) -> _LockFile | None:
    try:
        return _LockFile.model_validate(json.loads(path.read_text()))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
        return None


if sys.platform == "win32":
    import msvcrt

    def _lock(file: BinaryIO, *, blocking: bool) -> bool:
        while True:
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
            except OSError:
                if not blocking:
                    return False
                try:
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, _LOCK_BYTES)
                except OSError:
                    continue
                return True
            return True

    def _unlock(file: BinaryIO) -> None:
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
        except OSError:
            return

else:
    import fcntl

    def _lock(file: BinaryIO, *, blocking: bool) -> bool:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(file.fileno(), flags)
        except BlockingIOError:
            if blocking:
                raise
            return False
        return True

    def _unlock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
