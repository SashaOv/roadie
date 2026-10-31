import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import (
    EMITTER,
    Stopped,
    Value,
    execute,
    isolate_repo,
    write_build,
)

from roadie import cli
from roadie.build import (
    Binary,
    File,
    Files,
    RoadieError,
    Service,
    Step,
)
from roadie.build import TestResults as Results
from roadie.build import Tests as Checks
from roadie.engine.errors import StepFailed
from roadie.engine.graph import Graph, load
from roadie.engine.root import use_project_root
from roadie.engine.run import Run


# (spec) Plan order
# (spec) Step values
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


# (spec) Teardown
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


# (spec) Teardown
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


# (spec) Teardown
# (spec) Failures
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


# (spec) Artifacts
def test_run_artifact_uses_the_build_directory_and_variable(tmp_path: Path) -> None:
    with use_project_root(tmp_path):
        services = tmp_path / "services"
        other = tmp_path / "other"
        first, second = Value("one"), Value("two")
        first.variable, first.build_dir = "api", services
        second.variable, second.build_dir = "api", other
        run = Run()

        assert run.artifact(first, ".yaml") == tmp_path / "target" / "roadie" / "services" / "api.yaml"
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


# (spec) Step values
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


# (spec) Artifacts
def test_unnamed_steps_of_one_class_get_distinct_artifacts(tmp_path: Path) -> None:
    with use_project_root(tmp_path):
        first, second = Value("first"), Value("second")
        run = Run()
        one, two = run.artifact(first, ".txt"), run.artifact(second, ".txt")
        assert one != two
        assert one.name == f"Value-{first.serial}.txt"
        assert two.name == f"Value-{second.serial}.txt"


# (spec) Reusing a step
# (spec) Step kinds
def test_second_run_reuses_a_step_and_replays_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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
        def build(self, run: Run) -> Stopped:
            built.append("server")
            return Stopped()

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


# (spec) Reusing a step
# (spec) Step hooks
# (spec) Files input
def test_changed_files_rerun_a_step_and_identical_output_reuses_consumers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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


# (spec) Records
# (spec) Reusing a step
def test_failed_step_writes_no_record_and_a_changed_output_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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
    payload["entries"]["gone"] = {"fingerprint": "x", "output": "y", "value": "z"}
    record.write_text(json.dumps(payload))
    produced = execute([note])
    assert "gone" not in record.read_text()
    produced.unlink()
    again = execute([note])
    assert again.read_text() == "body"
    again.write_text("edited")
    rebuilt = execute([note])
    assert rebuilt.read_text() == "body"


def _counted(name: str, built: list[str]) -> Step[str]:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            built.append(name)
            return name

    note = Note()
    note.name = note.variable = name
    note.build_variables = frozenset({name})
    return note


# (spec) Records
def test_record_file_keeps_a_version_and_reuses_its_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    built: list[str] = []
    execute([_counted("note", built)])
    record = json.loads((root / "target" / "roadie" / "build.record.json").read_text())
    assert record["version"] == 1
    assert set(record["entries"]) == {"note"}
    execute([_counted("note", built)])
    assert built == ["note"]


# (spec) Records
def test_record_of_another_version_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    built: list[str] = []
    note = _counted("note", built)
    execute([note])
    record = root / "target" / "roadie" / "build.record.json"
    payload = json.loads(record.read_text())
    payload["version"] = 2
    record.write_text(json.dumps(payload))
    capsys.readouterr()
    execute([note])
    assert built == ["note", "note"]
    assert "ignoring unreadable record" not in capsys.readouterr().out


# (spec) Records
def test_unreadable_record_is_ignored_and_the_step_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    built: list[str] = []
    note = _counted("note", built)
    execute([note])
    record = root / "target" / "roadie" / "build.record.json"
    record.write_text("not json")
    capsys.readouterr()
    execute([note])
    assert built == ["note", "note"]
    message = (
        f"ignoring unreadable record {record}: Expecting value: line 1 column 1 (char 0)"
    )
    assert message in capsys.readouterr().out


# (spec) Records
# (spec) Command line
def test_unreadable_record_does_not_fail_a_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    write_build(root, "class Note(Step[str]):\n    def build(self, run):\n        return 'v'\nitem = Note()\n")
    cli.run(root)
    record = root / "target" / "roadie" / "build.record.json"
    record.write_text("not json")
    capsys.readouterr()
    cli.run(root, dry_run=True)
    output = capsys.readouterr().out
    assert f"ignoring unreadable record {record}:" in output
    assert "item = Note()  # run" in output


