import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, git_init, isolate_repo

from roadie.build import Directory, Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# (spec) Step kinds
def test_directory_is_reused_until_a_file_is_deleted_added_or_edited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import roadie.build as api

    assert "Directory" in api.__all__
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    git_init(root)
    (root / ".gitignore").write_text("*.ignored\n")
    built: list[str] = []

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            built.append("site")
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "kept.txt").write_bytes(b"kept")
            (path / "nested").mkdir(exist_ok=True)
            (path / "nested" / "inner.txt").write_bytes(b"inner")
            (path / "skip.ignored").write_bytes(b"ignored")
            (path / "empty").mkdir(exist_ok=True)
            return path

    site = Site()
    site.name = site.variable = "site"
    directory = execute([site])
    assert directory == root / "target" / "roadie" / "site"
    assert built == ["site"]
    subprocess.run(
        ["git", "check-ignore", "-q", "--", "target/roadie/site/skip.ignored"],
        cwd=root,
        check=True,
    )
    record = json.loads((root / "target" / "roadie" / "build.record.json").read_text())
    assert record["entries"]["site"]["value"] == {
        "path": "target/roadie/site",
        "files": [
            {"path": "kept.txt", "sha256": _sha(b"kept")},
            {"path": "nested/inner.txt", "sha256": _sha(b"inner")},
            {"path": "skip.ignored", "sha256": _sha(b"ignored")},
        ],
    }

    capsys.readouterr()
    assert execute([site]) == directory
    assert built == ["site"]
    assert "reused site" in capsys.readouterr().out

    (directory / "another-empty").mkdir()
    assert execute([site]) == directory
    assert built == ["site"]

    (directory / "skip.ignored").write_bytes(b"changed")
    execute([site])
    assert built == ["site", "site"]

    (directory / "nested" / "inner.txt").unlink()
    execute([site])
    assert built == ["site", "site", "site"]

    (directory / "extra.txt").write_bytes(b"extra")
    execute([site])
    assert built == ["site", "site", "site", "site"]

    shutil.rmtree(directory)
    execute([site])
    assert built == ["site", "site", "site", "site", "site"]
    assert directory.is_dir()


# (spec) Step kinds
def test_rebuilt_directory_with_identical_files_reuses_its_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    built: list[str] = []

    @dataclass(eq=False)
    class Site(Directory):
        marker: str = "a"

        def build(self, run: Run) -> Path:
            built.append("site")
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "a.txt").write_bytes(b"same")
            return path

    @dataclass(eq=False)
    class Consumer(Step[str]):
        source: Directory

        def build(self, run: Run) -> str:
            built.append("consumer")
            return (run[self.source] / "a.txt").read_text()

    site = Site()
    consumer = Consumer(site)
    site.name = site.variable = "site"
    consumer.name = consumer.variable = "consumer"
    assert execute([site, consumer]) == "same"
    assert built == ["site", "consumer"]
    built.clear()
    site.marker = "b"
    assert execute([site, consumer]) == "same"
    assert built == ["site"]


# (spec) Step kinds
def test_directory_step_rejects_a_path_that_is_not_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, ".txt")
            path.write_bytes(b"nope")
            return path

    site = Site()
    site.name = site.variable = "site"
    path = root / "target" / "roadie" / "site.txt"
    with pytest.raises(StepFailed) as error:
        execute([site])
    assert str(error.value) == f"site failed: site built {path}, which is not a directory"
