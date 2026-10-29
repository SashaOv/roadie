"""Start processes, check readiness and stop their process trees."""

import os
import shlex
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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
        process = spawn_server(command, environment=environment, cwd=cwd)
        url = f"http://127.0.0.1:{port}"
        try:
            if health_path is None:
                wait_for_port(process, port=port, timeout=timeout)
            else:
                wait_for_http_server(process, url=f"{url}{health_path}", timeout=timeout)
            return cls(process, url)
        except BaseException:
            stop_process_tree(process)
            raise

    def stop(self) -> None:
        """Signal the process tree with SIGTERM, wait up to 10 seconds, then
        send SIGKILL, and kill any descendant that outlives its parent."""
        stop_process_tree(self.process)


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


def spawn_server(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    stdout: BinaryIO | None = None,
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
        start_new_session=os.name == "posix",
    )


def _posix_descendant_pids(root_pid: int) -> tuple[int, ...]:
    """Return root_pid and every descendant found via /proc ppid links."""
    proc = Path("/proc")
    if not proc.is_dir():
        return (root_pid,)
    children: dict[int, list[int]] = {}
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
    if os.name != "posix":
        process.send_signal(sent)
        return
    # Snapshot the tree before any signal: once the wrapper dies, orphans are
    # reparented and a later /proc walk from process.pid misses them.
    tree = _posix_descendant_pids(process.pid)
    try:
        os.killpg(os.getpgid(process.pid), sent)
    except (ProcessLookupError, PermissionError):
        process.send_signal(sent)
    # killpg alone is not enough on some self-hosted Actions runners: a
    # background grandchild can sit outside the wrapper's group.
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
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, raise_interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    tree = (
        _posix_descendant_pids(process.pid)
        if os.name == "posix" and process.poll() is None
        else (process.pid,)
    )
    if process.poll() is None:
        signal_server(process, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            signal_server(process, signal.SIGKILL)
            process.wait()
    # The wrapper can exit while a background grandchild keeps running
    # (seen on self-hosted Actions runners with `sh -c 'sleep &'`).
    if os.name != "posix":
        return
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


