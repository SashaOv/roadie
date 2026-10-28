import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import roadie
from roadie import (
    Binary,
    CargoPackage,
    File,
    Group,
    HttpService,
    RoadieError,
    Service,
    Step,
    cli,
)
from roadie import TestFailure as Failure
from roadie import TestResults as Results
from roadie import Tests as Checks
from roadie.errors import StepFailed
from roadie.fingerprint import fingerprint
from roadie.graph import Graph, load
from roadie.inputs import Files
from roadie.process import RunningProcess, available_port
from roadie.root import find_project_root, project_root, use_project_root
from roadie.run import Run
from roadie.run import execute as _execute
from roadie.steps import RustBinary

FIXTURES = Path(__file__).parent / "fixtures"
_forced_root: Path | None = None


def execute(steps, **kwargs):
    root = kwargs.pop("root", None)
    if root is None:
        root = _forced_root if _forced_root is not None else find_project_root(Path.cwd())
    return _execute(steps, root=root, **kwargs)


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
    graph = Graph(tmp_path / "build.py", {"last": third}, tmp_path)
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


def test_run_artifact_uses_the_build_directory_and_variable(tmp_path: Path) -> None:
    with use_project_root(tmp_path):
        jammer = tmp_path / "jammer"
        other = tmp_path / "other"
        first, second = Value("one"), Value("two")
        first.variable, first.build_dir = "api", jammer
        second.variable, second.build_dir = "api", other
        run = Run()

        assert run.artifact(first, ".yaml") == tmp_path / "target" / "roadie" / "jammer" / "api.yaml"
        assert run.artifact(second, ".yaml") == tmp_path / "target" / "roadie" / "other" / "api.yaml"
        # build_dir is outside the repository root the run was given.
        external = Value("ext")
        external.variable = "api"
        external.build_dir = Path("/tmp/roadie-outside-build")
        copied = run.artifact(external, ".yaml")
        assert copied.parent.parent == tmp_path / "target" / "roadie" / "_outside"
        assert len(copied.parent.name) == 12
        assert copied.name == "api.yaml"
        unnamed = Value("loose")
        assert run.artifact(unnamed, ".txt") == tmp_path / "target" / "roadie" / f"Value-{unnamed.serial}.txt"


def test_run_releases_values_at_end() -> None:
    runs: list[Run] = []

    class Capture(Value):
        def build(self, run: Run) -> str:
            runs.append(run)
            return super().build(run)

    step = Capture("value")
    execute([step])
    with pytest.raises(RoadieError, match="not available"):
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
    monkeypatch.setattr(
        "roadie.cargo.CargoPackage.fingerprint",
        lambda self, base_dir: f"package:{self.name}",
    )
    source = Path(executable)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"binary")
    source.chmod(0o755)
    step = RustBinary(CargoPackage(package))
    copied = execute([step])
    assert copied != source
    assert copied.read_bytes() == b"binary"
    assert copied.stat().st_mode == source.stat().st_mode
    assert captured == [
        ("cargo", "build", "--locked", "-p", package, "--message-format=json")
    ]


def test_rust_binary_rejects_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roadie.cargo.CargoPackage.fingerprint",
        lambda self, base_dir: f"package:{self.name}",
    )
    monkeypatch.setattr(
        Step, "_cmd", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", "")
    )
    with pytest.raises(StepFailed, match="produced no executable"):
        execute([RustBinary(CargoPackage("missing"))])


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
    _isolate_repo(monkeypatch, tmp_path)

    class Command(Step[str]):
        def build(self, run: Run) -> str:
            return self._cmd(sys.executable, "-c", "from pathlib import Path; print(Path.cwd())", capture_output=True).stdout.strip()

    assert execute([Command()]) == str(tmp_path.resolve())


def test_started_commands_run_in_the_steps_base_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
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


def test_unnamed_steps_of_one_class_get_distinct_artifacts(tmp_path: Path) -> None:
    with use_project_root(tmp_path):
        first, second = Value("first"), Value("second")
        run = Run()
        one, two = run.artifact(first, ".txt"), run.artifact(second, ".txt")
        assert one != two
        assert one.name == f"Value-{first.serial}.txt"
        assert two.name == f"Value-{second.serial}.txt"


