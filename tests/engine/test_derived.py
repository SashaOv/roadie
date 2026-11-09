import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from support import (
    execute,
    git_init,
    isolate_repo,
    plan_names,
    posix_only,
    run_cli,
    write_build,
)

from roadie.engine.graph import load

_PY = repr(sys.executable)

_KINDS = """
from dataclasses import dataclass

@dataclass(eq=False)
class Emit(Step[str]):
    def build(self, run):
        path = run.artifact(self, ".txt")
        path.write_text("v")
        return "v"

@dataclass(eq=False)
class Hold(Step[str]):
    source: Step
    def build(self, run):
        path = run.artifact(self, ".txt")
        path.write_text(run[self.source])
        return run[self.source]

@dataclass(eq=False)
class Bundle(Step[str]):
    parts: tuple
    def build(self, run):
        return "v"

class Wrap(Step[str]):
    def __init__(self, source):
        self.source = source
    def call_arguments(self):
        return [], {}
    def build(self, run):
        return run[self.source]
"""


def _project(root: Path) -> None:
    write_build(root / "lib", _KINDS + "copy = Hold(Emit())\n")
    write_build(
        root,
        _KINDS
        + """
copy = Hold(Emit())
nested = Hold(Hold(Emit()))
checks = Group(Emit(), Emit())
bundle = Bundle((Emit(), Emit()))
step = Emit()
one = Group(step)
two = Group(step)
del step
wrap = Wrap(Emit())
lib = include("lib")
kept = Group(lib.copy)
""",
    )


# (spec) Results
def test_derived_names_follow_the_path_from_the_first_variable(tmp_path: Path) -> None:
    _project(tmp_path)
    graph = load(tmp_path)
    copy = graph.results["copy"]
    nested = graph.results["nested"]
    checks = graph.results["checks"]
    bundle = graph.results["bundle"]
    one = graph.results["one"]
    two = graph.results["two"]
    wrap = graph.results["wrap"]
    lib_copy = graph.results["lib"].copy

    assert copy.source.name == "copy.source"
    assert nested.source.name == "nested.source"
    assert nested.source.source.name == "nested.source.source"
    assert checks.steps[0].name == "checks[0]"
    assert checks.steps[1].name == "checks[1]"
    assert bundle.parts[0].name == "bundle.parts[0]"
    assert bundle.parts[1].name == "bundle.parts[1]"
    assert one.steps[0].name == "one[0]"
    assert two.steps[0] is one.steps[0]
    assert wrap.source.name == "wrap.source"
    assert lib_copy.name == "lib:copy"
    assert lib_copy.source.name == "lib:copy.source"
    assert lib_copy.source.build_dir == (tmp_path / "lib").resolve()
    assert copy.source.build_dir == tmp_path.resolve()


# (spec) Results
# (spec) Dry run output
def test_dry_run_names_a_derived_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path)
    run_cli(["--dry", "-f", str(tmp_path), "copy"])
    lines = capsys.readouterr().out.splitlines()
    assert "copy.source = Emit()  # run" in lines
    assert "copy = Hold(source=copy.source)  # run" in lines


# (spec) Results
# (spec) Reusing a step
# (spec) Records
# (spec) Artifacts
def test_each_derived_step_is_reused_with_its_record_and_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    _project(root)
    execute(load(root).plan("copy"), root=root)
    output = capsys.readouterr().out
    assert "build copy.source = Emit()" in output
    assert "reused copy.source" not in output
    record = json.loads((root / "target" / "roadie" / "build.record.json").read_text())
    assert "copy.source" in record["entries"]
    assert (root / "target" / "roadie" / "copy.source.txt").read_text() == "v"

    capsys.readouterr()
    execute(load(root).plan("copy"), root=root)
    second = capsys.readouterr().out
    assert "reused copy.source" in second
    assert "reused copy" in second
    assert "build " not in second

    execute(load(root).plan("nested"), root=root)
    capsys.readouterr()
    execute(load(root).plan("nested"), root=root)
    assert "reused nested.source.source" in capsys.readouterr().out

    execute(load(root).plan("checks"), root=root)
    capsys.readouterr()
    execute(load(root).plan("checks"), root=root)
    reused = capsys.readouterr().out
    assert "reused checks[0]" in reused
    assert "reused checks[1]" in reused

    execute(load(root).plan("bundle"), root=root)
    capsys.readouterr()
    execute(load(root).plan("bundle"), root=root)
    assert "reused bundle.parts[0]" in capsys.readouterr().out

    execute(load(root).plan("one"), root=root)
    capsys.readouterr()
    execute(load(root).plan("two"), root=root)
    assert "reused one[0]" in capsys.readouterr().out

    execute(load(root).plan("wrap"), root=root)
    capsys.readouterr()
    execute(load(root).plan("wrap"), root=root)
    assert "reused wrap.source" in capsys.readouterr().out

    execute(load(root).plan("kept"), root=root)
    lib_record = json.loads((root / "target" / "roadie" / "lib" / "build.record.json").read_text())
    assert "copy.source" in lib_record["entries"]
    assert (root / "target" / "roadie" / "lib" / "copy.source.txt").is_file()
    capsys.readouterr()
    execute(load(root).plan("kept"), root=root)
    assert "reused lib:copy.source" in capsys.readouterr().out


