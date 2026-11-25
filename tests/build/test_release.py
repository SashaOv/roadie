import errno
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from support import execute, failing_check, passing_check, posix_only, write_build

from roadie.build import Release
from roadie.build.manifests import CargoManifest, PackageJson, PyProject
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
    (repo / ".gitignore").write_text(".venv/\ntarget/\n")
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


def _local_tags(repo: Path) -> set[str]:
    return set(_git(repo, "tag", "--list").stdout.split())


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout


def _release(repo: Path, *secondary: str) -> str:
    step = Release("pyproject.toml", *secondary)
    step.base_dir = repo
    step.name = "release"
    value = execute([step], root=repo)
    assert isinstance(value, str)
    return value


# (spec) Step kinds
def test_release_names_version_sources_by_their_files(tmp_path: Path) -> None:
    with pytest.raises(RoadieError, match="Release takes paths: got int"):
        Release(1)
    with pytest.raises(RoadieError, match="Release takes a directory or a manifest file"):
        Release()
    with pytest.raises(RoadieError, match="Release primary takes a path: got int"):
        Release("pyproject.toml", primary=1)
    (tmp_path / "notes.txt").write_text("x\n")
    notes = Release("notes.txt")
    notes.base_dir = tmp_path
    with pytest.raises(
        RoadieError,
        match=re.escape(
            "Release takes pyproject.toml, Cargo.toml, or package.json files: got notes.txt"
        ),
    ):
        notes._manifests()
    step = Release("pyproject.toml", "other/pyproject.toml", message="ship it")
    assert step.call_arguments()[0] == ["pyproject.toml", "other/pyproject.toml"]
    import roadie.build as api

    assert "PyProject" not in api.__all__


# (spec) Step kinds
def test_pyproject_requires_a_static_version_and_fingerprints_its_content(tmp_path: Path) -> None:
    source = PyProject("pyproject.toml")
    missing = (tmp_path / "pyproject.toml").resolve()
    message = re.escape(f"{missing} has no static [project] version")
    with pytest.raises(RoadieError) as caught:
        source.read_version(tmp_path)
    assert str(caught.value).startswith(f"cannot read {missing}: ")
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


_MANIFEST_CASES = [
    (
        PyProject,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        "not [valid\n",
        '[project]\nname = "demo"\n',
        "has no static [project] version",
        "is not valid TOML",
    ),
    (
        CargoManifest,
        "Cargo.toml",
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        "not [valid\n",
        '[package]\nname = "demo"\nedition = "2021"\n',
        "has no static Cargo version",
        "is not valid TOML",
    ),
    (
        PackageJson,
        "package.json",
        '{"name": "demo", "version": "0.1.0"}\n',
        '{"name": \n',
        '{"name": "demo"}\n',
        "has no version",
        "is not valid JSON",
    ),
]


# (spec) Step kinds
@pytest.mark.parametrize(
    "kind,filename,valid,invalid,versionless,versionless_message,invalid_message",
    _MANIFEST_CASES,
)
def test_a_manifest_fails_when_it_cannot_be_read_or_parsed(
    kind: type, filename: str, valid: str, invalid: str, versionless: str,
    versionless_message: str, invalid_message: str, tmp_path: Path,
) -> None:
    source = kind(filename)  # type: ignore[operator]
    file = (tmp_path / filename).resolve()
    with pytest.raises(RoadieError) as caught:
        source.read_version(tmp_path)
    assert str(caught.value).startswith(f"cannot read {file}: ")
    (tmp_path / filename).write_text(invalid)
    with pytest.raises(RoadieError) as caught_invalid:
        source.read_version(tmp_path)
    assert str(caught_invalid.value) == f"{file} {invalid_message}"
    (tmp_path / filename).write_text(versionless)
    with pytest.raises(RoadieError) as caught_versionless:
        source.read_version(tmp_path)
    assert str(caught_versionless.value) == f"{file} {versionless_message}"
    (tmp_path / filename).write_text(valid)
    assert source.read_version(tmp_path) == "0.1.0"


