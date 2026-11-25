"""Prefix each line of a step's output when several steps run at once."""

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import IO, TextIO, cast

_prefix: ContextVar[str] = ContextVar("roadie_line_prefix", default="")
_sink: ContextVar["LineSink | None"] = ContextVar("roadie_line_sink", default=None)


class LineSink:
    """Write whole lines to the real standard streams, one line at a time."""

    def __init__(self, stdout: TextIO, stderr: TextIO, *, line_limit: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self._line_limit = line_limit
        self._lock = threading.Lock()
        self._pending: dict[int, str] = {}

    def _write_line(self, stream: TextIO, prefix: str, text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"
        stream.write(f"{prefix}{text}")

    def emit(self, stream: TextIO, prefix: str, text: str) -> None:
        if text:
            with self._lock:
                self._write_line(stream, prefix, text)
                stream.flush()

    def write(self, text: str) -> int:
        """Keep at most one piece per thread, including at the line limit."""
        if not text:
            return 0
        thread = threading.get_ident()
        prefix = _prefix.get()
        with self._lock:
            pending = self._pending.pop(thread, "")
            start = 0
            emitted = False
            while start < len(text):
                end = start + self._line_limit - len(pending)
                newline = text.find("\n", start, end + 1)
                if newline >= 0:
                    end = newline + 1
                elif end >= len(text):
                    self._pending[thread] = pending + text[start:]
                    break
                self._write_line(self.stdout, prefix, pending + text[start:end])
                emitted = True
                pending = ""
                start = end
            if emitted:
                self.stdout.flush()
        return len(text)

    def flush(self) -> None:
        thread = threading.get_ident()
        prefix = _prefix.get()
        with self._lock:
            rest = self._pending.pop(thread, "")
            if rest:
                self._write_line(self.stdout, prefix, rest)
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


@contextmanager
def unprefixed() -> Iterator[None]:
    """Print without the current step's prefix."""
    token = _prefix.set("")
    try:
        yield
    finally:
        _prefix.reset(token)


def reset_prefix(token: Token[str]) -> None:
    _prefix.reset(token)


def read_lines(
    pipe: IO[str],
    stream: TextIO,
    prefix: str,
    parts: list[str] | None,
    *,
    line_limit: int,
    echo: bool = True,
    daemon: bool = False,
) -> threading.Thread:
    """Read bounded pieces while preserving the original text in `parts`."""
    sink = current_sink()

    def run() -> None:
        head = ""
        while True:
            line = head + pipe.readline(line_limit + 1 - len(head))
            if not line:
                return
            head = ""
            # Read one character ahead so a line at the limit can keep its newline.
            if len(line) > line_limit and not line.endswith("\n"):
                line, head = line[:-1], line[-1]
            if parts is not None:
                parts.append(line)
            if not echo:
                continue
            if sink is not None:
                sink.emit(stream, prefix, line)
            else:
                stream.write(line)
                stream.flush()

    thread = threading.Thread(target=run, daemon=daemon)
    thread.start()
    return thread


@contextmanager
def parallel_output(line_limit: int) -> Iterator[None]:
    """Send standard output through a line sink for the duration of a parallel run."""
    sink = LineSink(sys.stdout, sys.stderr, line_limit=line_limit)
    token = _sink.set(sink)
    previous = sys.stdout
    sys.stdout = cast(TextIO, _Stdout(sink))
    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stdout = previous
        _sink.reset(token)
