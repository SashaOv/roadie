import re
import subprocess
from pathlib import Path

import pytest
from support import execute, write_build

from roadie.build import PyProject, Release
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.graph import load


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _project(directory: Path, version: str, name: str = "pyproject.toml") -> Path:
    file = directory / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'[project]\nname = "demo"\nversion = "{version}"\n')
    return file


def _repository(tmp_path: Path, version: str = "0.1.0") -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "release@example.com")
    _git(repo, "config", "user.name", "Release")
    (repo / ".gitignore").write_text(".venv/\n")
    _project(repo, version)
    _git(repo, "add", "pyproject.toml", ".gitignore")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")
    return repo, bare


def _advance(repo: Path) -> None:
    notes = repo / "notes.txt"
    notes.write_text(notes.read_text() + "x\n" if notes.exists() else "x\n")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "advance")


def _tags(bare: Path) -> set[str]:
    listed = _git(bare, "tag", "--list")
    return set(listed.stdout.split())


def _release(repo: Path, *secondary: PyProject) -> str:
    step = Release(PyProject("pyproject.toml"), *secondary)
    step.base_dir = repo
    step.name = "release"
    value = execute([step], root=repo)
    assert isinstance(value, str)
    return value


# (spec) Step kinds
def test_pyproject_requires_a_static_version_and_fingerprints_its_content(tmp_path: Path) -> None:
    source = PyProject("pyproject.toml")
    missing = (tmp_path / "pyproject.toml").resolve()
    message = re.escape(f"{missing} has no static [project] version")
    with pytest.raises(RoadieError, match=message):
        source.read_version(tmp_path)
    missing.write_text('[project]\nname = "demo"\ndynamic = ["version"]\n')
    with pytest.raises(RoadieError, match=message):
        source.read_version(tmp_path)
    missing.write_text('[project]\nname = "demo"\nversion = "0.1.0"\ndynamic = ["version"]\n')
    with pytest.raises(RoadieError, match=message):
        source.read_version(tmp_path)

    missing.write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    assert source.read_version(tmp_path) == "0.1.0"
    original = source.fingerprint(tmp_path)
    missing.write_text('[project]\nname = "demo"\nversion = "0.1.1"\n')
    assert source.fingerprint(tmp_path) != original


# (spec) Step kinds
def test_release_without_a_version_tag_publishes_the_primary_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    assert _release(repo) == "0.1.0"
    assert "released v0.1.0" in capsys.readouterr().out
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "init"
    assert _tags(bare) == {"v0.1.0"}
    assert not (repo / "uv.lock").exists()
    with pytest.raises(StepFailed, match="cannot release: nothing to release"):
        _release(repo)


# (spec) Step kinds
def test_release_bumps_the_build_part_and_skips_suffixed_tags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, bare = _repository(tmp_path, "0.1.9")
    _project(repo / "other", "0.1.9")
    _git(repo, "add", "other/pyproject.toml")
    _git(repo, "commit", "-q", "-m", "other")
    _git(repo, "tag", "v0.1.9")
    _git(repo, "tag", "v0.2.0-rc1")
    _advance(repo)

    assert _release(repo, PyProject("other/pyproject.toml")) == "0.1.10"
    assert "released v0.1.10" in capsys.readouterr().out
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "v0.1.10 release"
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "pyproject.toml" in committed
    assert "other/pyproject.toml" in committed
    assert "uv.lock" not in committed
    assert 'version = "0.1.10"' in (repo / "pyproject.toml").read_text()
    assert 'version = "0.1.10"' in (repo / "other" / "pyproject.toml").read_text()
    assert _tags(bare) == {"v0.1.10"}


