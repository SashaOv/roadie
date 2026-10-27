import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from wright import process as processes


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_sigterm_takes_the_same_cleanup_path_as_ctrl_c() -> None:
    previous = signal.getsignal(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt), processes.interrupt_on_terminate():
        os.kill(os.getpid(), signal.SIGTERM)

    assert signal.getsignal(signal.SIGTERM) is previous


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
        )
    )
    deadline = time.monotonic() + 10
    while not pid_file.read_text().strip() if pid_file.exists() else True:
        if time.monotonic() > deadline:
            process.kill()
            pytest.fail("grandchild never reported its pid")
        time.sleep(0.05)
    grandchild = int(pid_file.read_text().strip())

    processes.stop_dev_server(process)

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


def test_running_process_waits_for_port_and_stops() -> None:
    port = processes.available_port()
    server = processes.RunningProcess.start(
        (sys.executable, "-c", ("import socket; s = socket.socket(); "
         f"s.bind(('127.0.0.1', {port})); s.listen(); s.accept()")),
        port=port,
        timeout=5,
    )
    try:
        assert processes.port_is_busy(port)
        assert server.url == f"http://127.0.0.1:{port}"
    finally:
        server.stop()
    assert server.process.poll() is not None
    assert not processes.port_is_busy(port)


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
    with pytest.raises(SystemExit, match="exited with status 7|did not start listening"):
        processes.RunningProcess.start((sys.executable, "-c", command), port=processes.available_port(), timeout=0.5)
    assert len(started) == 1
    assert started[0].poll() is not None
