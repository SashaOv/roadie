"""Verify --enforce names the files a step reads but does not declare."""

import _thread
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from support import execute, git_init, tracing_only

from roadie import cli
from roadie.build import Directory, File, Files, Step
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.run import Run


def _located[T: Step[object]](step: T, name: str, root: Path) -> T:
    step.name = name
    step.variable = name
    step.base_dir = root
    step.build_dir = root
    return step


def _project(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    git_init(root)
    (root / "declared.txt").write_text("declared")
    (root / "undeclared.txt").write_text("undeclared")
    (root / ".gitignore").write_text("ignored.txt\noutput.txt\n")
    (root / "ignored.txt").write_text("ignored")
    return root


def _read_script(*names: str) -> str:
    lines = "\n".join(f"Path({name!r}).read_text()" for name in names)
    return f"from pathlib import Path\n{lines}\n"


class Reader(Step[str]):
    def __init__(
        self,
        script: str,
        declared: Files | None = None,
        source: Step[object] | None = None,
    ) -> None:
        self.script = script
        self.declared_input = declared
        self.source = source

    def build(self, run: Run) -> str:
        if self.source is not None:
            _ = run[self.source]
        self._cmd(sys.executable, "-c", self.script)
        return "ok"


class GenFile(File):
    def __init__(self, target: str) -> None:
        self.target = target
        self.copy_to = None

    def build(self, run: Run) -> Path:
        path = self.base_dir / self.target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated")
        return path


class GenDir(Directory):
    def __init__(self, target: str) -> None:
        self.target = target
        self.copy_to = None

    def build(self, run: Run) -> Path:
        directory = self.base_dir / self.target
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "a.txt").write_text("a")
        (directory / "b.txt").write_text("b")
        return directory


# (spec) Enforcing declared inputs
@tracing_only
def test_an_undeclared_read_fails_and_other_steps_still_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    a = _located(Reader(_read_script("declared.txt", "undeclared.txt"), Files("declared.txt")), "a", root)
    b = _located(Reader(_read_script("declared.txt"), Files("declared.txt")), "b", root)
    consumer = _located(Reader(_read_script("declared.txt"), Files("declared.txt"), source=a), "c", root)
    with pytest.raises(RoadieError) as caught:
        execute([a, b, consumer], root=root, enforce=True, jobs=1)
    assert str(caught.value) == "undeclared reads in 1 steps: a"
    out = capsys.readouterr().out
    assert "a reads files it does not declare: undeclared.txt" in out
    assert "build b = " in out
    assert "build c = " in out


# (spec) Enforcing declared inputs
@tracing_only
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_undeclared_read_with_parallel_jobs(tmp_path: Path, jobs: int) -> None:
    root = _project(tmp_path)
    a = _located(Reader(_read_script("undeclared.txt")), "a", root)
    with pytest.raises(RoadieError, match=r"undeclared reads in 1 steps: a"):
        execute([a], root=root, enforce=True, jobs=jobs)


