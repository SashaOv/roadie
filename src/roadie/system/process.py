"""Start a process in its own group and stop a process tree."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO


def process_group_session() -> bool:
    """True when `spawn_in_own_group` puts the child in its own session."""
    return sys.platform != "win32"


def spawn_in_own_group(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    stdin: int | None = None,
    stdout: BinaryIO | int | None = None,
    stderr: BinaryIO | int | None = None,
) -> subprocess.Popen[bytes]:
    """Start a process in its own process group on POSIX; alone on Windows."""
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=None if environment is None else dict(environment),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=sys.platform != "win32",
    )


if sys.platform == "win32":

    def signal_process_tree(
        process: subprocess.Popen[bytes], sent: signal.Signals
    ) -> None:
        if not _taskkill(process.pid, force=False):
            process.send_signal(sent)

    def interrupt_process(process: subprocess.Popen[Any], *, session: bool) -> None:
        if not _taskkill(process.pid, force=False):
            process.terminate()

    def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
        """taskkill /T, wait up to 10 seconds, then taskkill /T /F."""
        if process.poll() is not None:
            return
        if not _taskkill(process.pid, force=False):
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if not _taskkill(process.pid, force=True):
                process.kill()
            process.wait()

    def _taskkill(pid: int, *, force: bool) -> bool:
        command = ["taskkill", "/T", "/PID", str(pid)]
        if force:
            command.append("/F")
        try:
            subprocess.run(command, capture_output=True, check=False)
        except OSError:
            return False
        return True

else:

    def signal_process_tree(
        process: subprocess.Popen[bytes], sent: signal.Signals
    ) -> None:
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

    def interrupt_process(process: subprocess.Popen[Any], *, session: bool) -> None:
        if session:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
        else:
            process.terminate()

    def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
        tree = (
            _posix_descendant_pids(process.pid) if process.poll() is None else (process.pid,)
        )
        if process.poll() is None:
            signal_process_tree(process, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                signal_process_tree(process, signal.SIGKILL)
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

    def _collect_descendants(
        root_pid: int, children: dict[int, list[int]]
    ) -> tuple[int, ...]:
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
