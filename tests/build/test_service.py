import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import (
    FIXTURES,
    FakeDeployment,
    execute,
    fake_server,
    write_build,
)

from roadie import cli
from roadie.build import (
    HttpService,
    Service,
    Step,
)
from roadie.engine.errors import StepFailed
from roadie.engine.process import RunningProcess, available_port
from roadie.engine.run import Run


# (spec) Step kinds
# (spec) Teardown
@pytest.mark.parametrize("ending", ["success", "failure", "interrupt"])
def test_service_process_is_stopped_after_run(ending: str) -> None:
    started: list[RunningProcess] = []

    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            port = available_port()
            server = self._start_cmd(
                sys.executable,
                "-c",
                # Keep accepting: the readiness poll is itself a connection.
                (
                    "import socket; s = socket.socket(); "
                    f"s.bind(('127.0.0.1', {port})); s.listen()\n"
                    "while True: s.accept()[0].close()"
                ),
                port=port,
                timeout=5,
            )
            started.append(server)
            return server

    @dataclass(eq=False)
    class Consumer(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            assert run[self.server].process.poll() is None
            if ending == "failure":
                raise RuntimeError("failed")
            if ending == "interrupt":
                raise KeyboardInterrupt

    server = Server()
    try:
        if ending == "success":
            execute([server, Consumer(server)])
        else:
            with pytest.raises(
                KeyboardInterrupt if ending == "interrupt" else StepFailed
            ):
                execute([server, Consumer(server)])
        assert started[0].process.poll() is not None
    finally:
        for child in started:
            child.stop()


# (spec) Step kinds
# (spec) Reusing a step
def test_service_steps_are_not_reusable() -> None:
    assert Step.reusable is True
    assert Service.reusable is False


# (spec) Step kinds
def test_http_service_waits_for_its_health_path_and_fails_when_the_process_exits(tmp_path: Path) -> None:
    port = available_port()
    ready = tmp_path / "ready"

    class Api(HttpService):
        health_path = "/health"

        def build(self, run: Run) -> RunningProcess:
            return self._start_cmd(
                sys.executable, str(FIXTURES / "http_service.py"), str(port), str(ready),
                port=port, timeout=5,
            )

    @dataclass(eq=False)
    class After(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            return None

    api = Api()
    api.name = "api"
    execute([api, After(api)])
    assert ready.read_text() == "ok"

    class Exits(HttpService):
        def build(self, run: Run) -> RunningProcess:
            listen = available_port()
            command = (
                "import socket, time; "
                f"s = socket.socket(); s.bind(('127.0.0.1', {listen})); s.listen(); "
                "time.sleep(0.3); raise SystemExit(4)"
            )
            return self._start_cmd(sys.executable, "-c", command, port=listen, timeout=2)

    dead = Exits()
    dead.name = "api"
    with pytest.raises(StepFailed, match="api failed:.*exited with status 4 before becoming ready"):
        execute([dead])


# (spec) Requested result
def test_requested_service_stays_up_until_ctrl_c(capsys: pytest.CaptureFixture[str]) -> None:
    deployment = FakeDeployment(interrupt=True)

    def release() -> None:
        assert deployment.waiting.wait(2)
        assert deployment.stopped is False
        deployment.release.set()

    threading.Thread(target=release).start()
    execute([fake_server(deployment)])
    assert deployment.stopped is True
    output = capsys.readouterr().out
    assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
    assert "server exited" not in output


# (spec) Requested result
@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX-only")
def test_sigterm_while_a_requested_service_is_up_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    started = threading.Event()

    class Sleeper:
        url = "http://127.0.0.1:9"
        process = None
        stopped = False

        def wait(self) -> int:
            started.set()
            time.sleep(5)
            return 0

        def stop(self) -> None:
            self.stopped = True

    sleeper = Sleeper()
    sleeper.process = sleeper

    def poke() -> None:
        assert started.wait(2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=poke).start()
    execute([fake_server(sleeper)])
    assert sleeper.stopped is True
    output = capsys.readouterr().out
    assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
    assert "server exited" not in output


# (spec) Requested result
@pytest.mark.parametrize("status", [0, 3])
def test_requested_service_exit_status(status: int, capsys: pytest.CaptureFixture[str]) -> None:
    deployment = FakeDeployment(status=status)
    if status == 0:
        execute([fake_server(deployment)])
        output = capsys.readouterr().out
        assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
        assert "server exited" in output
    else:
        with pytest.raises(StepFailed, match="server failed: exited with status 3"):
            execute([fake_server(deployment)])
    assert deployment.stopped is True


# (spec) Requested result
def test_cli_ctrl_c_on_a_requested_service_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "class Up(Service):\n"
        "    def build(self, run):\n"
        "        class Proc:\n"
        "            url = 'http://127.0.0.1:9'\n"
        "            def wait(self):\n"
        "                raise KeyboardInterrupt\n"
        "            def stop(self):\n"
        "                pass\n"
        "        return Proc()\n"
        "dev = Up()\n",
    )
    cli.run(tmp_path)
    assert "dev is up: http://127.0.0.1:9 (Ctrl-C to stop)" in capsys.readouterr().out
