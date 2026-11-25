"""Start processes, check readiness and stop their process trees."""

import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import TextIOWrapper
from pathlib import Path
from typing import IO, Any, TextIO

from roadie.engine.errors import StepFailed
from roadie.engine.lock import RUN_PID_VARIABLE
from roadie.engine.prefix import current_prefix, current_sink, read_lines
from roadie.system import (
    cannot_run_message,
    interrupt_process,
    parse_trace,
    prepare_command,
    process_group_session,
    signal_process_tree,
    spawn_in_own_group,
    stop_process_tree,
    traced_command,
)

_inherit_stdin: ContextVar[bool] = ContextVar("roadie_inherit_stdin", default=True)
_tracked: dict[int, tuple[subprocess.Popen[Any], bool]] = {}
_tracked_lock = threading.Lock()
_interrupt = threading.Event()
_before_stop: Callable[[], None] | None = None

_COMMAND_NAME_WORDS = 3
OUTPUT_TAIL = 2000
OUTPUT_LINE_LIMIT = 65_536


@dataclass
class TraceCollector:
    reads: set[Path] = field(default_factory=set)
    written: set[Path] = field(default_factory=set)


_trace_collector: ContextVar[TraceCollector | None] = ContextVar(
    "roadie_trace_collector", default=None
)


@contextmanager
def collect_traces(collector: TraceCollector) -> Generator[None]:
    """Collect the traces of `_cmd` programs run in this scope for one step."""
    token = _trace_collector.set(collector)
    try:
        yield
    finally:
        _trace_collector.reset(token)


def _absorb_trace(collector: TraceCollector, log: Path) -> None:
    try:
        if log.is_file():
            read, written = parse_trace(log.read_text(errors="surrogateescape"))
            collector.reads.update(read)
            collector.written.update(written)
    except OSError:
        pass
    finally:
        log.unlink(missing_ok=True)


# (spec) Step helpers
@dataclass(frozen=True)
class RunningProcess:
    process: subprocess.Popen[bytes]
    url: str
    readers: tuple[threading.Thread, ...] = ()

    def stop(self) -> None:
        """Signal the process tree with SIGTERM, wait up to 10 seconds, then
        send SIGKILL, and kill any descendant that outlives its parent."""
        untrack_process(self.process)
        stop_process_tree(self.process)
        for reader in self.readers:
            reader.join()


class _Tail(list[str]):
    """Keep only the last `OUTPUT_TAIL` characters appended."""

    _size = 0

    def append(self, line: str) -> None:
        super().append(line)
        self._size += len(line)
        while self._size > OUTPUT_TAIL:
            excess = self._size - OUTPUT_TAIL
            if len(self[0]) <= excess:
                self._size -= len(self.pop(0))
            else:
                self[0] = self[0][excess:]
                self._size -= excess