# (spec) Records
def test_build_files_keep_separate_records_and_include_reuses_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    write_build(root / "services", EMITTER)
    write_build(root / "other", EMITTER)
    execute(load(root / "services").plan())
    execute(load(root / "other").plan())
    assert (root / "target" / "roadie" / "services" / "api.txt").is_file()
    assert (root / "target" / "roadie" / "other" / "api.txt").is_file()
    assert (root / "target" / "roadie" / "services" / "build.record.json").is_file()
    assert (root / "target" / "roadie" / "other" / "build.record.json").is_file()

    write_build(root / "module", EMITTER)
    write_build(root, 'module = include("module")\nresult = module.api\n')
    execute(load(root).plan("result"))
    ran = root / "module" / "ran.txt"
    assert ran.is_file()
    ran.unlink()
    execute(load(root / "module").plan())
    assert not ran.exists()
    assert (root / "target" / "roadie" / "module" / "api.txt").read_text() == "v1"


# (spec) Step kinds
def test_service_starts_only_when_a_consumer_must_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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

        def build(self, run: Run) -> Stopped:
            events.append("server")
            assert run[self.binary].read_text() == "exe"
            return Stopped()

        def teardown(self, value: Stopped) -> None:
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


# (spec) Environment files
def test_env_files_override_step_settings_and_not_the_reverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    monkeypatch.setenv("PORT", "process")
    monkeypatch.setenv("EXTRA", "process")
    first = tmp_path / "a.env"
    second = tmp_path / "b.env"
    first.write_text("PORT=a\nNAME=from-a\n")
    second.write_text("PORT=b\n")
    seen: list[dict[str, str]] = []

    class FakeProcess:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            self.returncode = 0
            self.pid = 1
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            seen.append(environment)

        def communicate(self) -> tuple[str, str]:
            return "", ""

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            return None

    def fake_start(*args: object, **kwargs: object) -> Stopped:
        environment = kwargs["environment"]
        assert isinstance(environment, dict)
        seen.append(environment)
        return Stopped()

    monkeypatch.setattr("roadie.engine.step.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("roadie.engine.step.RunningProcess.start", fake_start)

    class Server(Service):
        def build(self, run: Run) -> Stopped:
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


# (spec) Environment files
# (spec) Step fingerprints
def test_changed_env_file_reruns_a_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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


# (spec) Step hooks
# (spec) Reusing a step
def test_recorded_values_restore_their_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    built: list[str] = []

    class Pair(Step[tuple[int, int]]):
        def build(self, run: Run) -> tuple[int, int]:
            built.append("pair")
            return (1, 2)

    class Child(Pair):
        pass

    class Codes(Step[dict[int, str]]):
        def build(self, run: Run) -> dict[int, str]:
            built.append("codes")
            return {1: "a"}

    class Plain(Step):  # no concrete value type: JSON stays as decoded
        def build(self, run: Run) -> tuple[str, str]:
            built.append("plain")
            return ("a", "b")

    pair = Child()
    pair.name = pair.variable = "pair"
    codes = Codes()
    codes.name = codes.variable = "codes"
    plain = Plain()
    plain.name = plain.variable = "plain"

    assert execute([pair]) == (1, 2)
    restored = execute([pair])
    assert built == ["pair"]
    assert restored == (1, 2)
    assert type(restored) is tuple

    assert execute([codes]) == {1: "a"}
    restored_codes = execute([codes])
    assert built == ["pair", "codes"]
    assert restored_codes == {1: "a"}
    assert type(next(iter(restored_codes))) is int

    assert execute([plain]) == ("a", "b")
    restored_plain = execute([plain])
    assert built == ["pair", "codes", "plain"]
    assert restored_plain == ["a", "b"]

    record = json.loads((root / "target" / "roadie" / "build.record.json").read_text())
    assert record["entries"]["pair"]["value"] == [1, 2]
    assert record["entries"]["codes"]["value"] == {"1": "a"}
    assert record["entries"]["plain"]["value"] == ["a", "b"]


# (spec) Step hooks
# (spec) Reusing a step
def test_recorded_value_of_the_wrong_type_fails_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)

    class Pair(Step[tuple[int, int]]):
        def build(self, run: Run) -> tuple[int, int]:
            return (1, 2)

    pair = Pair()
    pair.name = pair.variable = "pair"
    execute([pair])
    record = root / "target" / "roadie" / "build.record.json"
    payload = json.loads(record.read_text())
    payload["entries"]["pair"]["value"] = "nope"
    record.write_text(json.dumps(payload))
    with pytest.raises(StepFailed, match="pair failed"):
        execute([pair])


# (spec) Environment files
def test_missing_env_file_stops_before_any_step(tmp_path: Path) -> None:
    missing = tmp_path / "nope.env"

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            raise AssertionError("ran")

    note = Note()
    note.env_files = (missing,)
    with pytest.raises(RoadieError, match="environment file does not exist:.*nope.env"):
        execute([note])


# (spec) Environment files
def test_config_env_applies_before_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_repo(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    write_build(
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
