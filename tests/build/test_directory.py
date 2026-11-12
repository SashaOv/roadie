import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, git_init, isolate_repo, needs_symlinks, write_build

from roadie.build import Directory, Step
from roadie.engine.errors import StepFailed
from roadie.engine.root import use_project_root
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


# (spec) Directory step
@needs_symlinks
def test_directory_records_a_link_by_its_target_and_does_not_follow_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    built: list[str] = []

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            built.append("site")
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "kept.txt").write_bytes(b"kept")
            outside = path.parent / "outside"
            outside.mkdir(exist_ok=True)
            secret = outside / "secret.txt"
            if not secret.exists():
                secret.write_bytes(b"secret")
            nested = outside / "nested"
            nested.mkdir(exist_ok=True)
            inner = nested / "inner.txt"
            if not inner.exists():
                inner.write_bytes(b"inner")
            for name in ("link", "dirlink"):
                link = path / name
                if link.is_symlink() or link.exists():
                    link.unlink()
            (path / "link").symlink_to(secret)
            (path / "dirlink").symlink_to(nested, target_is_directory=True)
            return path

    site = Site()
    site.name = site.variable = "site"
    directory = execute([site])
    assert isinstance(directory, Path)
    record = json.loads((root / "target" / "roadie" / "build.record.json").read_text())
    files = record["entries"]["site"]["value"]["files"]
    assert [item["path"] for item in files] == ["dirlink", "kept.txt", "link"]
    by_path = {item["path"]: item for item in files}
    assert by_path["kept.txt"] == {"path": "kept.txt", "sha256": _sha(b"kept")}
    assert by_path["link"] == {"path": "link", "target": os.readlink(directory / "link")}
    assert by_path["dirlink"] == {
        "path": "dirlink",
        "target": os.readlink(directory / "dirlink"),
    }

    outside = directory.parent / "outside"
    (outside / "secret.txt").write_bytes(b"changed")
    (outside / "nested" / "inner.txt").write_bytes(b"changed")
    execute([site])
    assert built == ["site"]

    (directory / "link").unlink()
    (directory / "link").symlink_to(outside / "other.txt")
    execute([site])
    assert built == ["site", "site"]


# (spec) Directory step
def test_directory_unreadable_file_fails_with_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "a.txt").write_bytes(b"a")
            return path

    site = Site()
    site.name = site.variable = "site"
    real = Path.read_bytes

    def fail(self: Path) -> bytes:
        if self.name == "a.txt":
            raise OSError("denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", fail)
    path = root / "target" / "roadie" / "site" / "a.txt"
    with pytest.raises(StepFailed) as error:
        execute([site])
    assert str(error.value) == f"site failed: cannot read {path}: denied"


def _site(text: bytes = b"new\n") -> Directory:
    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "out.txt").write_bytes(text)
            return path

    site = Site(copy_to="dist")
    site.name = site.variable = "site"
    return site


