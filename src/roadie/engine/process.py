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
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, BinaryIO, TextIO

from roadie.engine.prefix import current_prefix, current_sink, read_lines

_tracked: dict[int, tuple[subprocess.Popen[Any], bool]] = {}
_tracked_lock = threading.Lock()
_interrupt = threading.Event()
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
        track_process(process, session=os.name != "nt")
        url = f"http://127.0.0.1:{port}"
        try:
            if health_path is None:
                wait_for_port(process, port=port, timeout=timeout)
            else:
                wait_for_http_server(process, url=f"{url}{health_path}", timeout=timeout)
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
    deadline = time.monotonic() + timeout
    while True:
        if port_is_busy(port):
            return
        return_code = process.poll()
        if return_code is not None:
            raise SystemExit(
                f"{_process_name(process)} exited with status {return_code} before becoming ready"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(
                f"{_process_name(process)} did not start listening on port {port} within {timeout:g}s"
            )
        try:
            process.wait(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            pass


def http_url_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_for_http_server(
    process: subprocess.Popen[bytes],
    *,
    url: str,
    timeout: float = 120,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if http_url_is_ready(url):
            return
        return_code = process.poll()
        if return_code is not None:
            raise SystemExit(
                f"{_process_name(process)} exited with status {return_code} before becoming ready"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit(
                f"{_process_name(process)} did not become ready at {url} within {timeout:g}s"
            )
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
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=None if environment is None else dict(environment),
        stdout=stdout,
        stderr=stderr,
        start_new_session=os.name == "posix",
    )


def _posix_descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Return root_pid and every descendant, from `/proc` or `ps`."""
    if Path("/proc").is_dir():
        return _proc_descendant_pids(root_pid)
    return _ps_descendant_pids(root_pid)


def _proc_descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Descendants from `/proc/<pid>/stat` ppid links. Linux."""
    children: dict[int, list[int]] = {}
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        end = stat.rfind(")")
        if end < 0:
            continue
        fields = stat[end + 2 :].split()
        if len(fields) < 2:
            continue
        try:
            ppid = int(fields[1])
            pid = int(entry.name)
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    return _collect_descendants(root_pid, children)


def _ps_descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Descendants from `ps -A -o pid=,ppid=`. macOS, where `/proc` is absent."""
    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return (root_pid,)
    if completed.returncode != 0:
        return (root_pid,)
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    return _collect_descendants(root_pid, children)


def _collect_descendants(root_pid: int, children: dict[int, list[int]]) -> tuple[int, ...]:
    found: list[int] = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        found.append(pid)
        stack.extend(children.get(pid, ()))
    return tuple(found)


def signal_server(process: subprocess.Popen[bytes], sent: signal.Signals) -> None:
    if os.name == "nt":
        _signal_windows_process(process, sent)
        return
    _signal_posix_group(process, sent)


def _signal_windows_process(process: subprocess.Popen[bytes], sent: signal.Signals) -> None:
    """Signal the process alone. Unverified until a Windows CI job exists."""
    process.send_signal(sent)


def _signal_posix_group(process: subprocess.Popen[bytes], sent: signal.Signals) -> None:
    """Signal the process group, then every descendant found before the signal.

    Snapshot the tree first: once the wrapper dies, orphans are reparented and
    a later walk from process.pid misses them. killpg alone misses a grandchild
    that sits outside the wrapper's group.
    """
    tree = _posix_descendant_pids(process.pid)
    try:
        os.killpg(os.getpgid(process.pid), sent)
    except (ProcessLookupError, PermissionError):
        process.send_signal(sent)
    for pid in reversed(tree):
        try:
            os.kill(pid, sent)
        except (ProcessLookupError, PermissionError):
            continue


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


def request_stop() -> None:
    """Ask running steps to stop, and signal the programs they are running."""
    _interrupt.set()
    stop_tracked_processes()


def stop_tracked_processes() -> None:
    with _tracked_lock:
        items = list(_tracked.values())
    for process, session in items:
        if process.poll() is not None:
            continue
        if session and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
        else:
            process.terminate()


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        _stop_windows_process(process)
        return
    _stop_posix_process_tree(process)


def _stop_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Signal the process alone, with no new session. Unverified until a
    Windows CI job exists; `SIGKILL` is not a Windows signal."""
    if process.poll() is None:
        _signal_windows_process(process, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _signal_windows_process(process, signal.SIGKILL)
            process.wait()


def _stop_posix_process_tree(process: subprocess.Popen[bytes]) -> None:
    tree = _posix_descendant_pids(process.pid) if process.poll() is None else (process.pid,)
    if process.poll() is None:
        signal_server(process, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            signal_server(process, signal.SIGKILL)
            process.wait()
    # The wrapper can exit while a background grandchild keeps running
    # (seen on self-hosted Actions runners with `sh -c 'sleep &'`).
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        alive = []
        for pid in tree:
            if pid == process.pid:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.append(pid)
        if not alive:
            return
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        time.sleep(0.05)


