"""Prefix each line of a step's output when several steps run at once."""

import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TextIO, cast

_prefix: ContextVar[str] = ContextVar("roadie_line_prefix", default="")
_sink: ContextVar["LineSink | None"] = ContextVar("roadie_line_sink", default=None)


class LineSink:
    """Write whole lines to the real standard streams, one line at a time."""

    def __init__(self, stdout: TextIO, stderr: TextIO) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self._lock = threading.Lock()
        self._pending: dict[int, str] = {}

    def emit(self, stream: TextIO, prefix: str, text: str) -> None:
        if not text:
            return
        if not text.endswith("\n"):
            text += "\n"
        with self._lock:
            stream.write(f"{prefix}{text}" if prefix else text)
            stream.flush()

    def write(self, text: str) -> int:
        """Buffer `text` from the calling thread and emit its complete lines."""
        if not text:
            return 0
        thread = threading.get_ident()
        prefix = _prefix.get()
        with self._lock:
            parts = (self._pending.pop(thread, "") + text).split("\n")
            self._pending[thread] = parts[-1]
            for line in parts[:-1]:
                self.stdout.write(f"{prefix}{line}\n" if prefix else f"{line}\n")
            if len(parts) > 1:
                self.stdout.flush()
        return len(text)

    def flush(self) -> None:
        thread = threading.get_ident()
        prefix = _prefix.get()
        with self._lock:
            rest = self._pending.pop(thread, "")
            if rest:
                self.stdout.write(f"{prefix}{rest}\n" if prefix else rest)
                self.stdout.flush()


class _Stdout:
    def __init__(self, sink: LineSink) -> None:
        self._sink = sink

    def write(self, text: str) -> int:
        return self._sink.write(text)

    def flush(self) -> None:
        self._sink.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str) -> object:
        return getattr(self._sink.stdout, name)


def current_prefix() -> str:
    return _prefix.get()


def current_sink() -> LineSink | None:
    return _sink.get()


def set_prefix(name: str) -> Token[str]:
    return _prefix.set(f"[{name}] ")


def reset_prefix(token: Token[str]) -> None:
    _prefix.reset(token)


def output_streams() -> tuple[TextIO, TextIO]:
    """The real standard output and error, beneath a parallel run's line sink."""
    sink = current_sink()
    if sink is None:
        return sys.stdout, sys.stderr
    return sink.stdout, sink.stderr


def read_lines(
    pull: Callable[[], str],
    stream: TextIO,
    prefix: str,
    parts: list[str],
    *,
    daemon: bool = False,
) -> threading.Thread:
    """Append each line from `pull` to `parts`, and print it live on `stream`."""
    sink = current_sink()

    def run() -> None:
        while True:
            line = pull()
            if line == "":
                return
            parts.append(line)
            if sink is not None:
                sink.emit(stream, prefix, line)
            else:
                stream.write(line)
                stream.flush()

    thread = threading.Thread(target=run, daemon=daemon)
    thread.start()
    return thread


@contextmanager
def parallel_output() -> Iterator[None]:
    """Send standard output through a line sink for the duration of a parallel run."""
    sink = LineSink(sys.stdout, sys.stderr)
    token = _sink.set(sink)
    previous = sys.stdout
    sys.stdout = cast(TextIO, _Stdout(sink))
    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stdout = previous
        _sink.reset(token)