def test_group_value_contains_its_steps_values() -> None:
    first, second = Value("first"), Value("second")
    group = Group(first, second)
    assert execute([first, second, group]) == ("first", "second")
    assert execute([Group()]) == ()


@dataclass(eq=False)
class TextFile(File):
    text: str

    def build(self, run: Run) -> Path:
        artifact = run.artifact(self, ".txt")
        artifact.write_text(self.text)
        return artifact


@pytest.mark.parametrize("previous", [None, "old\n", "new\n"])
def test_file_output_is_published_and_equal_file_is_not_rewritten(previous: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    destination = tmp_path / "nested/output.txt"
    if previous is not None:
        destination.parent.mkdir()
        destination.write_text(previous)
        os.utime(destination, ns=(1_000_000_000, 1_000_000_000))
    step = TextFile("new\n", output="nested/output.txt")
    assert execute([step]) == destination
    assert destination.read_text() == "new\n"
    output = capsys.readouterr().out
    if previous == "new\n":
        assert output == ""
        assert destination.stat().st_mtime_ns == 1_000_000_000
    else:
        assert f"updated {destination}" in output


@pytest.mark.parametrize("previous", [None, "old\n", "new\n"])
@pytest.mark.parametrize("tracked", [False, True])
def test_locked_file_output_protects_tracked_files_and_writes_untracked_files(previous: str | None, tracked: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    destination = tmp_path / "output.txt"
    destination.write_text(previous or "old\n")
    if tracked:
        subprocess.run(["git", "add", "output.txt"], cwd=tmp_path, check=True)
    if previous is None:
        destination.unlink()
    step = TextFile("new\n", output=Path("output.txt"))
    step.name = "generated"
    if tracked and previous != "new\n":
        with pytest.raises(StepFailed) as error:
            execute([step], locked=True)
        assert "generated failed" in str(error.value)
        assert str(destination) in str(error.value)
        diff = capsys.readouterr().out
        assert "--- " in diff and "+++ " in diff and "+new\n" in diff
        assert (destination.read_text() if destination.exists() else None) == previous
    else:
        assert execute([step], locked=True) == destination
        assert destination.read_text() == "new\n"


def test_file_output_value_is_available_to_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)

    @dataclass(eq=False)
    class Consumer(Step[str]):
        source: File

        def build(self, run: Run) -> str:
            assert run[self.source] == tmp_path / "output.txt"
            return run[self.source].read_text()

    source = TextFile("new\n", output="output.txt")
    assert execute([source, Consumer(source)]) == "new\n"


def test_file_output_failure_releases_the_built_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    destination = tmp_path / "output.txt"
    destination.mkdir()
    released: list[Path] = []

    class ReleasingFile(TextFile):
        def teardown(self, value: Path) -> None:
            released.append(value)

    step = ReleasingFile("new\n", output="output.txt")
    with pytest.raises(StepFailed, match="output.txt"):
        execute([step])
    with use_project_root(tmp_path):
        assert released == [Run().artifact(step, ".txt")]


def test_file_output_is_written_under_locked_outside_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    destination = tmp_path / "new/dir/output.txt"
    assert execute([TextFile("new\n", output=destination)], locked=True) == destination


def test_locked_file_output_reports_git_failure_and_keeps_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    (tmp_path / ".git").write_text("invalid git metadata\n")
    destination = tmp_path / "output.txt"
    destination.write_text("old\n")
    with pytest.raises(StepFailed) as error:
        execute([TextFile("new\n", output=destination)], locked=True)
    assert str(destination) in str(error.value)
    assert "Cannot check whether" in str(error.value)
    assert destination.read_text() == "old\n"


class _Exited:
    def wait(self) -> int:
        return 0

    def poll(self) -> int:
        return 0


class _Stopped:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:9"
        self.process = _Exited()

    def stop(self) -> None:
        return None


def _isolate_repo(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(f"{__name__}._forced_root", root)


def _write_build(directory: Path, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from roadie import *\n{source}")


_EMITTER = """
from dataclasses import dataclass
from pathlib import Path
from roadie.run import Run

@dataclass(eq=False)
class Emitter(File):
    text: str = "v1"
    def build(self, run: Run) -> Path:
        path = run.artifact(self, ".txt")
        path.write_text(self.text)
        (self.build_dir / "ran.txt").write_text("ran")
        return path

api = Emitter()
"""


def test_second_run_reuses_a_step_and_replays_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    built: list[str] = []

    @dataclass(eq=False)
    class Note(File):
        def build(self, run: Run) -> Path:
            built.append("note")
            path = run.artifact(self, ".txt")
            path.write_text("body")
            return path

    @dataclass(eq=False)
    class Logged(Checks):
        def build(self, run: Run) -> Results:
            built.append("logged")
            return Results((), (f"log-{len(built)}",))

    class Always(Checks):
        reusable = False

        def build(self, run: Run) -> Results:
            built.append("always")
            return Results((), ("always",))

    class Server(Service):
        def build(self, run: Run) -> _Stopped:
            built.append("server")
            return _Stopped()

    note, logged, always, server = Note(), Logged(), Always(), Server()
    for step, name in ((note, "note"), (logged, "logged"), (always, "always"), (server, "server")):
        step.name = name
        step.variable = name
    execute([note, logged, always, server])
    assert built == ["note", "logged", "always", "server"]
    built.clear()
    execute([note, logged, always, server])
    output = capsys.readouterr().out
    assert built == ["always", "server"]
    assert "reused note" in output
    assert "reused logged" in output
    assert "log-2" in output


def test_changed_files_rerun_a_step_and_identical_output_reuses_consumers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    (root / "input.txt").write_text("one")
    built: list[str] = []

    @dataclass(eq=False)
    class Note(File):
        files: Files
        note: str = "a"

        def build(self, run: Run) -> Path:
            built.append("note")
            path = run.artifact(self, ".txt")
            path.write_text((self.base_dir / "input.txt").read_text())
            return path

    @dataclass(eq=False)
    class Downstream(File):
        source: File

        def build(self, run: Run) -> Path:
            built.append("down")
            path = run.artifact(self, ".out")
            path.write_text(run[self.source].read_text() + "!")
            return path

    note = Note(Files("input.txt"))
    down = Downstream(note)
    note.name = note.variable = "note"
    down.name = down.variable = "down"
    execute([note, down])
    built.clear()
    execute([note, down])
    assert built == []

    (root / "input.txt").write_text("two")
    execute([note, down])
    assert built == ["note", "down"]

    built.clear()
    note.note = "b"
    execute([note, down])
    assert built == ["note"]


def test_failed_step_writes_no_record_and_a_changed_output_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    state = {"fail": True}

    @dataclass(eq=False)
    class Note(File):
        def build(self, run: Run) -> Path:
            if state["fail"]:
                raise RuntimeError("nope")
            path = run.artifact(self, ".txt")
            path.write_text("body")
            return path

    note = Note()
    note.name = note.variable = "note"
    note.build_variables = frozenset({"note"})
    with pytest.raises(StepFailed, match="nope"):
        execute([note])
    record = root / "target" / "roadie" / "build.record.json"
    assert not record.exists()

    state["fail"] = False
    execute([note])
    assert record.is_file()
    payload = json.loads(record.read_text())
    payload["gone"] = {"fingerprint": "x", "output": "y", "content": None, "value": "z"}
    record.write_text(json.dumps(payload))
    produced = execute([note])
    assert "gone" not in record.read_text()
    produced.unlink()
    again = execute([note])
    assert again.read_text() == "body"
    again.write_text("edited")
    rebuilt = execute([note])
    assert rebuilt.read_text() == "body"


def test_locked_run_does_not_reuse_a_hand_edited_tracked_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    @dataclass(eq=False)
    class Note(File):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, ".txt")
            path.write_text("body")
            return path

    note = Note(output="output.txt")
    note.name = note.variable = "note"
    destination = execute([note])
    subprocess.run(["git", "add", "output.txt"], cwd=root, check=True)
    destination.write_text("hand edited\n")
    with pytest.raises(StepFailed, match="output.txt"):
        execute([note], locked=True)
    assert destination.read_text() == "hand edited\n"


def test_tests_consumer_is_reused_when_the_replayed_log_would_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    built: list[str] = []

    @dataclass(eq=False)
    class Logged(Checks):
        def build(self, run: Run) -> Results:
            built.append("logged")
            return Results((), (f"log-{len(built)}",))

    @dataclass(eq=False)
    class Downstream(File):
        source: Checks

        def build(self, run: Run) -> Path:
            built.append("down")
            path = run.artifact(self, ".out")
            path.write_text("ok")
            return path

    logged = Logged()
    down = Downstream(logged)
    logged.name = logged.variable = "logged"
    down.name = down.variable = "down"
    execute([logged, down])
    built.clear()
    execute([logged, down])
    output = capsys.readouterr().out
    assert built == []
    assert "reused down" in output
    assert "log-1" in output


def test_rust_binary_is_rebuilt_when_its_recorded_copy_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    source = root / "compiler-output"
    source.write_bytes(b"v1")
    source.chmod(0o755)
    message = (
        '{"reason":"compiler-artifact","package_id":"path+file:///tmp/app#app@0.1.0",'
        f'"target":{{"kind":["bin"]}},"executable":"{source}"}}\n'
    )
    calls: list[tuple[str, ...]] = []

    def command(self: Step[object], *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, message, "")

    monkeypatch.setattr(Step, "_cmd", command)
    monkeypatch.setattr("roadie.cargo.CargoPackage.fingerprint", lambda self, base_dir: "package:app")
    step = RustBinary(CargoPackage("app"))
    step.name = step.variable = "app"
    first = execute([step])
    assert first.read_bytes() == b"v1"
    execute([step])
    assert len(calls) == 1
    first.write_bytes(b"tampered")
    execute([step])
    assert len(calls) == 2
    assert first.read_bytes() == b"v1"


def test_build_files_keep_separate_records_and_include_reuses_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    _write_build(root / "jammer", _EMITTER)
    _write_build(root / "other", _EMITTER)
    execute(load(root / "jammer").plan())
    execute(load(root / "other").plan())
    assert (root / "target" / "roadie" / "jammer" / "api.txt").is_file()
    assert (root / "target" / "roadie" / "other" / "api.txt").is_file()
    assert (root / "target" / "roadie" / "jammer" / "build.record.json").is_file()
    assert (root / "target" / "roadie" / "other" / "build.record.json").is_file()

    _write_build(root / "module", _EMITTER)
    _write_build(root, 'module = include("module")\nresult = module.api\n')
    execute(load(root).plan("result"))
    ran = root / "module" / "ran.txt"
    assert ran.is_file()
    ran.unlink()
    execute(load(root / "module").plan())
    assert not ran.exists()
    assert (root / "target" / "roadie" / "module" / "api.txt").read_text() == "v1"


def test_dry_run_marks_reuse_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    project = root / "project"
    _write_build(project, _EMITTER.replace("api = Emitter()", 'api = Emitter("v1")\n'))
    cli.run(project)
    capsys.readouterr()
    cli.run(project, dry_run=True)
    assert capsys.readouterr().out.splitlines() == ["api = Emitter('v1')  # reuse"]
    _write_build(project, _EMITTER.replace("api = Emitter()", 'api = Emitter("v2")\n'))
    cli.run(project, dry_run=True)
    assert capsys.readouterr().out.splitlines() == ["api = Emitter('v2')  # run"]


def test_service_steps_are_not_reusable() -> None:
    assert Step.reusable is True
    assert Service.reusable is False


def test_plain_attribute_of_unsupported_type_is_an_error() -> None:
    @dataclass(eq=False)
    class Custom(Step[None]):
        value: object

        def build(self, run: Run) -> None:
            pass

    step = Custom({"bad": 1})
    step.name = "custom"
    with pytest.raises(RoadieError, match="custom attribute value must be"):
        fingerprint(step)


def test_step_fingerprint_covers_files_dependency_outputs_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    with use_project_root(root):
        bundle = root / "dist"
        bundle.mkdir()
        (bundle / "app.js").write_text("one")
        outside = root / "outside.txt"
        outside.write_text("one")
        (bundle / "link.js").symlink_to(outside)

        @dataclass(eq=False)
        class Bundle(Step[None]):
            files: Files
            source: Step[str]

            def build(self, run: Run) -> None:
                pass

        source = Value("built")
        step = Bundle(Files("dist"), source)
        outputs = {source: "a" * 64}
        original = fingerprint(step, outputs=outputs)
        (bundle / "app.js").write_text("two")
        edited = fingerprint(step, outputs=outputs)
        assert edited != original
        outside.write_text("two")
        followed = fingerprint(step, outputs=outputs)
        assert followed != edited
        assert fingerprint(step, outputs={source: "b" * 64}) != followed
        assert fingerprint(step, outputs=outputs, env={"PORT": "1"}) != fingerprint(step, outputs=outputs, env={})
        step.env_values = {"PORT": "9"}
        assert fingerprint(step, outputs=outputs) == fingerprint(step, outputs=outputs, env={"PORT": "9"})
        step.env_values = {"PORT": "10"}
        assert fingerprint(step, outputs=outputs) != fingerprint(step, outputs=outputs, env={"PORT": "9"})

        missing = Files("absent")
        before = missing.fingerprint(root)
        (root / "absent").write_text("created")
        assert missing.fingerprint(root) != before


def test_http_service_waits_for_its_health_path_and_fails_when_the_process_exits(tmp_path: Path) -> None:
    port = available_port()
    ready = tmp_path / "ready"
    script = (
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "from pathlib import Path\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    hits = 0\n"
        "    def do_GET(self):\n"
        "        Handler.hits += 1\n"
        "        ok = self.path == '/health' and Handler.hits >= 3\n"
        "        if ok:\n"
        f"            Path({str(ready)!r}).write_text('ok')\n"
        "        self.send_response(200 if ok else 503)\n"
        "        self.end_headers()\n"
        "    def log_message(self, format, *args):\n"
        "        return\n"
        f"ThreadingHTTPServer(('127.0.0.1', {port}), Handler).serve_forever()\n"
    )

    class Api(HttpService):
        health_path = "/health"

        def build(self, run: Run) -> RunningProcess:
            return self._start_cmd(sys.executable, "-c", script, port=port, timeout=5)

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


def test_service_starts_only_when_a_consumer_must_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    events: list[str] = []

    class Program(Binary):
        def build(self, run: Run) -> Path:
            events.append("binary")
            path = run.artifact(self, "")
            path.write_text("exe")
            return path

    @dataclass(eq=False)
    class Note(File):
        label: str = "a"

        def build(self, run: Run) -> Path:
            events.append("note")
            path = run.artifact(self, ".txt")
            path.write_text(self.label)
            return path

    @dataclass(eq=False)
    class Server(Service):
        binary: Binary

        def build(self, run: Run) -> _Stopped:
            events.append("server")
            assert run[self.binary].read_text() == "exe"
            return _Stopped()

        def teardown(self, value: _Stopped) -> None:
            events.append("stop")

    @dataclass(eq=False)
    class Between(File):
        def build(self, run: Run) -> Path:
            events.append("between")
            path = run.artifact(self, ".txt")
            path.write_text("between")
            return path

    @dataclass(eq=False)
    class Consumer(File):
        source: File
        server: Service

        def build(self, run: Run) -> Path:
            events.append("consumer")
            assert run[self.server].url == "http://127.0.0.1:9"
            path = run.artifact(self, ".out")
            path.write_text(run[self.source].read_text())
            return path

    binary, note, between = Program(), Note(), Between()
    server = Server(binary)
    consumer = Consumer(note, server)
    for step, name in (
        (binary, "binary"),
        (note, "note"),
        (server, "server"),
        (between, "between"),
        (consumer, "consumer"),
    ):
        step.name = step.variable = name
    plan = [binary, note, server, between, consumer]

    execute(plan)
    assert events == ["binary", "note", "between", "server", "consumer", "stop"]
    events.clear()
    capsys.readouterr()

    execute(plan)
    output = capsys.readouterr().out
    assert events == []
    assert "not started server: no step that takes it runs" in output
    assert "reused consumer" in output

    events.clear()
    note.label = "b"
    execute(plan)
    output = capsys.readouterr().out
    assert events == ["note", "server", "consumer", "stop"]
    assert "reused between" in output
    assert "not started" not in output


class _Deployment:
    def __init__(self, status: int | None = 0, interrupt: bool = False) -> None:
        self.url = "http://127.0.0.1:9"
        self.process = self
        self.status = status
        self.interrupt = interrupt
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.stopped = False

    def wait(self) -> int:
        if self.interrupt:
            self.waiting.set()
            if not self.release.wait(5):
                raise AssertionError("the service was not interrupted")
            raise KeyboardInterrupt
        self.waiting.set()
        assert self.status is not None
        return self.status

    def stop(self) -> None:
        self.stopped = True


def _server(deployment: object) -> Service:
    class Server(Service):
        def build(self, run: Run) -> object:
            return deployment

    server = Server()
    server.name = "server"
    return server


def test_requested_service_stays_up_until_ctrl_c(capsys: pytest.CaptureFixture[str]) -> None:
    deployment = _Deployment(interrupt=True)

    def release() -> None:
        assert deployment.waiting.wait(2)
        assert deployment.stopped is False
        deployment.release.set()

    threading.Thread(target=release).start()
    execute([_server(deployment)])
    assert deployment.stopped is True
    output = capsys.readouterr().out
    assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
    assert "server exited" not in output


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
    execute([_server(sleeper)])
    assert sleeper.stopped is True
    output = capsys.readouterr().out
    assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
    assert "server exited" not in output


@pytest.mark.parametrize("status", [0, 3])
def test_requested_service_exit_status(status: int, capsys: pytest.CaptureFixture[str]) -> None:
    deployment = _Deployment(status=status)
    if status == 0:
        execute([_server(deployment)])
        output = capsys.readouterr().out
        assert "server is up: http://127.0.0.1:9 (Ctrl-C to stop)" in output
        assert "server exited" in output
    else:
        with pytest.raises(StepFailed, match="server failed: exited with status 3"):
            execute([_server(deployment)])
    assert deployment.stopped is True


def test_cli_ctrl_c_on_a_requested_service_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_build(
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


_STDIN_SERVER = """
import sys

from roadie.process import available_port


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


@pytest.mark.parametrize(("typed", "status"), [(b"b\n", 0), (b"x\n", 1)])
def test_requested_service_reads_the_terminal_input(
    typed: bytes, status: int, tmp_path: Path
) -> None:
    # A dev server's keyboard shortcuts (Vite's b and q) need its stdin.
    _write_build(tmp_path, _STDIN_SERVER)
    finished = subprocess.run(
        (sys.executable, "-c", "from roadie.cli import main; main()", str(tmp_path)),
        cwd=Path(roadie.__file__).resolve().parent.parent,
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


def test_env_files_override_step_settings_and_not_the_reverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _isolate_repo(monkeypatch, tmp_path)
    monkeypatch.setenv("PORT", "process")
    monkeypatch.setenv("EXTRA", "process")
    first = tmp_path / "a.env"
    second = tmp_path / "b.env"
    first.write_text("PORT=a\nNAME=from-a\n")
    second.write_text("PORT=b\n")
    seen: list[dict[str, str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        seen.append(environment)
        return subprocess.CompletedProcess((), 0, "", "")

    def fake_start(*args: object, **kwargs: object) -> _Stopped:
        environment = kwargs["environment"]
        assert isinstance(environment, dict)
        seen.append(environment)
        return _Stopped()

    monkeypatch.setattr("roadie.step.subprocess.run", fake_run)
    monkeypatch.setattr("roadie.step.RunningProcess.start", fake_start)

    class Server(Service):
        def build(self, run: Run) -> _Stopped:
            return self._start_cmd(
                "serve",
                port=9,
                environment={"PORT": "step", "NAME": "step", "ONLY": "step"},
            )

    @dataclass(eq=False)
    class After(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            self._cmd("tool", environment={"PORT": "step", "NAME": "step", "ONLY": "step"})

    server = Server()
    execute([server, After(server)], env=(first, second))
    assert [item["PORT"] for item in seen] == ["b", "b"]
    assert [item["NAME"] for item in seen] == ["from-a", "from-a"]
    assert [item["ONLY"] for item in seen] == ["step", "step"]
    assert [item["EXTRA"] for item in seen] == ["process", "process"]

    seen.clear()
    server.env_files = ()
    execute([server, After(server)])
    assert seen[0]["PORT"] == "step"
    assert seen[0]["EXTRA"] == "process"


def test_changed_env_file_reruns_a_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    env_file = root / "dev.env"
    env_file.write_text("MODE=one\n")
    built: list[str] = []

    @dataclass(eq=False)
    class Note(File):
        def build(self, run: Run) -> Path:
            built.append(self.env_values["MODE"])
            path = run.artifact(self, ".txt")
            path.write_text(self.env_values["MODE"])
            return path

    note = Note()
    note.name = note.variable = "note"
    note.env_files = (env_file,)
    execute([note])
    execute([note])
    assert built == ["one"]
    env_file.write_text("MODE=two\n")
    execute([note])
    assert built == ["one", "two"]


def test_missing_env_file_stops_before_any_step(tmp_path: Path) -> None:
    missing = tmp_path / "nope.env"

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            raise AssertionError("ran")

    note = Note()
    note.env_files = (missing,)
    with pytest.raises(RoadieError, match="environment file does not exist:.*nope.env"):
        execute([note])


def test_config_env_applies_before_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_repo(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    _write_build(
        tmp_path,
        "from pathlib import Path\n"
        "class Probe(Step[None]):\n"
        "    def build(self, run):\n"
        "        Path('seen.txt').write_text(self.env_values['MODE'] + ' ' + self.env_values['TOKEN'])\n"
        'config(env=["from-config.env"])\n'
        "probe = Probe()\n",
    )
    (tmp_path / "from-config.env").write_text("MODE=config\nTOKEN=from-config\n")
    cli_env = tmp_path / "cli.env"
    cli_env.write_text("MODE=cli\n")
    execute(load(tmp_path).plan(), env=(cli_env,))
    assert (tmp_path / "seen.txt").read_text() == "cli from-config"


def _git(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)


def test_project_root_outside_load_and_execute_is_an_error() -> None:
    with pytest.raises(
        RoadieError,
        match="no project root: run steps through roadie's load and execute",
    ):
        project_root()


def test_subdirectory_of_a_work_tree_records_under_the_top_level(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    _write_build(repo / "jammer", _EMITTER)
    graph = load(repo / "jammer")
    assert graph.root == repo.resolve()
    execute(graph.plan(), root=graph.root)
    assert (repo / "target" / "roadie" / "jammer" / "api.txt").is_file()
    assert not (repo / "jammer" / "target").exists()


def test_build_outside_a_work_tree_records_under_its_own_directory(tmp_path: Path) -> None:
    _write_build(tmp_path, _EMITTER)
    graph = load(tmp_path)
    assert graph.root == tmp_path.resolve()
    execute(graph.plan(), root=graph.root)
    assert (tmp_path / "target" / "roadie" / "api.txt").read_text() == "v1"
    assert not (tmp_path / "jammer").exists()


def test_included_file_shares_the_including_files_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    _git(repo)
    _write_build(other, _EMITTER)
    _write_build(repo / "app", 'other = include("../../other")\nresult = other.api\n')
    graph = load(repo / "app")
    assert graph.root == repo.resolve()
    execute(graph.plan("result"), root=graph.root)
    assert not (other / "target").exists()
    recorded = list((repo / "target" / "roadie" / "_outside").rglob("api.txt"))
    assert recorded
    assert recorded[0].read_text() == "v1"