# (spec) Results
# (spec) Reusing a step
def test_rebuilding_one_loop_member_reuses_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name)
    write_build(
        root,
        "checks = Group(\n"
        f"    Check({_PY}, '-c', 'pass', inputs=Files('a.txt')),\n"
        f"    Check({_PY}, '-c', 'pass', inputs=Files('b.txt')),\n"
        f"    Check({_PY}, '-c', 'pass', inputs=Files('c.txt')),\n"
        ")\n",
    )
    assert plan_names(root) == ["checks[0]", "checks[1]", "checks[2]", "checks"]
    execute(load(root).plan(), root=root)
    capsys.readouterr()
    execute(load(root).plan(), root=root)
    assert "reused checks[1]" in capsys.readouterr().out

    (root / "b.txt").write_text("changed")
    capsys.readouterr()
    execute(load(root).plan(), root=root)
    output = capsys.readouterr().out
    assert "reused checks[0]" in output
    assert "build checks[1] = Check(" in output
    assert "reused checks[2]" in output
    assert "build checks[0]" not in output
    assert "build checks[2]" not in output


def _roadie() -> Path:
    parent = Path(sys.executable).parent
    for name in ("roadie", "roadie.exe"):
        candidate = parent / name
        if candidate.is_file():
            return candidate
    raise AssertionError(f"roadie is not installed next to {sys.executable}")


def _scenario_source(ready: Path) -> str:
    program = repr(sys.executable)
    pause = repr(
        "from pathlib import Path; import time; "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(60)"
    )
    return f"""\
from dataclasses import dataclass

@dataclass(eq=False)
class Emit(Step[str]):
    def build(self, run):
        completed = self._cmd({program}, "-c", "print('secret' + '-out')", capture_output=True)
        return (completed.stdout or "").strip()

@dataclass(eq=False)
class Copy(File):
    source: Step
    def build(self, run):
        path = run.artifact(self, ".txt")
        path.write_text(run[self.source] + "\\n")
        return path

@dataclass(eq=False)
class Slow(Step[None]):
    def build(self, run):
        self._cmd({program}, "-c", {pause})

copy = Copy(Emit(), copy_to="copied.txt")
checks = Group(*(
    Check({program}, "-c", "pass", inputs=Files(name))
    for name in ("a.txt", "b.txt", "c.txt")
))
slow = Slow()
"""


def _project_scenario(root: Path) -> Path:
    git_init(root)
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name + "\n")
    write_build(root, _scenario_source(root / "slow.ready"))
    return root / "slow.ready"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_roadie()), "--jobs", "1", "-f", str(root), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


# (spec) Results
# (spec) Reusing a step
# (spec) Dry run output
# (spec) Generated files
def test_the_naming_scenario_runs_through_the_roadie_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _project_scenario(root)
    first = _cli(root, "copy", "checks")
    assert first.returncode == 0, first.stderr
    assert "build copy.source = Emit()" in first.stdout
    assert "build checks[0] = Check(" in first.stdout
    assert "secret-out" not in first.stdout
    assert "secret-out" not in first.stderr
    assert (root / "copied.txt").read_text() == "secret-out\n"

    second = _cli(root, "copy", "checks")
    assert second.returncode == 0, second.stderr
    assert not any(line.startswith("build ") for line in second.stdout.splitlines())
    for name in ("copy.source", "copy", "checks[0]", "checks[1]", "checks[2]", "checks"):
        assert f"reused {name}" in second.stdout

    (root / "b.txt").write_text("changed\n")
    dry = _cli(root, "--dry", "copy", "checks")
    assert dry.returncode == 0, dry.stderr
    assert any(
        line.startswith("checks[1] = Check(") and line.endswith("  # run")
        for line in dry.stdout.splitlines()
    )

    edited = _cli(root, "copy", "checks")
    assert edited.returncode == 0, edited.stderr
    assert "build checks[1] = Check(" in edited.stdout
    assert "build checks[0]" not in edited.stdout
    assert "build checks[2]" not in edited.stdout
    assert "build copy" not in edited.stdout
    assert "reused checks[0]" in edited.stdout
    assert "reused checks[2]" in edited.stdout


# (spec) Teardown
@posix_only
def test_sigterm_during_a_slow_step_names_it_and_exits_130(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ready = _project_scenario(root)
    process = subprocess.Popen(
        [str(_roadie()), "--jobs", "1", "-f", str(root), "slow"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.is_file():
            if process.poll() is not None:
                out, err = process.communicate()
                pytest.fail(f"slow step exited early ({process.returncode}): {out}{err}")
            time.sleep(0.05)
        assert ready.is_file()
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 130, err
    assert "interrupted: stopping slow" in out.splitlines()