# (spec) Step kinds
@pytest.mark.parametrize(
    "kind,filename,valid,invalid,versionless,versionless_message,invalid_message",
    _MANIFEST_CASES,
)
def test_a_directory_skips_only_a_versionless_manifest(
    kind: type, filename: str, valid: str, invalid: str, versionless: str,
    versionless_message: str, invalid_message: str, tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    companion = bundle / "pyproject.toml"
    companion_text = '[project]\nname = "demo"\nversion = "0.1.0"\n'
    if filename == "pyproject.toml":
        companion = bundle / "Cargo.toml"
        companion_text = '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n'
    companion.write_text(companion_text)
    target = bundle / filename
    if target != companion:
        target.write_text(versionless)
    else:
        target.write_text(versionless)
    step = Release("bundle")
    step.base_dir = tmp_path
    groups = step._manifests()
    assert [item.name for _, found in groups for item in found] == [companion.name]

    target.write_text(invalid)
    with pytest.raises(RoadieError) as caught:
        step._manifests()
    file = target.resolve()
    assert str(caught.value) == f"{file} {invalid_message}"


# (spec) Step kinds
@posix_only
@pytest.mark.parametrize(
    "kind,filename,valid,invalid,versionless,versionless_message,invalid_message",
    _MANIFEST_CASES,
)
def test_a_directory_fails_on_an_unreadable_manifest(
    kind: type, filename: str, valid: str, invalid: str, versionless: str,
    versionless_message: str, invalid_message: str, tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    companion = bundle / "pyproject.toml"
    companion_text = '[project]\nname = "demo"\nversion = "0.1.0"\n'
    if filename == "pyproject.toml":
        companion = bundle / "Cargo.toml"
        companion_text = '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n'
    companion.write_text(companion_text)
    target = bundle / filename
    target.write_text(valid)
    target.chmod(0)
    try:
        step = Release("bundle")
        step.base_dir = tmp_path
        with pytest.raises(RoadieError) as caught:
            step._manifests()
        assert str(caught.value).startswith(f"cannot read {target.resolve()}: ")
    finally:
        target.chmod(0o644)


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
def test_cargo_and_package_json_keep_their_line_endings(tmp_path: Path) -> None:
    cargo = (
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    package = '{\n  "name": "demo",\n  "version": "0.1.0"\n}\n'
    cases = (
        (CargoManifest("Cargo.toml"), "Cargo.toml", cargo, 'version = "0.1.1"'),
        (PackageJson("package.json"), "package.json", package, '"version": "0.1.1"'),
    )
    for source, name, text, marker in cases:
        for ending, label in (("\n", "lf"), ("\r\n", "crlf")):
            directory = tmp_path / f"{label}-{name}"
            directory.mkdir()
            path = directory / name
            path.write_bytes(text.replace("\n", ending).encode())
            assert source.write_version(directory, "0.1.1") == (path.resolve(),)
            written = path.read_bytes()
            assert marker.encode() in written
            assert ending.encode() in written
            if ending == "\n":
                assert b"\r\n" not in written


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
def test_a_package_lock_beside_a_later_manifest_leaves_the_tree_clean(
    tmp_path: Path,
) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    web = repo / "web"
    web.mkdir()
    (web / "package.json").write_text('{"name": "web", "version": "0.1.0"}\n')
    (web / "package-lock.json").write_text("{}\n")
    _git(repo, "add", "web/package.json", "web/package-lock.json")
    _git(repo, "commit", "-q", "-m", "web")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    step = Release(".", "web/package.json")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed, match="package-lock.json"):
        execute([step], root=repo)
    assert _porcelain(repo) == ""
    assert _local_tags(repo) == {"v0.1.0"}
    assert _tags(bare) == set()


# (spec) Step kinds
def test_a_cargo_dotted_version_after_a_valid_manifest_leaves_the_tree_clean(
    tmp_path: Path,
) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    app = repo / "app"
    (app / "src").mkdir(parents=True)
    (app / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    (app / "Cargo.toml").write_text(
        'package.name = "demo"\npackage.version = "0.1.0"\npackage.edition = "2021"\n'
    )
    _git(repo, "add", "app")
    _git(repo, "commit", "-q", "-m", "cargo")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    step = Release("pyproject.toml", "app/Cargo.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed, match="version line is missing"):
        execute([step], root=repo)
    assert _porcelain(repo) == ""
    assert _local_tags(repo) == {"v0.1.0"}
    assert _tags(bare) == set()


# (spec) Step kinds
@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize(
    "query",
    [
        ("git", "rev-parse", "--show-toplevel"),
        ("git", "status", "--porcelain"),
        ("cargo", "locate-project", "--workspace", "--message-format", "plain"),
        ("uv", "version", "--frozen", "--dry-run", "0.1.1"),
        ("uv", "version", "--frozen", "0.1.1"),
    ],
)
def test_release_query_failures_keep_their_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: tuple[str, ...], started: bool
) -> None:
    repo, bare = _repository(tmp_path)
    source = "pyproject.toml"
    if query[0] == "cargo":
        source = "Cargo.toml"
        (repo / source).write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
        _git(repo, "add", source)
        _git(repo, "commit", "-qm", "cargo")
    late = query == ("uv", "version", "--frozen", "0.1.1")
    if late:
        (repo / "package.json").write_text('{"name": "demo", "version": "0.1.0"}\n')
        _git(repo, "add", "package.json")
        _git(repo, "commit", "-qm", "node manifest")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")

    start = _short_head(repo)
    real_run = subprocess.run

    def run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if tuple(args) != query or kwargs.get("check") is not False:
            return real_run(args, **kwargs)
        if not started:
            raise FileNotFoundError(errno.ENOENT, "program missing", query[0])
        return subprocess.CompletedProcess(args, 1, "", "operation refused")

    monkeypatch.setattr("roadie.system.command.prepare_command", lambda command: list(command))
    monkeypatch.setattr(subprocess, "run", run)
    step = Release("package.json", source) if late else Release(source)
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed) as caught:
        execute([step], root=repo)
    reason = "operation refused" if started else f"cannot run {query[0]}: [Errno {errno.ENOENT}] program missing"
    message = f"release failed: cannot release: {reason}"
    if late:
        message += f"; left: edited package.json; to undo: git reset --hard {start}"
    assert str(caught.value) == message
    if late:
        assert '"version": "0.1.1"' in (repo / "package.json").read_text()
        _undo(repo, message)
    assert _porcelain(repo) == ""
    assert _tags(bare) == set()


# (spec) Step kinds
def test_a_failing_uv_dry_run_leaves_the_tree_clean(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    app = repo / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    _git(repo, "add", "app/pyproject.toml")
    _git(repo, "commit", "-q", "-m", "app")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    step = Release("pyproject.toml", "app/pyproject.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed, match="cannot release:"):
        execute([step], root=repo)
    assert _porcelain(repo) == ""
    assert _local_tags(repo) == {"v0.1.0"}
    assert _tags(bare) == set()


# (spec) Step kinds
def test_a_failing_cargo_update_dry_run_leaves_the_tree_clean(
    tmp_path: Path,
) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    app = repo / "app"
    (app / "src").mkdir(parents=True)
    (app / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    (app / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    subprocess.run(
        ["cargo", "generate-lockfile"], cwd=app, check=True, capture_output=True
    )
    (app / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n\n'
        '[dependencies]\nmissing = { path = "missing" }\n'
    )
    _git(repo, "add", "app")
    _git(repo, "commit", "-q", "-m", "cargo")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    step = Release("pyproject.toml", "app/Cargo.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed, match="cannot release:"):
        execute([step], root=repo)
    assert _porcelain(repo) == ""
    assert _local_tags(repo) == {"v0.1.0"}
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


def _short_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def _undo(repo: Path, message: str) -> None:
    undo = message.split("; to undo: ", 1)[1].split("; to retry: ", 1)[0]
    subprocess.run(undo, shell=True, cwd=repo, check=True, capture_output=True, text=True)


# (spec) Step kinds
def test_a_failing_lockfile_update_names_what_it_left(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    app = repo / "app"
    (app / "src").mkdir(parents=True)
    (app / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    (app / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    subprocess.run(
        ["cargo", "generate-lockfile"], cwd=app, check=True, capture_output=True
    )
    _git(repo, "add", "app")
    _git(repo, "commit", "-q", "-m", "cargo")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    (app / "Cargo.lock").chmod(0o444)
    start = _short_head(repo)
    step = Release("pyproject.toml", "app/Cargo.toml")
    step.base_dir = repo
    step.name = "release"
    try:
        with pytest.raises(StepFailed) as caught:
            execute([step], root=repo)
        message = str(caught.value)
        assert message.endswith(
            f"; left: edited pyproject.toml, app/Cargo.toml; "
            f"to undo: git reset --hard {start}"
        )
        _undo(repo, message)
        assert _porcelain(repo) == ""
        assert _short_head(repo) == start
        assert _local_tags(repo) == {"v0.1.0"}
        assert _tags(bare) == set()
    finally:
        lock = app / "Cargo.lock"
        if lock.exists():
            lock.chmod(0o644)


# (spec) Step kinds
def test_an_unwritable_manifest_names_what_it_left(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    web = repo / "web"
    web.mkdir()
    package = web / "package.json"
    package.write_text('{"name": "web", "version": "0.1.0"}\n')
    _git(repo, "add", "web")
    _git(repo, "commit", "-q", "-m", "web")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    package.chmod(0o444)
    start = _short_head(repo)
    step = Release("pyproject.toml", "web/package.json")
    step.base_dir = repo
    step.name = "release"
    try:
        with pytest.raises(StepFailed) as caught:
            execute([step], root=repo)
        message = str(caught.value)
        denied = PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(package.resolve()))
        assert message == (
            f"release failed: cannot release: cannot write {package.resolve()}: {denied}; "
            f"left: edited pyproject.toml; to undo: git reset --hard {start}"
        )
        assert package.read_text() == '{"name": "web", "version": "0.1.0"}\n'
        _undo(repo, message)
        assert _porcelain(repo) == ""
        assert _local_tags(repo) == {"v0.1.0"}
        assert _tags(bare) == set()
    finally:
        package.chmod(0o644)


# (spec) Step kinds
def test_a_refused_commit_names_what_it_left(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    start = _short_head(repo)
    step = Release("pyproject.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed) as caught:
        execute([step], root=repo)
    message = str(caught.value)
    assert message == (
        f"release failed: cannot release: git exited with status 1; "
        f"left: edited pyproject.toml; to undo: git reset --hard {start}"
    )
    _undo(repo, message)
    assert _porcelain(repo) == ""
    assert _short_head(repo) == start
    assert _local_tags(repo) == {"v0.1.0"}
    assert _tags(bare) == set()


def _push_refused_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo, bare = _repository(tmp_path, "0.1.0")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    return repo, bare, hook


# (spec) Step kinds
def test_a_refused_push_names_what_it_left_and_can_be_retried(
    tmp_path: Path,
) -> None:
    repo, bare, _hook = _push_refused_repo(tmp_path / "undo")
    start = _short_head(repo)
    step = Release("pyproject.toml")
    step.base_dir = repo
    step.name = "release"
    with pytest.raises(StepFailed) as caught:
        execute([step], root=repo)
    message = str(caught.value)
    commit = _short_head(repo)
    assert message.endswith(
        f"; left: edited pyproject.toml, commit {commit}, tag v0.1.1; "
        f"to undo: git reset --hard {start} && git tag -d v0.1.1; "
        f"to retry: git push --atomic origin HEAD:main v0.1.1"
    )
    _undo(repo, message)
    assert _porcelain(repo) == ""
    assert _short_head(repo) == start
    assert _local_tags(repo) == {"v0.1.0"}
    assert _tags(bare) == set()

    retry_repo, retry_bare, retry_hook = _push_refused_repo(tmp_path / "retry")
    retry_step = Release("pyproject.toml")
    retry_step.base_dir = retry_repo
    retry_step.name = "release"
    with pytest.raises(StepFailed) as caught_retry:
        execute([retry_step], root=retry_repo)
    retry = str(caught_retry.value).split("; to retry: ", 1)[1]
    retry_hook.unlink()
    subprocess.run(
        retry, shell=True, cwd=retry_repo, check=True, capture_output=True, text=True
    )
    assert _tags(retry_bare) == {"v0.1.1"}


# (spec) Step kinds
def test_release_requires_run_before_it_and_a_failure_publishes_nothing(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    write_build(
        repo,
        f"gate = {failing_check()}\nrelease = Release('pyproject.toml', requires=(gate,))\n",
    )
    plan = load(repo).plan("release")
    assert [step.name for step in plan] == ["gate", "release"]
    with pytest.raises(StepFailed, match="gate failed"):
        execute(plan, root=repo)
    assert _tags(bare) == set()

    passing, passing_remote = _repository(tmp_path / "passing", "0.1.0")
    write_build(
        passing,
        f"gate = {passing_check()}\nrelease = Release('pyproject.toml', requires=(gate,))\n",
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


def _cargo_package(directory: Path, version: str, name: str = "demo") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "src").mkdir(exist_ok=True)
    (directory / "src" / "lib.rs").write_text("pub fn demo() {}\n")
    manifest = directory / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\nedition = "2021"\n\n'
        '[lib]\npath = "src/lib.rs"\n'
    )
    return manifest


def _package_json(directory: Path, version: str | None, name: str = "demo") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / "package.json"
    if version is None:
        file.write_text(f'{{"name": "{name}"}}\n')
    else:
        file.write_text(f'{{"name": "{name}", "version": "{version}"}}\n')
    return file


def _run(repo: Path, *sources: str, primary: str | None = None) -> str:
    step = Release(*sources, primary=primary)
    step.base_dir = repo
    step.name = "release"
    value = execute([step], root=repo)
    assert isinstance(value, str)
    return value


# (spec) Step kinds
def test_a_directory_releases_agreeing_manifests_and_skips_a_versionless_one(
    tmp_path: Path,
) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    cargo = _cargo_package(repo, "0.1.0")
    package = _package_json(repo, None)
    original = package.read_text()
    _git(repo, "add", "Cargo.toml", "src/lib.rs", "package.json")
    _git(repo, "commit", "-q", "-m", "manifests")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, ".") == "0.1.1"
    assert 'version = "0.1.1"' in (repo / "pyproject.toml").read_text()
    assert 'version = "0.1.1"' in cargo.read_text()
    assert package.read_text() == original
    assert "v0.1.1" in _tags(bare)


# (spec) Step kinds
def test_a_workspace_member_without_its_own_version_is_skipped(tmp_path: Path) -> None:
    repo, _bare = _repository(tmp_path, "0.1.0")
    member = repo / "member"
    member.mkdir()
    _project(member, "0.1.0")
    cargo = member / "Cargo.toml"
    cargo.write_text('[package]\nname = "member"\nversion.workspace = true\nedition = "2021"\n')
    original = cargo.read_text()
    _git(repo, "add", "member")
    _git(repo, "commit", "-q", "-m", "member")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, "member") == "0.1.1"
    assert 'version = "0.1.1"' in (member / "pyproject.toml").read_text()
    assert cargo.read_text() == original
    assert 'version = "0.1.0"' in (repo / "pyproject.toml").read_text()


# (spec) Step kinds
def test_a_directory_with_no_versioned_manifest_fails(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    _package_json(repo / "blank", None)
    _git(repo, "add", "blank/package.json")
    _git(repo, "commit", "-q", "-m", "blank")
    with pytest.raises(
        StepFailed,
        match=re.escape(
            "cannot release: blank has no versioned pyproject.toml, Cargo.toml or package.json"
        ),
    ):
        _run(repo, "blank")
    assert _tags(bare) == set()
    assert 'version = "0.1.0"' in (repo / "pyproject.toml").read_text()


# (spec) Step kinds
def test_a_file_source_does_not_release_the_manifest_beside_it(tmp_path: Path) -> None:
    repo, _bare = _repository(tmp_path, "0.1.0")
    package = _package_json(repo, "0.1.0")
    original = package.read_text()
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-q", "-m", "package")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, "pyproject.toml") == "0.1.1"
    assert 'version = "0.1.1"' in (repo / "pyproject.toml").read_text()
    assert package.read_text() == original


# (spec) Step kinds
def test_directories_and_files_release_only_the_named_manifests(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    other = repo / "other"
    cargo = _cargo_package(other, "0.1.0", name="other")
    package = _package_json(other, "0.1.0")
    original = package.read_text()
    _git(repo, "add", "other")
    _git(repo, "commit", "-q", "-m", "other")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, ".", "other/Cargo.toml") == "0.1.1"
    assert 'version = "0.1.1"' in (repo / "pyproject.toml").read_text()
    assert 'version = "0.1.1"' in cargo.read_text()
    assert package.read_text() == original
    assert "v0.1.1" in _tags(bare)


# (spec) Step kinds
def test_disagreeing_manifests_fail_until_primary_names_one(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.2.0")
    package = _package_json(repo, "0.1.0")
    _git(repo, "add", "package.json")
    _git(repo, "commit", "-q", "-m", "versions")
    pyproject = (repo / "pyproject.toml").read_text()
    original = package.read_text()
    with pytest.raises(
        StepFailed,
        match=re.escape(
            "cannot release: . holds different versions: "
            "pyproject.toml 0.2.0, package.json 0.1.0; name one with primary="
        ),
    ):
        _run(repo, ".")
    assert (repo / "pyproject.toml").read_text() == pyproject
    assert package.read_text() == original
    assert _tags(bare) == set()

    assert _run(repo, ".", primary="pyproject.toml") == "0.2.0"
    assert (repo / "pyproject.toml").read_text() == pyproject
    assert '"version": "0.2.0"' in package.read_text()
    assert _tags(bare) == {"v0.2.0"}


# (spec) Step kinds
def test_a_primary_the_sources_do_not_release_fails(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    vendor = _project(repo / "vendor", "9.9.9")
    original = vendor.read_text()
    root = (repo / "pyproject.toml").read_text()
    _git(repo, "add", "vendor/pyproject.toml")
    _git(repo, "commit", "-q", "-m", "vendor")
    with pytest.raises(
        StepFailed,
        match=re.escape("Release primary vendor/pyproject.toml is not among its sources"),
    ):
        _run(repo, ".", primary="vendor/pyproject.toml")
    assert (repo / "pyproject.toml").read_text() == root
    assert vendor.read_text() == original
    assert _tags(bare) == set()


# (spec) Step kinds
def test_a_subdirectory_manifest_is_left_alone(tmp_path: Path) -> None:
    repo, _bare = _repository(tmp_path, "0.1.0")
    nested = _project(repo / "nested", "9.9.9")
    original = nested.read_text()
    _git(repo, "add", "nested/pyproject.toml")
    _git(repo, "commit", "-q", "-m", "nested")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, ".") == "0.1.1"
    assert nested.read_text() == original
    assert 'version = "0.1.1"' in (repo / "pyproject.toml").read_text()


# (spec) Step kinds
def test_a_niva_shaped_tree_releases_the_named_directories(tmp_path: Path) -> None:
    repo, bare = _repository(tmp_path, "0.1.0")
    root_cargo = _cargo_package(repo, "0.1.0", name="niva")
    root_package = _package_json(repo, None, name="niva")
    desktop_package = _package_json(repo / "desktop", "0.1.0", name="desktop")
    tauri = _cargo_package(repo / "desktop" / "src-tauri", "0.1.0", name="niva-tauri")
    vendor = _project(repo / "vendor" / "x", "9.9.9")
    root_package_text = root_package.read_text()
    vendor_text = vendor.read_text()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "tree")
    _git(repo, "tag", "v0.1.0")
    _advance(repo)
    assert _run(repo, ".", "desktop", "desktop/src-tauri") == "0.1.1"
    for manifest in (repo / "pyproject.toml", root_cargo, desktop_package, tauri):
        assert "0.1.1" in manifest.read_text()
    assert root_package.read_text() == root_package_text
    assert vendor.read_text() == vendor_text
    assert "v0.1.1" in _tags(bare)
