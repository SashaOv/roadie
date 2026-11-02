"""The `Files` input: files and directories a step reads."""

import os
import subprocess
from pathlib import Path

from roadie.build.path import content_digest, link_target, walk_files
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest
from roadie.engine.root import project_root


def _entry_key(path: Path, root: Path | None) -> str:
    absolute = path if path.is_absolute() else path.absolute()
    if root is None:
        return str(absolute)
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        return str(absolute)
    return relative.as_posix() if relative.parts else "."


def _file_mark(path: Path) -> str:
    if path.is_symlink():
        return link_target(path)
    if not path.is_file():
        return "missing"
    return content_digest(path)


def _resolve(base_dir: Path, raw: str | Path) -> Path:
    path = Path(raw)
    joined = path if path.is_absolute() else base_dir / path
    return Path(os.path.abspath(joined))


# (spec) Files input
def listed_files(directory: Path) -> list[Path]:
    """Files a directory stands for.

    In a git work tree, the files `git ls-files --cached --others
    --exclude-standard` lists under it. Otherwise every file under it.
    Symbolic links are not followed, so a link to a directory is not
    descended. An unreadable directory fails with `cannot read <path>:
    <reason>`.
    """
    root = _work_tree(directory)
    if root is None:
        return walk_files(directory)
    relative = _relative_to_root(directory, root)
    if relative is None:
        return walk_files(directory)
    spec = relative.as_posix() if relative.parts else "."
    return _git_list(root, directory, spec)


def _work_tree(directory: Path) -> Path | None:
    probe = directory
    while True:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=probe,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            if not _cwd_denied(error, probe):
                return None
            parent = probe.parent
            if parent == probe:
                return None
            probe = parent
            continue
        if completed.returncode != 0:
            return None
        return Path(completed.stdout.strip()).resolve()


def _cwd_denied(error: OSError, probe: Path) -> bool:
    filename = error.filename
    if filename is None:
        return False
    return Path(os.fspath(filename)) == Path(os.fspath(probe))


def _relative_to_root(directory: Path, root: Path) -> Path | None:
    for candidate in (directory, Path(os.path.abspath(directory))):
        try:
            return candidate.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
    return None


def _git_list(root: Path, directory: Path, spec: str) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            spec,
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    text = completed.stderr.decode(errors="replace")
    warnings = [line for line in text.splitlines() if line.startswith("warning:")]
    if warnings:
        reason = "\n".join(warnings)
        raise RoadieError(f"cannot read {directory}: {reason}")
    if completed.returncode != 0:
        detail = text.strip()
        reason = detail or f"git exited with status {completed.returncode}"
        raise RoadieError(f"cannot read {directory}: {reason}")
    names = [item for item in completed.stdout.decode().split("\0") if item]
    return [root / name for name in names]


def _refuse_ignored_directory(raw: str | Path, directory: Path) -> None:
    """Fail when a directory in a work tree is ignored by git."""
    root = _work_tree(directory)
    if root is None:
        return
    relative = _relative_to_root(directory, root)
    if relative is None or not relative.parts:
        return
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative.as_posix()],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return
    shown = raw if isinstance(raw, str) else os.fspath(raw)
    raise RoadieError(
        f"Files({shown}): {shown} is ignored by git; take the step that builds it"
    )


# (spec) Files input
class Files(Input):
    def __init__(self, *paths: str | Path) -> None:
        self.paths = paths

    def __repr__(self) -> str:
        shown = ", ".join(
            repr(path if isinstance(path, str) else os.fspath(path)) for path in self.paths
        )
        return f"Files({shown})"

    def fingerprint(self, base_dir: Path) -> str:
        try:
            root = project_root().resolve()
        except RoadieError:
            root = None
        entries: dict[str, str] = {}
        for raw in self.paths:
            path = _resolve(base_dir, raw)
            if path.is_symlink():
                entries[_entry_key(path, root)] = _file_mark(path)
            elif path.is_dir():
                _refuse_ignored_directory(raw, path)
                for child in listed_files(path):
                    entries[_entry_key(child, root)] = _file_mark(child)
            else:
                entries[_entry_key(path, root)] = _file_mark(path)
        return digest(entries)
