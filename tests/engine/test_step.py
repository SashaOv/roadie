import gc
import os
import subprocess
import sys
import tracemalloc
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from support import Note, execute, isolate_repo, write_build

import roadie
from roadie import cli
from roadie.build import (
    Attribute,
    CargoPackage,
    Check,
    Directory,
    File,
    Files,
    Group,
    Param,
    Pytest,
    Release,
    RustBinary,
    Service,
    Step,
    UserApproval,
    UserTask,
)
from roadie.engine.errors import StepFailed
from roadie.engine.fingerprint import fingerprint
from roadie.engine.graph import load
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
        server: Attribute[Service]

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
        server: Attribute[Service]

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


# (spec) Step helpers
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_failing_command_keeps_only_the_stderr_tail(
    jobs: int, tmp_path: Path
) -> None:
    class Command(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(
                sys.executable,
                "-c",
                "import sys\n"
                "for _ in range(5000): sys.stderr.write('x' * 999 + '\\n')\n"
                "sys.stderr.write('reason')\n"
                "sys.exit(7)",
            )

    step = Command()
    step.name = "tool"
    tracemalloc.start()
    try:
        with pytest.raises(StepFailed) as error:
            execute([step], root=tmp_path, jobs=jobs)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
    assert str(error.value).endswith("reason")
    assert "x" * 2001 not in str(error.value)


# (spec) Step helpers
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_uncaptured_command_keeps_no_stdout(jobs: int, tmp_path: Path) -> None:
    class Command(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(
                sys.executable,
                "-c",
                "for _ in range(5000): print('y' * 999, flush=True)",
            )

    step = Command()
    step.name = "tool"
    tracemalloc.start()
    try:
        execute([step], root=tmp_path, jobs=jobs)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


# (spec) Step hooks
def test_value_adapter_does_not_retain_a_dropped_step_class(tmp_path: Path) -> None:
    def build_once() -> weakref.ReferenceType[type]:
        class Temporary(Step[int]):
            def build(self, run: Run) -> int:
                return 7

        step = Temporary()
        step.name = step.variable = "temporary"
        assert execute([step], root=tmp_path) == 7
        return weakref.ref(Temporary)

    reference = build_once()
    gc.collect()
    assert reference() is None

# (spec) Dependencies and attributes
def test_builtin_steps_declare_their_attributes(tmp_path: Path) -> None:
    class Artifact(File):
        def build(self, run: Run) -> Path:
            return run.artifact(self, ".txt")

    class Site(Directory):
        def build(self, run: Run) -> Path:
            return run.artifact(self, "")

    write_build(tmp_path / "lib", "default_step_result = default_step(Group())")
    write_build(tmp_path, "result = include('lib')")
    included = load(tmp_path).result()
    files = Files("input.txt")
    artifact = Artifact(copy_to="generated.txt")
    steps = [
        Check("python", "-V", inputs=(files, artifact)),
        Pytest("-q", inputs=(files, artifact)),
        UserTask("Open the app", "Does it launch", inputs=(files, artifact)),
        UserApproval("Release?"),
        Release(".", primary="pyproject.toml", requires=artifact, message=Param("message")),
        Site(copy_to="public"),
        artifact,
        RustBinary(CargoPackage("example"), files),
        Group(artifact),
        included,
    ]
    mark = get_args(Attribute[str])[1]
    for step in steps:
        hints = get_type_hints(type(step), include_extras=True)
        marked = {name: getattr(step, name) for name, hint in hints.items() if mark in get_args(hint)[1:]}
        assert marked == step.attributes(), type(step).__name__


# (spec) Dependencies and attributes
def test_step_state_stays_out_of_its_attributes_dependencies_and_fingerprint() -> None:
    class Stateful(Step[None]):
        label: Attribute[str]
        count: int
        _peer: Step[str]
        _cache: object

        def __init__(self) -> None:
            self.label = "marked"
            self.count = 0
            self._peer = Note()

        def build(self, run: Run) -> None:
            self._cache = object()
            self.count += 1

    step = Stateful()
    assert list(step.dependencies()) == []
    assert step.call_arguments() == ([], {"label": "marked"})
    before = fingerprint(step)
    execute([step])
    assert step.count == 1
    assert fingerprint(step) == before
    assert list(step.dependencies()) == []


# (spec) Dependencies and attributes
def test_inherited_attributes_use_declaration_order() -> None:
    class Base(Step[None]):
        first: Attribute[str]
        second: Attribute[str] = "second"

        def build(self, run: Run) -> None:
            return None

    class Child(Base):
        third: Attribute[str]

        def __init__(self) -> None:
            self.third = "third"
            self.first = "first"

    assert list(Child().attributes().items()) == [
        ("first", "first"),
        ("second", "second"),
        ("third", "third"),
    ]


# (spec) Dependencies and attributes
def test_step_field_metadata_lives_only_as_long_as_the_class() -> None:
    def read_fields() -> weakref.ReferenceType[type[Step[None]]]:
        class Temporary(Step[None]):
            label: Attribute[str] = "value"

            def build(self, run: Run) -> None:
                return None

        assert Temporary().attributes() == {"label": "value"}
        return weakref.ref(Temporary)

    cls = read_fields()
    gc.collect()
    assert cls() is None


# (spec) Dependencies and attributes
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry"])
def test_step_annotations_are_read_in_the_build_file_module(
    tmp_path: Path, dry: bool
) -> None:
    root = tmp_path
    (root / "build.py").write_text(
        """from __future__ import annotations
from roadie.build import *

class Custom(Step[str]):
    field: Attribute[Label] = "value"
    quoted: Attribute["Hint"] = "also marked"
    private: list[int] = []

    def build(self, run: Run) -> str:
        return self.field

type Label = str
step = Custom()
""",
    )
    step = load(root).result()
    assert step.attributes() == {"field": "value", "quoted": "also marked"}
    assert execute([step], root=root, dry=dry) == (None if dry else "value")


# (spec) Dependencies and attributes
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry"])
@pytest.mark.parametrize("future", [False, True], ids=["deferred", "future"])
def test_unreadable_step_annotation_names_the_class_and_field(
    tmp_path: Path, dry: bool, future: bool
) -> None:
    if not future and sys.version_info < (3, 14):
        pytest.skip("deferred annotations require Python 3.14")
    source = """
class Custom(Step[None]):
    field: Attribute[Missing] = "value"

    def build(self, run: Run) -> None:
        return None

step = Custom()
"""
    if future:
        source = "from __future__ import annotations\n" + source
    path = tmp_path / "build.py"
    path.write_text(source.replace("\nclass Custom", "\nfrom roadie.build import *\n\nclass Custom", 1))
    with pytest.raises(SystemExit, match=r"Custom\.field annotation cannot be read: name 'Missing' is not defined"):
        cli.run(file=tmp_path, dry_run=dry)


# (spec) Dependencies and attributes
@pytest.mark.parametrize("dry", [False, True], ids=["run", "dry"])
@pytest.mark.parametrize("kind", ["dataclass", "initializer"])
@pytest.mark.parametrize(
    ("declaration", "assignment", "message"),
    [
        ("", "self.extra = 'value'", "attribute extra is not declared in its class"),
        ("extra: Step[None]", "self.extra = Group()", "field extra holds a step but is not an Attribute"),
        ("extra: Files", "self.extra = Files('input.txt')", "field extra holds a step but is not an Attribute"),
        ("extra: tuple[Step[None], ...]", "self.extra = (Group(),)", "field extra holds a step but is not an Attribute"),
        ("report: Attribute[str]", "self.report = 'value'", "attribute report is reserved by Roadie"),
    ],
)
def test_invalid_step_fields_fail_a_run_and_dry_run(
    tmp_path: Path, dry: bool, kind: str, declaration: str, assignment: str, message: str
) -> None:
    decorator = "@dataclass(eq=False)\n" if kind == "dataclass" else ""
    initializer = "__post_init__(self)" if kind == "dataclass" else "__init__(self)"
    if kind == "dataclass" and declaration:
        declaration += " = field(init=False)"
    write_build(
        tmp_path,
        f"""
from dataclasses import dataclass, field

{decorator}class Invalid(Step[None]):
    {declaration}

    def {initializer}:
        {assignment}

    def build(self, run: Run) -> None:
        return None

invalid = Invalid()
""",
    )
    with pytest.raises(SystemExit, match=f"invalid {message}"):
        cli.run(file=tmp_path, dry_run=dry)