# (spec) Directory step
@pytest.mark.parametrize("previous", [None, b"old\n", b"new\n"])
def test_directory_output_is_published_and_equal_directory_is_not_rewritten(
    previous: bytes | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    destination = root / "dist"
    output = destination / "out.txt"
    if previous is not None:
        destination.mkdir()
        output.write_bytes(previous)
        os.utime(output, ns=(1_000_000_000, 1_000_000_000))
    assert execute([_site()]) == destination
    assert output.read_bytes() == b"new\n"
    printed = capsys.readouterr().out
    if previous == b"new\n":
        assert "updated" not in printed
        assert output.stat().st_mtime_ns == 1_000_000_000
    else:
        assert f"updated {output}" in printed


# (spec) Directory step
@pytest.mark.parametrize("previous", [None, b"old\n", b"new\n"])
@pytest.mark.parametrize("tracked", [False, True])
def test_locked_directory_output_protects_tracked_files_and_writes_untracked_files(
    previous: bytes | None,
    tracked: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    destination = root / "dist"
    output = destination / "out.txt"
    destination.mkdir()
    output.write_bytes(previous or b"old\n")
    if tracked:
        subprocess.run(["git", "add", "dist/out.txt"], cwd=root, check=True)
    if previous is None:
        output.unlink()
    if tracked and previous != b"new\n":
        with pytest.raises(StepFailed) as error:
            execute([_site()], locked=True)
        assert str(error.value) == f"site failed: generated directory is stale: {destination}"
        assert str(output) in capsys.readouterr().out.splitlines()
        expected = None if previous is None else previous
        assert (output.read_bytes() if output.exists() else None) == expected
    else:
        assert execute([_site()], locked=True) == destination
        assert output.read_bytes() == b"new\n"


# (spec) Directory step
def test_locked_directory_names_every_changed_tracked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    destination = root / "dist"
    destination.mkdir()
    first = destination / "a.txt"
    second = destination / "b.txt"
    first.write_bytes(b"old")
    second.write_bytes(b"old")
    subprocess.run(["git", "add", "dist/a.txt", "dist/b.txt"], cwd=root, check=True)

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "a.txt").write_bytes(b"new")
            (path / "b.txt").write_bytes(b"new")
            return path

    site = Site(copy_to="dist")
    site.name = site.variable = "site"
    with pytest.raises(StepFailed) as error:
        execute([site], locked=True)
    assert str(error.value) == f"site failed: generated directory is stale: {destination}"
    assert capsys.readouterr().out.splitlines()[-2:] == [str(first), str(second)]
    assert first.read_bytes() == b"old"
    assert second.read_bytes() == b"old"


# (spec) Directory step
def test_locked_tracked_directory_file_ignores_autocrlf_checkout_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "dir@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Directory"], cwd=root, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=root, check=True)
    output = root / "dist" / "out.txt"
    output.parent.mkdir()
    output.write_bytes(b"new\n")
    subprocess.run(["git", "add", "dist/out.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=root, check=True)
    output.unlink()
    subprocess.run(["git", "checkout", "-q", "--", "dist/out.txt"], cwd=root, check=True)
    assert output.read_bytes() == b"new\r\n"
    assert execute([_site()], locked=True) == root / "dist"
    assert output.read_bytes() == b"new\r\n"
    assert "updated" not in capsys.readouterr().out


# (spec) Directory step
def test_directory_output_is_written_under_locked_outside_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    destination = root / "dist"
    assert execute([_site()], locked=True) == destination
    assert (destination / "out.txt").read_bytes() == b"new\n"


# (spec) Directory step
def test_reused_directory_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    published: list[Path] = []

    class Site(_site().__class__):
        def publish(self, run: Run, value: Path) -> Path:
            published.append(value)
            return super().publish(run, value)

    site = Site(copy_to="dist")
    site.name = site.variable = "site"
    execute([site])
    execute([site])
    assert published == [root / "target" / "roadie" / "site"]


# (spec) Directory step
def test_directory_output_failure_releases_the_built_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    (root / "dist").write_bytes(b"blocking")
    released: list[Path] = []

    class Releasing(_site().__class__):
        def teardown(self, value: Path) -> None:
            released.append(value)

    step = Releasing(copy_to="dist")
    step.name = step.variable = "site"
    with pytest.raises(StepFailed, match="dist"):
        execute([step])
    with use_project_root(root):
        assert released == [Run().artifact(step, "")]


# (spec) Directory step
def test_publishing_removes_a_tracked_file_and_keeps_an_untracked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    destination = root / "dist"
    destination.mkdir()
    (destination / "old.txt").write_bytes(b"old")
    (destination / "keep.txt").write_bytes(b"keep")
    (destination / "extra.txt").write_bytes(b"extra")
    subprocess.run(["git", "add", "dist/old.txt", "dist/keep.txt"], cwd=root, check=True)

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "keep.txt").write_bytes(b"keep")
            return path

    site = Site(copy_to="dist")
    site.name = site.variable = "site"
    assert execute([site]) == destination
    assert not (destination / "old.txt").exists()
    assert (destination / "keep.txt").read_bytes() == b"keep"
    assert (destination / "extra.txt").read_bytes() == b"extra"


# (spec) Directory step
def test_locked_publish_keeps_a_tracked_file_the_build_no_longer_produces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    isolate_repo(monkeypatch, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    destination = root / "dist"
    destination.mkdir()
    old = destination / "old.txt"
    old.write_bytes(b"old")
    subprocess.run(["git", "add", "dist/old.txt"], cwd=root, check=True)

    @dataclass(eq=False)
    class Site(Directory):
        def build(self, run: Run) -> Path:
            path = run.artifact(self, "")
            path.mkdir(exist_ok=True)
            (path / "keep.txt").write_bytes(b"keep")
            return path

    site = Site(copy_to="dist")
    site.name = site.variable = "site"
    with pytest.raises(StepFailed) as error:
        execute([site], locked=True)
    assert str(error.value) == f"site failed: generated directory is stale: {destination}"
    assert str(old) in capsys.readouterr().out.splitlines()
    assert old.read_bytes() == b"old"
    assert not (destination / "keep.txt").exists()


def _roadie() -> Path:
    parent = Path(sys.executable).parent
    for name in ("roadie", "roadie.exe"):
        candidate = parent / name
        if candidate.is_file():
            return candidate
    raise AssertionError(f"roadie is not installed next to {sys.executable}")


def _project(text: str, extra: bool) -> str:
    extra_line = '        (path / "extra.txt").write_text("extra", newline="\\n")\n' if extra else ""
    return f"""\
from dataclasses import dataclass
from pathlib import Path
from roadie.engine.run import Run

@dataclass(eq=False, kw_only=True)
class Site(Directory):
    text: str = {text!r}
    marker: Files = Files("marker.txt")

    def build(self, run: Run) -> Path:
        (self.base_dir / "marker.txt").read_text()
        path = run.artifact(self, "")
        path.mkdir(exist_ok=True)
        (path / "out.txt").write_text(self.text, newline="\\n")
{extra_line}        return path

@dataclass(eq=False)
class Consumer(Step[str]):
    source: Directory

    def build(self, run: Run) -> str:
        tally = self.base_dir / "consumer-runs.txt"
        count = int(tally.read_text()) + 1 if tally.exists() else 1
        tally.write_text(str(count), newline="\\n")
        return (run[self.source] / "out.txt").read_text()

@dataclass(eq=False)
class Watch(Step[None]):
    files: Files

    def build(self, run: Run) -> None:
        return None

site = Site(copy_to="dist")
watch = Watch(Files("dist"))
consumer = default_step(Consumer(site))
"""


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_roadie()), "-j", "1", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


# (spec) Directory step
def test_directory_scenario_runs_through_the_roadie_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    git_init(root)
    (root / ".gitignore").write_text("dist/\n")
    (root / "marker.txt").write_text("a")
    write_build(root, _project("one", extra=False))
    output = root / "dist" / "out.txt"

    first = _cli(root)
    assert first.returncode == 0, first.stderr
    assert "built site" in first.stdout
    assert "built consumer" in first.stdout
    assert output.read_text() == "one"
    assert (root / "consumer-runs.txt").read_text() == "1"

    second = _cli(root)
    assert second.returncode == 0, second.stderr
    assert "reused site" in second.stdout
    assert "reused consumer" in second.stdout
    assert "built site" not in second.stdout
    assert "built consumer" not in second.stdout

    output.unlink()
    deleted = _cli(root)
    assert deleted.returncode == 0, deleted.stderr
    assert "built site" in deleted.stdout
    assert output.read_text() == "one"

    output.write_text("hand")
    edited = _cli(root)
    assert edited.returncode == 0, edited.stderr
    assert "built site" in edited.stdout
    assert output.read_text() == "one"

    (root / "marker.txt").write_text("b")
    rebuilt = _cli(root)
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert "built site" in rebuilt.stdout
    assert "reused consumer" in rebuilt.stdout
    assert (root / "consumer-runs.txt").read_text() == "1"

    ignored = _cli(root, "watch")
    assert ignored.returncode == 1
    assert "Files(dist): dist is ignored by git; take the step that builds it" in (
        ignored.stderr + ignored.stdout
    )

    subprocess.run(["git", "add", "-f", "dist/out.txt"], cwd=root, check=True)
    write_build(root, _project("two", extra=False))
    stale = _cli(root, "--locked")
    assert stale.returncode == 1
    assert f"generated directory is stale: {root / 'dist'}" in (stale.stderr + stale.stdout)
    assert str(output) in stale.stdout
    assert output.read_text() == "one"

    write_build(root, _project("one", extra=True))
    written = _cli(root, "--locked")
    assert written.returncode == 0, written.stderr
    assert (root / "dist" / "extra.txt").read_text() == "extra"
    assert output.read_text() == "one"
