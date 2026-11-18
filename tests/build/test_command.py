import json
import signal
import sys
from pathlib import Path

from roadie.build import (
    Command,
    RunningProcess,
    available_port,
    cargo_executable,
    port_is_busy,
    signal_server,
    wait_for_port,
)


# (spec) Step helpers
def test_a_command_runs_and_starts_a_server_without_a_step(tmp_path: Path) -> None:
    completed = Command(
        (sys.executable, "-c", "print('hi')"), cwd=tmp_path, label="tool"
    ).run(capture_output=True)
    assert completed.stdout == "hi\n"

    executable = tmp_path / "app"
    line = json.dumps(
        {
            "reason": "compiler-artifact",
            "package_id": "path+file:///tmp/app#app@0.1.0",
            "target": {"kind": ["bin"]},
            "executable": str(executable),
        }
    )
    assert cargo_executable(line, "app") == executable

    port = available_port()
    server: RunningProcess = Command(
        (
            sys.executable,
            "-c",
            (
                # Keep accepting: the readiness poll is itself a connection.
                "import socket\n"
                f"s = socket.create_server(('127.0.0.1', {port}))\n"
                "while True: s.accept()[0].close()\n"
            ),
        ),
        cwd=tmp_path,
        label="server",
    ).start(port=port, timeout=5)
    try:
        wait_for_port(server.process, port=port, timeout=5)
        assert port_is_busy(port)
        signal_server(server.process, signal.SIGTERM)
    finally:
        server.stop()
    assert server.process.poll() is not None
    assert not port_is_busy(port)
