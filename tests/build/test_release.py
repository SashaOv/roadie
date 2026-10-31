import re
import subprocess
from pathlib import Path

import pytest
from support import execute, write_build

from roadie.build import Release
from roadie.build.release import CargoManifest, PackageJson, PyProject
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.graph import load
from roadie.engine.parameters import ParameterValues


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
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
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


def _release(repo: Path, *secondary: str) -> str:
    step = Release("pyproject.toml", *secondary)
    step.base_dir = repo
    step.name = "release"
    value = execute([step], root=repo)
    assert isinstance(value, str)
    return value


# (spec) Step kinds
def test_release_names_version_sources_by_their_files() -> None:
    with pytest.raises(RoadieError, match="Release takes paths: got int"):
        Release(1)
    with pytest.raises(
        RoadieError,
        match=re.escape(
            "Release takes pyproject.toml, Cargo.toml, or package.json files: got notes.txt"
        ),
    ):
        Release("notes.txt")
    step = Release("pyproject.toml", "other/pyproject.toml", message="ship it")
    assert step.call_arguments()[0] == ["pyproject.toml", "other/pyproject.toml"]
    import roadie.build as api

    assert "PyProject" not in api.__all__


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
def test_cargo_reads_the_workspace_version_or_the_package_version(tmp_path: Path) -> None:
    source = CargoManifest("Cargo.toml")
    manifest = tmp_path / "Cargo.toml"
    resolved = manifest.resolve()
    manifest.write_text('[package]\nname = "demo"\nedition = "2021"\n')
    with pytest.raises(RoadieError, match=re.escape(f"{resolved} has no static Cargo version")):
        source.read_version(tmp_path)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    package = (
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n# version = "leave"\n'
    )
    manifest.write_text(package)
    assert source.read_version(tmp_path) == "0.1.0"
    original = source.fingerprint(tmp_path)
    assert source.write_version(tmp_path, "0.1.1") == (resolved,)
    assert manifest.read_text() == package.replace('version = "0.1.0"', 'version = "0.1.1"', 1)
    assert source.fingerprint(tmp_path) != original

    manifest.write_text(
        "[workspace]\nmembers = []\n\n"
        "[workspace.package]\n"
        'version = "0.2.0"\n\n'
        "[package]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    assert source.read_version(tmp_path) == "0.2.0"
    source.write_version(tmp_path, "0.2.1")
    text = manifest.read_text()
    assert 'version = "0.2.1"' in text
    assert 'version = "0.1.0"' in text
    assert 'edition = "2021"' in text


# (spec) Step kinds
def test_a_tracked_cargo_lock_joins_the_release_commit(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    subprocess.run(["cargo", "generate-lockfile"], cwd=repo, check=True, capture_output=True)
    _git(repo, "add", "Cargo.toml", "Cargo.lock", "src")
    _git(repo, "commit", "-q", "-m", "cargo")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    before = (repo / "Cargo.lock").read_text()
    step = Release("Cargo.toml")
    step.base_dir = repo
    step.name = "release"
    assert execute([step], root=repo) == "0.1.1"
    assert 'version = "0.1.1"' in (repo / "Cargo.toml").read_text()
    assert 'edition = "2021"' in (repo / "Cargo.toml").read_text()
    assert (repo / "Cargo.lock").read_text() != before
    assert 'version = "0.1.1"' in (repo / "Cargo.lock").read_text()
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert set(committed) == {"Cargo.toml", "Cargo.lock"}
    assert "v0.1.1" in _tags(bare)


# (spec) Step kinds
def test_a_failing_cargo_update_stops_the_release(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path / "broken", "0.1.0")
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n\n'
        '[dependencies]\nmissing = { path = "missing" }\n'
    )
    (repo / "Cargo.lock").write_text("# not a lockfile\n")
    _git(repo, "add", "Cargo.toml", "Cargo.lock", "src")
    _git(repo, "commit", "-q", "-m", "cargo")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    step = Release("Cargo.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed, match="cannot release:") as error:
        execute([step], root=repo)
    assert "version line is missing" not in str(error.value)
    assert _tags(bare) == set()


# (spec) Step kinds
def test_package_json_replaces_its_version_and_refuses_a_package_lock(tmp_path: Path) -> None:
    source = PackageJson("package.json")
    package = tmp_path / "package.json"
    resolved = package.resolve()
    package.write_text('{"name": "demo"}\n')
    with pytest.raises(RoadieError, match=re.escape(f"{resolved} has no version")):
        source.read_version(tmp_path)
    text = '{\n  "name": "demo",\n  "version": "0.1.0",\n  "deps": {"version": "9.9.9"}\n}\n'
    package.write_text(text)
    assert source.read_version(tmp_path) == "0.1.0"
    assert source.write_version(tmp_path, "0.1.1") == (resolved,)
    assert package.read_text() == text.replace('"version": "0.1.0"', '"version": "0.1.1"', 1)

    repo, bare = _repository(tmp_path / "locked", "0.1.0")
    locked = repo / "app" / "package.json"
    locked.parent.mkdir()
    original = '{\n  "name": "app",\n  "version": "0.1.0"\n}\n'
    locked.write_text(original)
    (repo / "app" / "package-lock.json").write_text("{}\n")
    _git(repo, "add", "app")
    _git(repo, "commit", "-q", "-m", "js")
    _git(repo, "push", "-q", "origin", "HEAD")
    step = PackageJson("app/package.json")
    with pytest.raises(
        RoadieError,
        match=re.escape(
            f"cannot release: {locked.resolve()} has a package-lock.json, "
            "which Roadie does not update"
        ),
    ):
        step.write_version(repo, "0.1.1")
    assert locked.read_text() == original
    assert _tags(bare) == set()

    plain, plain_remote = _repository(tmp_path / "pnpm", "0.1.0")
    (plain / "package.json").write_text('{\n  "name": "app",\n  "version": "0.1.0"\n}\n')
    lock = plain / "pnpm-lock.yaml"
    lock.write_text("lockfileVersion: '9.0'\n")
    _git(plain, "add", "package.json", "pnpm-lock.yaml")
    _git(plain, "commit", "-q", "-m", "pnpm")
    _git(plain, "push", "-q", "origin", "HEAD")
    _git(plain, "tag", "v0.1.0")
    _advance(plain)
    kept = lock.read_bytes()
    release = Release("package.json")
    release.base_dir = plain
    release.name = "release"
    assert execute([release], root=plain) == "0.1.1"
    assert '"version": "0.1.1"' in (plain / "package.json").read_text()
    assert lock.read_bytes() == kept
    committed = _git(plain, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["package.json"]
    assert "v0.1.1" in _tags(plain_remote)


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

    assert _release(repo, "other/pyproject.toml") == "0.1.10"
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
        'gate = Check("false")\nrelease = Release("pyproject.toml", requires=(gate,))\n',
    )
    plan = load(repo).plan("release")
    assert [step.name for step in plan] == ["gate", "release"]
    with pytest.raises(StepFailed, match="false"):
        execute(plan, root=repo)
    assert _tags(bare) == set()

    passing, passing_remote = _repository(tmp_path / "passing", "0.1.0")
    write_build(
        passing,
        'gate = Check("true")\nrelease = Release("pyproject.toml", requires=(gate,))\n',
    )
    _git(passing, "add", "build.py")
    _git(passing, "commit", "-q", "-m", "build")
    _git(passing, "push", "-q", "origin", "HEAD")
    execute(load(passing).plan("release"), root=passing)
    assert _tags(passing_remote) == {"v0.1.0"}


def _object_kind(repo: Path, name: str) -> str:
    return _git(repo, "cat-file", "-t", name).stdout.strip()


# (spec) Step kinds
def test_release_message_is_a_string_or_a_param() -> None:
    with pytest.raises(RoadieError, match="Release message takes a string or a Param: got int"):
        Release("pyproject.toml", message=1)


# (spec) Step kinds
def test_a_release_message_is_the_commit_body_and_an_annotated_tag(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    step = Release("pyproject.toml", message="ship it")
    step.base_dir = repo
    step.name = "release"
    assert execute([step], root=repo) == "0.1.1"
    assert _git(repo, "log", "-1", "--format=%B").stdout.strip() == "v0.1.1 release\n\nship it"
    assert _object_kind(repo, "v0.1.1") == "tag"
    assert _git(repo, "tag", "-l", "--format=%(contents)", "v0.1.1").stdout.strip() == "ship it"
    assert "v0.1.1" in _tags(bare)

    quiet, quiet_remote = _repository(tmp_path / "quiet", "0.1.0")
    _git(quiet, "tag", "v0.1.0")
    _advance(quiet)
    plain = Release("pyproject.toml", message="")
    plain.base_dir = quiet
    plain.name = "release"
    assert execute([plain], root=quiet) == "0.1.1"
    assert _git(quiet, "log", "-1", "--format=%B").stdout.strip() == "v0.1.1 release"
    assert _object_kind(quiet, "v0.1.1") == "commit"
    assert "v0.1.1" in _tags(quiet_remote)


# (spec) Step kinds
def test_a_param_message_is_the_release_message(tmp_path: Path) -> None:
    repo, _bare = _repository(tmp_path, "0.1.0")
    write_build(
        repo,
        'release = Release("pyproject.toml", message=Param("message", default=None))\n',
    )
    _git(repo, "add", "build.py")
    _git(repo, "commit", "-q", "-m", "build")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    plan = load(repo).plan("release")
    execute(plan, root=repo, parameters=ParameterValues({"message": "from the run"}, {}))
    assert _git(repo, "log", "-1", "--format=%B").stdout.strip() == "v0.1.1 release\n\nfrom the run"
    assert _object_kind(repo, "v0.1.1") == "tag"