# (spec) Enforcing declared inputs
@tracing_only
def test_ignored_outside_and_written_files_are_not_checked(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outside = tmp_path.parent / "outside-enforce.txt"
    outside.write_text("outside")
    script = (
        "from pathlib import Path\n"
        "Path('ignored.txt').read_text()\n"
        f"Path({str(outside)!r}).read_text()\n"
        "Path('output.txt').write_text('x')\n"
        "Path('output.txt').read_text()\n"
    )
    a = _located(Reader(script), "a", root)
    assert execute([a], root=root, enforce=True) == "ok"


# (spec) Enforcing declared inputs
@tracing_only
def test_a_dependency_file_and_directory_cover_reads(tmp_path: Path) -> None:
    root = _project(tmp_path)
    gen = _located(GenFile("gen.txt"), "gen", root)
    a = _located(Reader(_read_script("gen.txt"), source=gen), "a", root)
    assert execute([gen, a], root=root, enforce=True) == "ok"

    root2 = tmp_path / "second"
    root2.mkdir()
    git_init(root2)
    (root2 / "declared.txt").write_text("d")
    gen_dir = _located(GenDir("gen_dir"), "gendir", root2.resolve())
    b = _located(Reader(_read_script("gen_dir/a.txt"), source=gen_dir), "b", root2.resolve())
    assert execute([gen_dir, b], root=root2.resolve(), enforce=True) == "ok"


# (spec) Enforcing declared inputs
@tracing_only
def test_a_read_after_chdir_names_the_resolved_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    sub = root / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text("inner")
    script = "import os\nfrom pathlib import Path\nos.chdir('sub')\nPath('inner.txt').read_text()\n"
    a = _located(Reader(script), "a", root)
    with pytest.raises(RoadieError):
        execute([a], root=root, enforce=True)
    assert "a reads files it does not declare: sub/inner.txt" in capsys.readouterr().out


# (spec) Enforcing declared inputs
@tracing_only
def test_the_report_marks_an_undeclared_step_failed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = root / "report.json"
    a = _located(Reader(_read_script("undeclared.txt")), "a", root)
    with pytest.raises(RoadieError):
        execute([a], root=root, enforce=True, report=report)
    events = [json.loads(line) for line in report.read_text().splitlines()]
    ends = [event for event in events if event["event"] == "step-end"]
    assert ends[0]["step"] == "a"
    assert ends[0]["outcome"] == "failed"
    assert events[-1]["event"] == "run-end"
    assert events[-1]["outcome"] == "failed"


# (spec) Enforcing declared inputs
@tracing_only
def test_an_enforcement_run_reuses_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    counter = root / "count.txt"

    class Counter(Step[str]):
        def build(self, run: Run) -> str:
            value = int(counter.read_text()) + 1 if counter.exists() else 1
            counter.write_text(str(value))
            self._cmd(sys.executable, "-c", "pass")
            return str(value)

    step = _located(Counter(), "counter", root)
    assert execute([step], root=root, enforce=True) == "1"
    assert execute([step], root=root, enforce=True) == "2"
    # A plain run now reuses the record the enforcement run wrote.
    assert execute([step], root=root) == "2"


class _Logs:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[Path] = []
        original = tempfile.mkstemp

        def recording(**kwargs: object) -> tuple[int, str]:
            handle, name = original(**kwargs)  # type: ignore[arg-type]
            if kwargs.get("prefix") == "roadie-trace-":
                self.paths.append(Path(name))
            return handle, name

        monkeypatch.setattr(tempfile, "mkstemp", recording)

    def assert_deleted(self) -> None:
        assert self.paths, "no trace log was created"
        assert all(not path.exists() for path in self.paths)


# (spec) Enforcing declared inputs
@tracing_only
def test_the_trace_log_is_deleted_after_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    logs = _Logs(monkeypatch)
    a = _located(Reader(_read_script("declared.txt"), Files("declared.txt")), "a", root)
    assert execute([a], root=root, enforce=True) == "ok"
    logs.assert_deleted()

    logs.paths.clear()
    failing = _located(
        Reader("from pathlib import Path\nPath('undeclared.txt').read_text()\nraise SystemExit(1)\n"),
        "failing",
        root,
    )
    with pytest.raises(StepFailed):
        execute([failing], root=root, enforce=True)
    logs.assert_deleted()


# (spec) Enforcing declared inputs
@tracing_only
def test_the_trace_log_is_deleted_after_an_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    logs = _Logs(monkeypatch)
    pidfile = root / "child.pid"
    script = (
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n"
    )

    def interrupt() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                _thread.interrupt_main()
                return
            time.sleep(0.05)

    threading.Thread(target=interrupt, daemon=True).start()
    a = _located(Reader(script), "a", root)
    with pytest.raises(KeyboardInterrupt):
        execute([a], root=root, enforce=True, jobs=1)
    logs.assert_deleted()


# (spec) Enforcing declared inputs
@tracing_only
def test_another_failure_skips_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    a = _located(
        Reader("from pathlib import Path\nimport sys\nPath('undeclared.txt').read_text()\nsys.exit(3)\n"),
        "a",
        root,
    )
    with pytest.raises(StepFailed) as caught:
        execute([a], root=root, enforce=True)
    assert "undeclared reads" not in str(caught.value)
    assert "reads files it does not declare" not in capsys.readouterr().out


# (spec) Command line
# (spec) Enforcing declared inputs
def test_enforce_refuses_dry_and_clean_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "build.py").write_text(
        "from roadie.build import *\n\nclass Note(Step[str]):\n    def build(self, run: Run) -> str:\n        return 'v'\nnote = Note()\n"
    )
    state = tmp_path / "target" / "roadie"
    state.mkdir(parents=True)
    (state / "keep").write_text("sentinel")
    with pytest.raises(SystemExit, match="--enforce does not apply to --dry"):
        cli.run(file=tmp_path, enforce=True, dry_run=True)
    with pytest.raises(SystemExit, match="--enforce does not apply to --clean"):
        cli.run(file=tmp_path, enforce=True, clean=True)
    assert (state / "keep").read_text() == "sentinel"


# (spec) Command line
# (spec) Enforcing declared inputs
def test_enforce_refuses_a_non_linux_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "tracing_error", lambda: "--enforce needs Linux")
    with pytest.raises(SystemExit, match="--enforce needs Linux"):
        cli.run(file=tmp_path, enforce=True)
    monkeypatch.setattr(
        "roadie.engine.run.tracing_error", lambda: "--enforce needs Linux"
    )
    step = _located(Reader("pass"), "a", tmp_path.resolve())
    with pytest.raises(RoadieError, match="--enforce needs Linux"):
        execute([step], root=tmp_path.resolve(), enforce=True)