# (spec) Step kinds
def test_release_keeps_a_higher_version_and_rejects_a_lower_one(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.9")
    _git(repo, "tag", "v0.1.9")
    _project(repo, "0.1.10")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", "bump")
    assert _release(repo) == "0.1.10"
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "bump"
    assert "v0.1.10" in _tags(bare)

    lower, lower_remote = _repository(tmp_path / "lower", "0.2.0")
    _git(lower, "tag", "v0.2.0")
    _advance(lower)
    _project(lower, "0.1.0")
    _git(lower, "add", "pyproject.toml")
    _git(lower, "commit", "-q", "-m", "downgrade")
    with pytest.raises(StepFailed, match="cannot release: version 0.1.0 is lower than v0.2.0"):
        _release(lower)
    assert 'version = "0.1.0"' in (lower / "pyproject.toml").read_text()
    assert _tags(lower_remote) == set()


# (spec) Step kinds
def test_release_fails_before_changing_anything(
    tmp_path: Path,
) -> None:
    dirty, dirty_remote = _repository(tmp_path / "dirty", "0.1.0")
    _git(dirty, "tag", "v0.1.0")
    _advance(dirty)
    (dirty / "pyproject.toml").write_text((dirty / "pyproject.toml").read_text() + "\n")
    with pytest.raises(StepFailed, match="cannot release: uncommitted changes"):
        _release(dirty)
    assert _tags(dirty_remote) == set()

    local = tmp_path / "local"
    subprocess.run(["git", "init", "-q", "-b", "main", str(local)], check=True)
    _git(local, "config", "user.email", "release@example.com")
    _git(local, "config", "user.name", "Release")
    _project(local, "0.1.0")
    _git(local, "add", "pyproject.toml")
    _git(local, "commit", "-q", "-m", "init")
    with pytest.raises(StepFailed, match="cannot release: no upstream"):
        _release(local)
    assert 'version = "0.1.0"' in (local / "pyproject.toml").read_text()

    behind, bare = _repository(tmp_path / "behind", "0.1.0")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    _git(clone, "config", "user.email", "release@example.com")
    _git(clone, "config", "user.name", "Release")
    (clone / "other.txt").write_text("z\n")
    _git(clone, "add", "other.txt")
    _git(clone, "commit", "-q", "-m", "remote")
    _git(clone, "push", "-q", "origin", "HEAD")
    with pytest.raises(StepFailed, match="cannot release: behind its upstream"):
        _release(behind)
    assert 'version = "0.1.0"' in (behind / "pyproject.toml").read_text()
    assert _tags(bare) == set()


# (spec) Step kinds
def test_a_tracked_lockfile_joins_the_release_commit(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    subprocess.run(["uv", "lock"], cwd=repo, check=True, capture_output=True)
    _git(repo, "add", "uv.lock")
    _git(repo, "commit", "-q", "-m", "lock")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    before = (repo / "uv.lock").read_bytes()
    assert _release(repo) == "0.1.1"
    assert (repo / "uv.lock").read_bytes() != before
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["pyproject.toml", "uv.lock"] or set(committed) == {
        "pyproject.toml",
        "uv.lock",
    }
    assert "v0.1.1" in _tags(bare)


# (spec) Step kinds
def test_release_requires_run_before_it_and_a_failure_publishes_nothing(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    write_build(
        repo,
        'gate = Check("false")\nrelease = Release(PyProject("pyproject.toml"), requires=(gate,))\n',
    )
    plan = load(repo).plan("release")
    assert [step.name for step in plan] == ["gate", "release"]
    with pytest.raises(StepFailed, match="false"):
        execute(plan, root=repo)
    assert _tags(bare) == set()

    passing, passing_remote = _repository(tmp_path / "passing", "0.1.0")
    write_build(
        passing,
        'gate = Check("true")\nrelease = Release(PyProject("pyproject.toml"), requires=(gate,))\n',
    )
    _git(passing, "add", "build.py")
    _git(passing, "commit", "-q", "-m", "build")
    _git(passing, "push", "-q", "origin", "HEAD")
    execute(load(passing).plan("release"), root=passing)
    assert _tags(passing_remote) == {"v0.1.0"}
