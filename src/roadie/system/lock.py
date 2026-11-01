"""Exclusive lock on an open file."""

import sys
from typing import BinaryIO

_LOCK_BYTES = 1
# Past any token the holder writes, so waiters can still read the file.
_LOCK_OFFSET = 2**30

if sys.platform == "win32":
    import msvcrt

    def lock(file: BinaryIO, *, blocking: bool) -> bool:
        while True:
            file.seek(_LOCK_OFFSET)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
            except OSError:
                if not blocking:
                    return False
                try:
                    file.seek(_LOCK_OFFSET)
                    msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, _LOCK_BYTES)
                except OSError:
                    continue
                return True
            return True

    def unlock(file: BinaryIO) -> None:
        file.seek(_LOCK_OFFSET)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
        except OSError:
            return

else:
    import fcntl

    def lock(file: BinaryIO, *, blocking: bool) -> bool:
        # flock ignores the offset; seek keeps one code path with Windows.
        file.seek(_LOCK_OFFSET)
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

    def unlock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
