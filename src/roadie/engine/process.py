"""Start processes, check readiness and stop their process trees."""

import os
import shlex
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, BinaryIO, TextIO

from roadie.engine.errors import StepFailed
from roadie.engine.lock import RUN_PID_VARIABLE
from roadie.engine.prefix import (
    current_prefix,
    current_sink,
    output_streams,
    read_lines,
)
from roadie.system import (
    cannot_run_message,
    interrupt_process,
    prepare_command,
    process_group_session,
    signal_process_tree,
    spawn_in_own_group,
)
from roadie.system import (
    stop_process_tree as _stop_process_tree,
)

_inherit_stdin: ContextVar[bool] = ContextVar("roadie_inherit_stdin", default=True)
_tracked: dict[int, tuple[subprocess.Popen[Any], bool]] = {}
_tracked_lock = threading.Lock()
_interrupt = threading.Event()
_before_stop: Callable[[], None] | None = None
_pumps: dict[int, list[threading.Thread]] = {}

_COMMAND_NAME_WORDS = 3


# (spec) Step helpers
@dataclass(frozen=True)
class RunningProcess:
    process: subprocess.Popen[bytes]
    url: str

    @classmethod
    def start(
        cls,
        command: Sequence[str],
        *,
        port: int,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 120,
        health_path: str | None = None,
    ) -> "RunningProcess":
        prefix = current_prefix()
        sink = current_sink()
        piped = bool(prefix) and sink is not None
        process = spawn_server(
            command,
            environment=environment,
            cwd=cwd,
            stdout=subprocess.PIPE if piped else None,
            stderr=subprocess.PIPE if piped else None,
        )
        if piped and sink is not None:
            _pumps[process.pid] = _pump_server(
                process, sink_stdout=sink.stdout, sink_stderr=sink.stderr, prefix=prefix
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
            return cls(process, url)
        except BaseException:
            untrack_process(process)
            stop_process_tree(process)
            _join_pumps(process)
            if interrupt_requested():
                raise KeyboardInterrupt
            raise

    def stop(self) -> None:
        """Signal the process tree with SIGTERM, wait up to 10 seconds, then
        send SIGKILL, and kill any descendant that outlives its parent."""
        untrack_process(self.process)
        stop_process_tree(self.process)
        _join_pumps(self.process)


class _Tail(list[str]):
    """Keep only the last 2000 characters appended."""

    def __init__(self) -> None:
        super().__init__()
        self._size = 0

    def append(self, line: str) -> None:
        super().append(line)
        self._size += len(line)
        while self._size > 2000:
            excess = self._size - 2000
            if len(self[0]) <= excess:
                self._size -= len(self[0])
                del self[0]
            else:
                self[0] = self[0][excess:]
                self._size -= excess


class _Discarded(list[str]):
    """Accept lines without keeping them."""

    def append(self, _line: str) -> None:
        pass


# (spec) Step helpers
@dataclass(frozen=True)
class Command:
    """A command with the directory, environment and label it runs with."""

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

        Its environment is the process environment, then `environment`, then
        `ROADIE_RUN_PID` set to this process's PID. It inherits standard
        input. Standard error is shown as it arrives, each line prefixed
        with `[<label>] ` when the run uses more than one job. Standard
        output is shown the same way, unless `capture_output` is set: then
        it is returned, unprefixed, and not printed. `show_output` prints
        that captured output as it arrives, with the same prefix, and still
        returns it. Unless `check` is false, a non-zero exit fails with
        `<label> failed: <command> exited with status <n>: <last 2000
        characters of standard error>`. It keeps captured standard output
        in full, the last 2000 characters of standard error, and nothing
        of standard output it does not capture. See docs/spec.md, "Step
        helpers", for command resolution and startup errors.
        """
        print("+", shlex.join(self.args), flush=True)
        prefix = current_prefix()
        out, err = output_streams()
        env = self._environment()
        try:
            process = subprocess.Popen(
                prepare_command(self.args, env),
                cwd=self.cwd,
                env=env,
                stdout=subprocess.PIPE if capture_output or prefix else None,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as error:
            raise StepFailed(self.label, cannot_run_message(self.args[0], error)) from error
        track_process(process, session=False)
        readers: list[threading.Thread] = []
        stdout_parts: list[str] = [] if capture_output else _Discarded()
        stderr_parts: list[str] = _Tail()
        try:
            assert process.stderr is not None
            readers.append(read_lines(process.stderr.readline, err, prefix, stderr_parts))
            if process.stdout is not None:
                readers.append(
                    read_lines(
                        process.stdout.readline,
                        out,
                        prefix,
                        stdout_parts,
                        echo=not capture_output or show_output,
                    )
                )
            try:
                wait_interruptibly(process)
                for reader in readers:
                    reader.join()
                stdout = "".join(stdout_parts) if capture_output else None
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
        if interrupt_requested():
            raise KeyboardInterrupt
        completed = subprocess.CompletedProcess(
            self.args,
            process.returncode if process.returncode is not None else 1,
            stdout,
            stderr,
        )
        if check:
            self._check(completed)
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
        standard input, and starts in a new session (POSIX) so its whole
        process tree can be signalled. When the run uses more than one job,
        each line the server prints is shown as it arrives, prefixed with
        `[<label>] `. The result holds the process and the URL
        `http://127.0.0.1:<port>`; it is ready once the port accepts
        connections, or, with `health_path`, once `GET <url><health_path>`
        answers 200.

        If the process exits first, it fails with `<process> exited with
        status <n> before becoming ready`; if `timeout` seconds pass, with
        `<process> did not start listening on port <port> within <timeout>s`
        (or `did not become ready at <url> within <timeout>s`). `<process>`
        is the executable's file name and up to two following arguments that
        are not options. In both cases the process tree is stopped. A
        command that cannot start fails as `run` does. The program is found
        as `run` finds it.
        """
        print("+", shlex.join(self.args), flush=True)
        try:
            return RunningProcess.start(
                self.args,
                port=port,
                environment=self._environment(),
                timeout=timeout,
                cwd=self.cwd,
                health_path=health_path,
            )
        except OSError as error:
            raise StepFailed(self.label, cannot_run_message(self.args[0], error)) from error

    def _environment(self) -> dict[str, str]:
        merged = {**os.environ, **(self.environment or {})}
        merged[RUN_PID_VARIABLE] = str(os.getpid())
        return merged

    def _check(self, completed: subprocess.CompletedProcess[str]) -> None:
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[-2000:]
            reason = f"{shlex.join(completed.args)} exited with status {completed.returncode}"
            raise StepFailed(
                self.label,
                f"{reason}: {detail}" if detail else reason,
            )


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


def _decode_line(pipe: IO[bytes]) -> str:
    raw = pipe.readline()
    return "" if raw == b"" else raw.decode(errors="replace")


def _pump_server(
    process: subprocess.Popen[bytes],
    *,
    sink_stdout: TextIO,
    sink_stderr: TextIO,
    prefix: str,
) -> list[threading.Thread]:
    stdout = process.stdout
    stderr = process.stderr
    if stdout is None or stderr is None:
        return []
    return [
        read_lines(lambda: _decode_line(stdout), sink_stdout, prefix, [], daemon=True),
        read_lines(lambda: _decode_line(stderr), sink_stderr, prefix, [], daemon=True),
    ]


def _join_pumps(process: subprocess.Popen[bytes]) -> None:
    for thread in _pumps.pop(process.pid, []):
        thread.join()


@contextmanager
def command_stdin(*, inherit: bool) -> Generator[None]:
    """Choose whether a command started in this scope inherits standard input."""
    token = _inherit_stdin.set(inherit)
    try:
        yield
    finally:
        _inherit_stdin.reset(token)


def spawn_server(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    stdout: BinaryIO | int | None = None,
    stderr: BinaryIO | int | None = None,
) -> subprocess.Popen[bytes]:
    """Start a server in its own process group.

    `cargo run` and `pnpm dev` are wrappers: the process that holds the port is
    a grandchild. Signalling the group reaches it; signalling the wrapper alone
    can leave the port held after the command returns.
    """
    return spawn_in_own_group(
        command,
        cwd=cwd,
        environment=environment,
        stdin=None if _inherit_stdin.get() else subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
    )


def signal_server(process: subprocess.Popen[bytes], sent: signal.Signals) -> None:
    signal_process_tree(process, sent)


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


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    _stop_process_tree(process)
