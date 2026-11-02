import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, git_init, posix_only

from roadie.build import Files, Step
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.run import Run


def _abspath(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _git_warning(root: Path, spec: str) -> str:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", spec],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    warnings = [line for line in completed.stderr.splitlines() if line.startswith("warning:")]
    assert warnings, completed.stderr
    return "\n".join(warnings)


# (spec) Files input
def test_directory_in_a_work_tree_skips_ignored_files_unless_named(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    git_init(root)
    source = root / "src"
    source.mkdir()
    (source / "main.py").write_text("one")
    (root / ".gitignore").write_text("*.ignored\n")
    files = Files("src")
    baseline = files.fingerprint(root)

    (source / "skip.ignored").write_text("nope")
    assert files.fingerprint(root) == baseline
    (source / "main.py").write_text("two")
    assert files.fingerprint(root) != baseline

    named = Files("src/skip.ignored")
    named_before = named.fingerprint(root)
    (source / "skip.ignored").write_text("changed")
    assert named.fingerprint(root) != named_before

    missing = Files("src/absent.py")
    missing_before = missing.fingerprint(root)
    (source / "absent.py").write_text("created")
    assert missing.fingerprint(root) != missing_before

    (source / "kept.ignored").write_text("tracked")
    subprocess.run(["git", "add", "-f", "src/kept.ignored"], cwd=root, check=True)
    tracked = files.fingerprint(root)
    (source / "kept.ignored").write_text("edited")
    assert files.fingerprint(root) != tracked


# (spec) Files input
def test_directory_outside_a_work_tree_includes_every_file(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.ignored\n")
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("one")
    (source / "skip.ignored").write_text("kept")
    files = Files("src")
    baseline = files.fingerprint(tmp_path)
    (source / "skip.ignored").write_text("changed")
    assert files.fingerprint(tmp_path) != baseline


# (spec) Files input
def test_files_record_a_link_by_its_target_and_do_not_follow_it(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.txt").write_bytes(b"kept")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    nested = outside / "nested"
    nested.mkdir()
    (nested / "inner.txt").write_bytes(b"inner")
    (source / "link").symlink_to(outside / "secret.txt")
    (source / "dirlink").symlink_to(nested, target_is_directory=True)
    files = Files("src")
    named = Files("src/link")
    named_dir = Files("src/dirlink")
    baseline = files.fingerprint(tmp_path)
    named_baseline = named.fingerprint(tmp_path)
    named_dir_baseline = named_dir.fingerprint(tmp_path)

    (outside / "secret.txt").write_bytes(b"changed")
    (nested / "inner.txt").write_bytes(b"changed")
    assert files.fingerprint(tmp_path) == baseline
    assert named.fingerprint(tmp_path) == named_baseline
    assert named_dir.fingerprint(tmp_path) == named_dir_baseline

    (source / "link").unlink()
    (source / "link").symlink_to(outside / "other.txt")
    assert files.fingerprint(tmp_path) != baseline
    assert named.fingerprint(tmp_path) != named_baseline


# (spec) Files input
def test_files_in_a_work_tree_record_a_link_by_its_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    git_init(root)
    source = root / "src"
    source.mkdir()
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    (source / "link").symlink_to(outside / "secret.txt")
    files = Files("src")
    baseline = files.fingerprint(root)
    (outside / "secret.txt").write_bytes(b"changed")
    assert files.fingerprint(root) == baseline
    (source / "link").unlink()
    (source / "link").symlink_to(outside / "other.txt")
    assert files.fingerprint(root) != baseline


# (spec) Files input
def test_unreadable_file_fails_with_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_bytes(b"a")

    def fail(self: Path) -> bytes:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_bytes", fail)
    shown = Path(os.path.abspath(source / "a.txt"))
    with pytest.raises(RoadieError) as error:
        Files("src").fingerprint(tmp_path)
    assert str(error.value) == f"cannot read {shown}: denied"


# (spec) Files input
@posix_only
def test_unreadable_directory_outside_a_work_tree_fails_the_step(tmp_path: Path) -> None:
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "a.txt").write_text("a")
    path = _abspath(hidden)
    hidden.chmod(0)
    try:
        try:
            list(path.iterdir())
        except OSError as error:
            reason = str(error)
        else:
            raise AssertionError("the directory was readable")

        @dataclass(eq=False)
        class Bundle(Step[None]):
            files: Files

            def build(self, run: Run) -> None:
                return None

        step = Bundle(Files("hidden"))
        step.base_dir = tmp_path
        step.name = "bundle"
        with pytest.raises(StepFailed) as error:
            execute([step], root=tmp_path)
        assert str(error.value) == f"bundle failed: cannot read {path}: {reason}"
    finally:
        hidden.chmod(0o755)


# (spec) Files input
@posix_only
def test_unreadable_directory_in_a_work_tree_reports_the_git_warning(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    git_init(root)
    secret = root / "secret"
    secret.mkdir()
    (secret / "file.txt").write_text("hidden")
    secret.chmod(0)
    try:
        with pytest.raises(RoadieError) as error:
            Files("secret").fingerprint(root)
        warning = _git_warning(root, "secret")
        assert str(error.value) == f"cannot read {_abspath(secret)}: {warning}"
    finally:
        secret.chmod(0o755)
