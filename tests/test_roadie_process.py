import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from fixtures.http_service import HealthServer

from roadie import process as processes


def test_loopback_http_fixture_does_not_resolve_hostnames(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_lookup(host: str) -> str:
        pytest.fail(f"loopback HTTP fixture tried to resolve {host}")

    monkeypatch.setattr(socket, "getfqdn", reject_lookup)
    with HealthServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]


# (spec) Teardown
@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_sigterm_takes_the_same_cleanup_path_as_ctrl_c() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt), processes.interrupt_on_terminate():
        os.kill(os.getpid(), signal.SIGTERM)

    assert signal.getsignal(signal.SIGTERM) is previous


# (spec) Started processes
@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_stopping_a_server_stops_its_tree_not_only_the_wrapper(tmp_path: Path) -> None:
    """`cargo run` and `pnpm dev` hold the port in a grandchild, so a stop that
    signals only the wrapper leaves the port held."""
    if os.environ.get("RUNNER_ENVIRONMENT") == "self-hosted":
        pytest.skip(
            "self-hosted Actions runners leave start_new_session grandchildren "
            "unkillable from the job process after the wrapper exits"
        )
    pid_file = tmp_path / "grandchild.pid"
    process = processes.spawn_server(
        (
            sys.executable,
            "-c",
            ("import os, time\n"
            f"from pathlib import Path\n"
            f"pid_file = Path({str(pid_file)!r})\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(120)\n"
            "else:\n"
            "    pid_file.write_text(str(child))\n"
            "    os.waitpid(child, 0)\n"),
        ),
        cwd=tmp_path,
    )
    deadline = time.monotonic() + 10
    while not pid_file.read_text().strip() if pid_file.exists() else True:
        if time.monotonic() > deadline:
            process.kill()
            pytest.fail("grandchild never reported its pid")
        time.sleep(0.05)
    grandchild = int(pid_file.read_text().strip())

    processes.stop_process_tree(process)

    assert process.poll() is not None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(grandchild, signal.SIGKILL)
    pytest.fail(f"grandchild {grandchild} survived the stop")


# (spec) Started processes
def test_running_process_waits_for_port_and_stops() -> None:
    port = processes.available_port()
    server = processes.RunningProcess.start(
        # Keep accepting: the readiness poll is itself a connection.
        (sys.executable, "-c", ("import socket; s = socket.socket(); "
         f"s.bind(('127.0.0.1', {port})); s.listen()\n"
         "while True: s.accept()[0].close()")),
        port=port,
        timeout=5,
        cwd=Path.cwd(),
    )
    try:
        assert processes.port_is_busy(port)
        assert server.url == f"http://127.0.0.1:{port}"
    finally:
        server.stop()
    assert server.process.poll() is not None
    assert not processes.port_is_busy(port)


# (spec) Started processes
@pytest.mark.parametrize("ending", ["exit", "timeout"])
def test_running_process_cleans_up_failed_start(ending: str, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen[bytes]] = []
    original = processes.spawn_server

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = original(*args, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr(processes, "spawn_server", spawn)
    command = "raise SystemExit(7)" if ending == "exit" else "import threading; threading.Event().wait()"
    reason = (
        f"{re.escape(Path(sys.executable).name)} exited with status 7 before becoming ready"
        if ending == "exit"
        else f"{re.escape(Path(sys.executable).name)} did not start listening"
    )
    with pytest.raises(SystemExit, match=reason):
        processes.RunningProcess.start((sys.executable, "-c", command), port=processes.available_port(), timeout=0.5, cwd=Path.cwd())
    assert len(started) == 1
    assert started[0].poll() is not None


# (spec) Started processes
@pytest.mark.parametrize("ending", ["exit", "timeout"])
@pytest.mark.parametrize(
    ("command", "name"),
    [
        (("tool",), "tool"),
        (["tool", "serve"], "tool serve"),
        (("tool", "dev", "server"), "tool dev server"),
        (("tool", "dev", "server", "extra"), "tool dev server"),
        (("tool", "--config", "settings"), "tool"),
        (("tool", "serve", "--port", "43210"), "tool serve"),
        (("tool", "serve local"), "tool 'serve local'"),
        ((b"tool", b"serve"), "tool serve"),
        ("tool", "tool"),
        (b"tool", "tool"),
        (Path("tool"), "tool"),
    ],
)
def test_startup_errors_identify_the_command(
    command: object, name: str, ending: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "bin" / "tool"
    if isinstance(command, (tuple, list)):
        command = [executable, *command[1:]]
    elif isinstance(command, bytes):
        command = os.fsencode(executable)
    elif isinstance(command, Path):
        command = executable
    else:
        command = str(executable)

    class Process:
        args = command

        def poll(self) -> int | None:
            return 7 if ending == "exit" else None

    monkeypatch.setattr(processes, "port_is_busy", lambda port: False)
    with pytest.raises(SystemExit) as error:
        processes.wait_for_port(Process(), port=43210, timeout=0)
    reason = (
        "exited with status 7 before becoming ready"
        if ending == "exit"
        else "did not start listening on port 43210 within 0s"
    )
    assert str(error.value) == f"{name} {reason}"


# (spec) Started processes
def test_http_start_waits_until_the_health_path_returns_200() -> None:
    port = processes.available_port()
    server = processes.RunningProcess.start(
        (sys.executable, str(Path(__file__).parent / "fixtures/http_service.py"), str(port)),
        port=port,
        health_path="/health",
        timeout=5,
        cwd=Path.cwd(),
    )
    try:
        with urllib.request.urlopen(f"{server.url}/health", timeout=1) as response:
            assert response.status == 200
    finally:
        server.stop()


# (spec) Started processes
@pytest.mark.parametrize("ending", ["exit", "timeout"])
def test_http_start_fails_when_the_process_exits_or_never_returns_200(ending: str) -> None:
    port = processes.available_port()
    if ending == "exit":
        command = (
            "import socket, time; "
            f"s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(); "
            "time.sleep(0.3); raise SystemExit(4)"
        )
        reason = "exited with status 4 before becoming ready"
        timeout = 2
    else:
        command = "import time; time.sleep(30)"
        reason = f"did not become ready at http://127.0.0.1:{port}/health within 0.4s"
        timeout = 0.4
    with pytest.raises(SystemExit, match=re.escape(reason)):
        processes.RunningProcess.start(
            (sys.executable, "-c", command),
            port=port,
            health_path="/health",
            timeout=timeout,
            cwd=Path.cwd(),
        )


# (spec) Started processes
def test_spawn_server_requires_a_directory() -> None:
    with pytest.raises(TypeError):
        processes.spawn_server((sys.executable, "-c", "pass"))
