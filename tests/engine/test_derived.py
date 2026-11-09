import json
import sys
from pathlib import Path

import pytest
from support import execute, isolate_repo, plan_names, run_cli, write_build

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
