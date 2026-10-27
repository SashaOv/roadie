import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from forge.environment import repository_root
from jammer_build.steps import OpenApi
from wright import CargoPackage, Service, Step, WrightError, cli
from wright import TestFailure as Failure
from wright import TestResults as Results
from wright import Tests as Checks
from wright.errors import StepFailed
from wright.graph import Graph
from wright.process import RunningProcess, available_port
from wright.run import Run, execute
from wright.steps import RustBinary

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(eq=False)
class Value(Step[str]):
    value: str

    def build(self, run: Run) -> str:
        return self.value


def test_steps_build_in_plan_order_with_dependency_values(tmp_path: Path) -> None:
    events: list[object] = []

    @dataclass
    class Consumer(Step[str]):
        source: Step[str]

        def build(self, run: Run) -> str:
            events.append(run[self.source])
            return run[self.source] + "!"

    first = Value("first")
    second = Consumer(first)
    third = Consumer(second)
    graph = Graph(tmp_path / "build.py", {"last": third})
    assert execute(graph.plan()) == "first!!"
    assert events == ["first", "first!"]


@pytest.mark.parametrize("ending", ["success", "failure", "ctrl-c", "sigterm"])
def test_teardown_after_last_consumer_and_on_failure_or_interrupt(ending: str) -> None:
    if ending == "sigterm" and os.name != "posix":
        pytest.skip("SIGTERM unwinding is POSIX-only")
    events: list[str] = []

    @dataclass(eq=False)
    class Resource(Step[str]):
        label: str

        def build(self, run: Run) -> str:
            events.append("build " + self.label)
            return self.label

        def teardown(self, value: str) -> None:
            events.append("stop " + value)

    @dataclass(eq=False)
    class Consumer(Step[str]):
        resource: Step[str]

        def build(self, run: Run) -> str:
            events.append("consume " + run[self.resource])
            if ending == "failure":
                raise RuntimeError("rejected")
            if ending == "ctrl-c":
                raise KeyboardInterrupt
            if ending == "sigterm":
                os.kill(os.getpid(), signal.SIGTERM)
            return "consumed"

    class Later(Step[None]):
        def build(self, run: Run) -> None:
            events.append("later")

    held = Resource("held")
    transient = Resource("service")
    consumer = Consumer(transient)
    consumer.name = "consumer"

    # The held resource also belongs to the final step's dependencies.
    @dataclass(eq=False)
    class Last(Step[None]):
        resource: Step[str]

        def build(self, run: Run) -> None:
            events.append("last")

    steps = [held, transient, consumer, Later(), Last(held)]
    if ending == "failure":
        with pytest.raises(StepFailed, match="consumer failed: rejected"):
            execute(steps)
    elif ending in ("ctrl-c", "sigterm"):
        with pytest.raises(KeyboardInterrupt):
            execute(steps)
    else:
        execute(steps)
    assert events[:3] == ["build held", "build service", "consume service"]
    assert events[3:] == (
        ["stop service", "later", "last", "stop held"]
        if ending == "success"
        else ["stop service", "stop held"]
    )