# (spec) Step helpers
@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str] | None = None
    label: str = ""

    def run(
        self,
        *,
        capture_output: bool = False,
        show_output: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Print `+ <command>` and run the command in `cwd`.

        Its environment is the process environment, which holds the run's
        environment-file values for the run's lifetime, then `environment`,
        then `ROADIE_RUN_PID` set to this process's PID. It inherits standard
        input. Standard error is shown as it arrives, each line prefixed with
        `[<label>] ` when the run uses more than one job. Standard output is
        shown the same way, unless `capture_output` is set: then it is returned,
        unprefixed, and not printed. `show_output` prints that captured output
        as it arrives, with the same prefix, and still returns it. Unless
        `check` is false, a non-zero exit fails with `<label> failed: <command>
        exited with status <n>: <last 2000 characters of standard error>`. It
        keeps captured standard output in full, the last 2000 characters of
        standard error, and nothing of standard output it does not capture.
        In an enforcement run the program runs under the strace trace of
        docs/spec.md, "Enforcing declared inputs"; `_start_cmd` programs
        are not traced. See docs/spec.md, "Step helpers", for command
        resolution and startup errors.
        """
        print("+", shlex.join(self.args), flush=True)
        prefix = current_prefix()
        sink = current_sink()
        out, err = (sink.stdout, sink.stderr) if sink is not None else (sys.stdout, sys.stderr)
        env = self._environment()
        collector = _trace_collector.get()
        trace_log: Path | None = None
        argv: list[str] | str = prepare_command(self.args, env)
        if collector is not None:
            handle, name = tempfile.mkstemp(prefix="roadie-trace-", suffix=".log")
            os.close(handle)
            trace_log = Path(name)
            base = argv if isinstance(argv, list) else [argv]
            argv = traced_command(base, trace_log)
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.cwd,
                env=env,
                stdout=subprocess.PIPE if capture_output or prefix else None,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as error:
            if collector is not None and trace_log is not None:
                _absorb_trace(collector, trace_log)
            raise StepFailed(self.label, cannot_run_message(self.args[0], error)) from error
        track_process(process, session=False)
        readers: list[threading.Thread] = []
        stdout_parts: list[str] | None = [] if capture_output else None
        stderr_parts: list[str] = _Tail()
        try:
            assert process.stderr is not None
            readers.append(
                read_lines(process.stderr, err, prefix, stderr_parts, line_limit=OUTPUT_LINE_LIMIT)
            )
            if process.stdout is not None:
                readers.append(
                    read_lines(
                        process.stdout,
                        out,
                        prefix,
                        stdout_parts,
                        line_limit=OUTPUT_LINE_LIMIT,
                        echo=not capture_output or show_output,
                    )
                )
            try:
                wait_interruptibly(process)
                for reader in readers:
                    reader.join()
                stdout = "".join(stdout_parts or []) if capture_output else None
                stderr = "".join(stderr_parts)
            except KeyboardInterrupt:
                note_interrupt()
                if process.poll() is None:
                    interrupt_process(process, session=False)
                process.wait()
                raise
        finally:
            for reader in readers:
                reader.join()
            untrack_process(process)
            if collector is not None and trace_log is not None:
                _absorb_trace(collector, trace_log)
        if interrupt_requested():
            raise KeyboardInterrupt
        completed = subprocess.CompletedProcess(
            self.args,
            process.returncode if process.returncode is not None else 1,
            stdout,
            stderr,
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or "").strip()[-OUTPUT_TAIL:]
            reason = f"{shlex.join(completed.args)} exited with status {completed.returncode}"
            raise StepFailed(self.label, f"{reason}: {detail}" if detail else reason)
        return completed

    def start(
        self,
        *,
        port: int,
        timeout: float = 120,
        health_path: str | None = None,
    ) -> RunningProcess:
        """Print `+ <command>`, start a server, and return it once it is ready.

        The command runs in `cwd` with the environment of `run`, inherits
        standard input, and starts in a new session (POSIX) so its whole process
        tree can be signalled. When the run uses more than one job, each line
        the server prints is shown as it arrives, prefixed with `[<label>] `.
        The result holds the process and the URL `http://127.0.0.1:<port>`; it
        is ready once the port accepts connections, or, with `health_path`,
        once `GET <url><health_path>` answers 200. If the process exits first,
        it fails with `<process> exited with status <n> before becoming ready`;
        if `timeout` seconds pass, with `<process> did not start listening on
        port <port> within <timeout>s` (or `did not become ready at <url>
        within <timeout>s`). `<process>` is the executable's file name and up
        to two following arguments that are not options. In both cases the
        process tree is stopped. A command that cannot start fails as `run`
        does. The program is found as `run` finds it.
        """
        print("+", shlex.join(self.args), flush=True)
        prefix = current_prefix()
        sink = current_sink()
        piped = bool(prefix) and sink is not None
        try:
            # Own group: a `cargo run`/`pnpm dev` grandchild holds the port.
            process = spawn_in_own_group(
                self.args,
                cwd=self.cwd,
                environment=self._environment(),
                stdin=None if _inherit_stdin.get() else subprocess.DEVNULL,
                stdout=subprocess.PIPE if piped else None,
                stderr=subprocess.PIPE if piped else None,
            )
        except OSError as error:
            raise StepFailed(self.label, cannot_run_message(self.args[0], error)) from error
        readers: tuple[threading.Thread, ...] = ()
        if piped and sink is not None:
            assert process.stdout is not None and process.stderr is not None
            readers = (
                _print_server_lines(process.stdout, sink.stdout, prefix),
                _print_server_lines(process.stderr, sink.stderr, prefix),
            )
        track_process(process, session=process_group_session())
        url = f"http://127.0.0.1:{port}"
        try:
            if health_path is None:
                wait_for_port(process, port=port, timeout=timeout)
            else:
                health_url = f"{url}{health_path}"
                _wait_for_ready(
                    process, lambda: http_url_is_ready(health_url), timeout,
                    f"did not become ready at {health_url} within {timeout:g}s",
                )
            return RunningProcess(process, url, readers)
        except BaseException:
            untrack_process(process)
            stop_process_tree(process)
            for reader in readers:
                reader.join()
            if interrupt_requested():
                raise KeyboardInterrupt
            raise

    def _environment(self) -> dict[str, str]:
        merged = {
            **os.environ,
            **(self.environment or {}),
        }
        merged[RUN_PID_VARIABLE] = str(os.getpid())
        return merged


def port_is_busy(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_port(
    process: subprocess.Popen[bytes],
    *,
    port: int,
    timeout: float = 120,
) -> None:
    _wait_for_ready(
        process, lambda: port_is_busy(port), timeout,
        f"did not start listening on port {port} within {timeout:g}s",
    )


def http_url_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_for_ready(
    process: subprocess.Popen[bytes],
    ready: Callable[[], bool],
    timeout: float,
    timeout_message: str,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if ready():
            return
        return_code = process.poll()
        if return_code is not None:
            raise SystemExit(
                f"{_process_name(process)} exited with status {return_code} before becoming ready"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(f"{_process_name(process)} {timeout_message}")
        try:
            process.wait(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            pass


def _process_name(process: subprocess.Popen[bytes]) -> str:
    command = process.args
    if isinstance(command, (str, bytes, os.PathLike)):
        return Path(os.fsdecode(command)).name
    words = [Path(os.fsdecode(command[0])).name]
    for argument in command[1:_COMMAND_NAME_WORDS]:
        word = os.fsdecode(argument)
        if word.startswith("-"):
            break
        words.append(word)
    return shlex.join(words)


def _print_server_lines(pipe: IO[bytes], stream: TextIO, prefix: str) -> threading.Thread:
    """Print each line a server writes to `pipe`, keeping none."""
    text = TextIOWrapper(pipe, encoding="utf-8", errors="replace", newline="\n")
    return read_lines(text, stream, prefix, None, line_limit=OUTPUT_LINE_LIMIT, daemon=True)


@contextmanager
def command_context(*, inherit: bool) -> Generator[None]:
    stdin_token = _inherit_stdin.set(inherit)
    try:
        yield
    finally:
        _inherit_stdin.reset(stdin_token)


signal_server = signal_process_tree


@contextmanager
def interrupt_on_terminate() -> Generator[None]:
    """Route SIGTERM into the same cleanup path as Ctrl-C.

    Python's default SIGTERM handler exits without unwinding, so a `kill` or a
    CI timeout skips every `finally` here and orphans both servers with their
    ports still held.
    """

    def raise_interrupt(_number: int, _frame: object) -> None:
        request_stop()
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, raise_interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        reset_interrupt()


def wait_interruptibly(process: subprocess.Popen[Any]) -> int:
    """Wait for a process in short slices, so Ctrl-C ends the wait at once.

    On Windows, an untimed `wait()` raises `KeyboardInterrupt` only when the
    process exits, so a program that outlives Ctrl-C would hang the run.
    """
    while True:
        try:
            return process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass


def track_process(process: subprocess.Popen[Any], *, session: bool) -> None:
    """Remember a program a step is running so an interrupt can stop it."""
    with _tracked_lock:
        _tracked[process.pid] = (process, session)


def untrack_process(process: subprocess.Popen[Any]) -> None:
    with _tracked_lock:
        _tracked.pop(process.pid, None)


def interrupt_requested() -> bool:
    return _interrupt.is_set()


def reset_interrupt() -> None:
    _interrupt.clear()


def set_before_stop(callback: Callable[[], None] | None) -> Callable[[], None] | None:
    """Run `callback` once, before an interrupt stops anything. Returns the previous one."""
    global _before_stop
    previous = _before_stop
    _before_stop = callback
    return previous


def note_interrupt() -> None:
    """Tell the active run that an interrupt has arrived, before anything is stopped."""
    callback = _before_stop
    if callback is not None:
        callback()


def request_stop() -> None:
    """Ask running steps to stop, and signal the programs they are running."""
    note_interrupt()
    _interrupt.set()
    stop_tracked_processes()


def stop_tracked_processes() -> None:
    with _tracked_lock:
        items = list(_tracked.values())
    for process, session in items:
        if process.poll() is not None:
            continue
        interrupt_process(process, session=session)
