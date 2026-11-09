import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, isolate_repo, write_build

import roadie
from roadie.build import Service, Step
from roadie.engine.errors import StepFailed
from roadie.engine.process import RunningProcess, available_port
from roadie.engine.run import Run

_STDIN_SERVER = """
import sys

from roadie.engine.process import available_port


class Reader(Service):
    def build(self, run):
        port = available_port()
        return self._start_cmd(
            sys.executable,
            "-c",
            "import socket, sys, threading\\n"
            "s = socket.socket()\\n"
            f"s.bind(('127.0.0.1', {port}))\\n"
            "s.listen()\\n"
            "s.accept()[0].close()\\n"  # read only once the run waits on it
            "def serve():\\n"
            "    while True: s.accept()[0].close()\\n"
            "threading.Thread(target=serve, daemon=True).start()\\n"
            "sys.exit(0 if sys.stdin.readline() == 'b\\\\n' else 5)\\n",
            port=port,
        )


dev = Reader()
"""


# (spec) Step helpers
# (spec) Failures
def test_cmd_failure_names_step_command_exit_status_and_stderr_tail() -> None:
    class Command(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('x' * 3000 + 'reason'); sys.exit(7)",
            )

    step = Command()
    step.name = "compile"
    with pytest.raises(StepFailed) as error:
        execute([step])
    message = str(error.value)
    assert message.startswith("compile failed:")
    assert sys.executable in message
    assert "exited with status 7" in message
    assert message.endswith("reason")
    assert "x" * 2001 not in message


# (spec) Step helpers
def test_start_cmd_reports_early_exit() -> None:
    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            return self._start_cmd(
                sys.executable,
                "-c",
                "raise SystemExit(9)",
                port=available_port(),
                timeout=5,
            )

    server = Server()
    server.name = "server"
    with pytest.raises(
        StepFailed, match="server.*exited with status 9 before becoming ready"
    ):
        execute([server])


# (spec) Step helpers
@pytest.mark.parametrize("jobs", [1, 2])
def test_captured_stdout_is_returned_and_not_printed(
    jobs: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Command(Step[str]):
        def build(self, run: Run) -> str:
            completed = self._cmd(
                sys.executable,
                "-c",
                "import sys\nprint('secret' + '-out')\nsys.stderr.write('visible' + '-err\\n')",
                capture_output=True,
            )
            return completed.stdout or ""

    step = Command()
    step.name = "tool"
    assert execute([step], root=tmp_path, jobs=jobs) == "secret-out\n"
    captured = capsys.readouterr()
    shown = captured.out + captured.err
    assert "secret-out" not in shown
    assert "visible-err" in shown
    if jobs > 1:
        assert "[tool] visible-err" in shown


# (spec) Step helpers
@pytest.mark.parametrize("jobs", [1, 2])
def test_show_output_prints_captured_stdout_and_stderr(
    jobs: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Command(Step[str]):
        def build(self, run: Run) -> str:
            completed = self._cmd(
                sys.executable,
                "-c",
                "import sys\nprint('secret' + '-out')\nsys.stderr.write('visible' + '-err\\n')",
                capture_output=True,
                show_output=True,
            )
            return completed.stdout or ""

    step = Command()
    step.name = "tool"
    assert execute([step], root=tmp_path, jobs=jobs) == "secret-out\n"
    shown = capsys.readouterr()
    output = shown.out + shown.err
    assert "secret-out" in output
    assert "visible-err" in output
    if jobs > 1:
        assert "[tool] secret-out" in output
        assert "[tool] visible-err" in output


# (spec) Step helpers
def test_a_failing_captured_command_quotes_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Command(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(
                sys.executable,
                "-c",
                "import sys\nprint('hidden' + '-out')\nsys.stderr.write('boom' + '-err')\nsys.exit(3)",
                capture_output=True,
            )

    step = Command()
    step.name = "tool"
    with pytest.raises(StepFailed, match="boom-err") as error:
        execute([step], root=tmp_path)
    message = str(error.value)
    assert "exited with status 3" in message
    assert "hidden-out" not in message
    shown = capsys.readouterr()
    assert "boom-err" in shown.out + shown.err
    assert "hidden-out" not in shown.out + shown.err


# (spec) Step helpers
def test_commands_run_in_the_steps_base_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)

    class Command(Step[str]):
        def build(self, run: Run) -> str:
            return self._cmd(sys.executable, "-c", "from pathlib import Path; print(Path.cwd())", capture_output=True).stdout.strip()

    assert execute([Command()]) == str(tmp_path.resolve())


# (spec) Step helpers
def test_started_commands_run_in_the_steps_base_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    marker = tmp_path / "cwd.txt"
    port = available_port()

    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            return self._start_cmd(sys.executable, "-c", (
                "from pathlib import Path; import socket, threading; "
                "Path('cwd.txt').write_text(str(Path.cwd())); "
                f"s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(); "
                "threading.Event().wait()"
            ), port=port, timeout=5)

    @dataclass(eq=False)
    class After(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            return None

    server = Server()
    execute([server, After(server)])
    assert marker.read_text() == str(tmp_path.resolve())


# (spec) Step kinds
def test_an_unrequested_service_reads_the_null_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    port = available_port()
    marker = tmp_path / "stdin.txt"
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"secret")
    os.close(write_fd)
    saved = os.dup(0)
    os.dup2(read_fd, 0)
    os.close(read_fd)

    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            return self._start_cmd(
                sys.executable,
                "-c",
                "import sys, socket, threading\n"
                "got = sys.stdin.read(6)\n"
                f"open({str(marker)!r}, 'w').write(got or 'eof')\n"
                f"s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen()\n"
                "threading.Event().wait()\n",
                port=port,
                timeout=5,
            )

    @dataclass(eq=False)
    class After(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            return None

    server = Server()
    try:
        execute([server, After(server)])
    finally:
        os.dup2(saved, 0)
        os.close(saved)
    assert marker.read_text() == "eof"


# (spec) Step helpers
@pytest.mark.parametrize(("typed", "status"), [(b"b\n", 0), (b"x\n", 1)])
def test_requested_service_reads_the_terminal_input(
    typed: bytes, status: int, tmp_path: Path
) -> None:
    # A dev server's keyboard shortcuts (Vite's b and q) need its stdin.
    write_build(tmp_path, _STDIN_SERVER)
    finished = subprocess.run(
        (sys.executable, "-c", "from roadie.cli import main; main()", "-f", str(tmp_path)),
        cwd=Path(roadie.__file__).resolve().parent.parent,
        check=False,
        input=typed,
        capture_output=True,
        timeout=60,
    )
    output = (finished.stdout + finished.stderr).decode()
    assert finished.returncode == status, output
    if status == 0:
        assert "dev exited" in output
    else:
        assert "exited with status 5" in output