@pytest.mark.parametrize("ending", ["success", "failure", "interrupt"])
def test_service_process_is_stopped_after_run(ending: str) -> None:
    started: list[RunningProcess] = []

    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            port = available_port()
            server = self._start_cmd(
                sys.executable,
                "-c",
                (
                    "import socket; s = socket.socket(); "
                    f"s.bind(('127.0.0.1', {port})); s.listen(); s.accept()"
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


def test_teardown_failure_is_visible_and_does_not_skip_other_cleanup() -> None:
    stopped: list[str] = []

    @dataclass(eq=False)
    class Resource(Value):
        def teardown(self, value: str) -> None:
            stopped.append(value)
            if value == "broken":
                raise RuntimeError("cannot stop")

    @dataclass(eq=False)
    class Consumer(Step[None]):
        one: Step[str]
        two: Step[str]

        def build(self, run: Run) -> None:
            pass

    one, two = Resource("one"), Resource("broken")
    two.name = "broken"
    with pytest.raises(StepFailed, match="broken.*cannot stop"):
        execute([one, two, Consumer(one, two)])
    assert stopped == ["broken", "one"]


def test_failure_keeps_its_name_when_teardown_also_fails() -> None:
    class Broken(Value):
        def teardown(self, value: str) -> None:
            raise RuntimeError("stop broke")

    @dataclass(eq=False)
    class Fails(Step[None]):
        resource: Step[str]

        def build(self, run: Run) -> None:
            raise RuntimeError("rejected")

    resource = Broken("resource")
    step = Fails(resource)
    step.name = "consumer"
    with pytest.raises(StepFailed, match="consumer failed: rejected"):
        execute([resource, step])


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


def test_run_artifact_uses_step_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wright.run.repository_root", lambda: tmp_path)
    step = Value("value")
    step.name = "jammer:api"
    path = Run().artifact(step, ".yaml")
    assert path == tmp_path / "target" / "wright" / "jammer_api.yaml"
    assert path.parent.is_dir()


def test_run_releases_values_at_end() -> None:
    runs: list[Run] = []

    class Capture(Value):
        def build(self, run: Run) -> str:
            runs.append(run)
            return super().build(run)

    step = Capture("value")
    execute([step])
    with pytest.raises(WrightError, match="not available"):
        runs[0][step]


@pytest.mark.parametrize(
    ("package", "fixture", "executable"),
    [
        (
            "jammer_server",
            "cargo-build-jammer-server.jsonl",
            "/tmp/wright-cargo-ws-o7sjppt2/target/debug/jammer-server",
        ),
        (
            "sample",
            "cargo-build-sample.jsonl",
            "/tmp/wright-cargo-sample-48b05n8f/sample/target/debug/sample",
        ),
    ],
)
def test_rust_binary_picks_executable_from_captured_cargo_json(
    package: str, fixture: str, executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    def command(
        self: Step[object], *args: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(
            args, 0, (FIXTURES / fixture).read_text(), ""
        )

    monkeypatch.setattr(Step, "_cmd", command)
    step = RustBinary(CargoPackage(package))
    assert execute([step]) == Path(executable)
    assert captured == [
        ("cargo", "build", "--locked", "-p", package, "--message-format=json")
    ]


def test_rust_binary_rejects_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Step, "_cmd", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", "")
    )
    with pytest.raises(StepFailed, match="produced no executable"):
        execute([RustBinary(CargoPackage("missing"))])


def test_cli_names_failing_step_and_does_not_run_later_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: RustBinary, run: Run) -> Path:
        raise RuntimeError("cannot compile")

    def unexpected(self: OpenApi, run: Run) -> Path:
        raise AssertionError("ran after failure")

    monkeypatch.setattr(RustBinary, "build", fail)
    monkeypatch.setattr(OpenApi, "build", unexpected)
    with pytest.raises(SystemExit, match="jammer_server failed: cannot compile"):
        cli.run(repository_root() / "jammer", "api")


@pytest.mark.parametrize("failed", [False, True])
def test_tests_step_reports_failures_and_messages(failed: bool, capsys: pytest.CaptureFixture[str]) -> None:
    class Check(Checks):
        def build(self, run: Run) -> Results:
            return Results(
                failures=(Failure("GET /health", "Received 200; documented 418"),) if failed else (),
                messages=("checked API",),
            )

    check = Check()
    check.name = "fuzzed"
    if failed:
        with pytest.raises(StepFailed, match="fuzzed failed:.*GET /health.*Received 200; documented 418"):
            execute([check])
    else:
        assert execute([check]) == Results(messages=("checked API",))
    assert "checked API" in capsys.readouterr().out


def test_commands_run_in_the_steps_base_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    class Command(Step[str]):
        def build(self, run: Run) -> str:
            return self._cmd(sys.executable, "-c", "from pathlib import Path; print(Path.cwd())", capture_output=True).stdout.strip()

    assert execute([Command()]) == str(tmp_path.resolve())


def test_started_commands_run_in_the_steps_base_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
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

    execute([Server()])
    assert marker.read_text() == str(tmp_path.resolve())
