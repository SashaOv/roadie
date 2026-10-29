import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import (
    TextFile,
    execute,
    isolate_repo,
    write_build,
)

from roadie.build import File, Step
from roadie.engine.errors import StepFailed
from roadie.engine.graph import load
from roadie.engine.root import use_project_root
from roadie.engine.run import Run


# (spec) Generated files
@pytest.mark.parametrize("previous", [None, "old\n", "new\n"])
def test_file_output_is_published_and_equal_file_is_not_rewritten(previous: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
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
        assert "updated" not in output
        assert destination.stat().st_mtime_ns == 1_000_000_000
    else:
        assert f"updated {destination}" in output


# (spec) Generated files
@pytest.mark.parametrize("previous", [None, "old\n", "new\n"])
@pytest.mark.parametrize("tracked", [False, True])
def test_locked_file_output_protects_tracked_files_and_writes_untracked_files(previous: str | None, tracked: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
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


# (spec) Generated files
def test_file_output_value_is_available_to_dependents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)

    @dataclass(eq=False)
    class Consumer(Step[str]):
        source: File

        def build(self, run: Run) -> str:
            assert run[self.source] == tmp_path / "output.txt"
            return run[self.source].read_text()

    source = TextFile("new\n", output="output.txt")
    assert execute([source, Consumer(source)]) == "new\n"


# (spec) Generated files
def test_file_output_failure_releases_the_built_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
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


# (spec) Generated files
def test_file_output_is_written_under_locked_outside_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    destination = tmp_path / "new/dir/output.txt"
    assert execute([TextFile("new\n", output=destination)], locked=True) == destination


# (spec) Generated files
def test_locked_file_output_reports_git_failure_and_keeps_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    (tmp_path / ".git").write_text("invalid git metadata\n")
    destination = tmp_path / "output.txt"
    destination.write_text("old\n")
    with pytest.raises(StepFailed) as error:
        execute([TextFile("new\n", output=destination)], locked=True)
    assert str(destination) in str(error.value)
    assert "Cannot check whether" in str(error.value)
    assert destination.read_text() == "old\n"


# (spec) Reusing a step
# (spec) Generated files
def test_locked_run_does_not_reuse_a_hand_edited_tracked_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
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


# (spec) Step kinds
def test_file_output_is_keyword_only(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(eq=False, kw_only=True)\n"
        "class Note(File):\n"
        "    def build(self, run):\n"
        "        return self.output\n"
        "api = Note('api.yaml')\n",
    )
    with pytest.raises(TypeError):
        load(tmp_path)
