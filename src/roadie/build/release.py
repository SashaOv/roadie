"""Release a version from a `pyproject.toml` and push the tag."""

import hashlib
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input
from roadie.engine.step import Step

if TYPE_CHECKING:
    from roadie.engine.run import Run

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RoadieError(f"cannot release: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        reason = detail or f"git exited with status {completed.returncode}"
        raise RoadieError(f"cannot release: {reason}")
    return completed


def _repository(base_dir: Path) -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "not a git repository"
        raise RoadieError(f"cannot release: {detail}")
    return Path(completed.stdout.strip()).resolve()


def _status_path(line: str) -> str:
    entry = line[3:]
    if " -> " in entry:
        entry = entry.split(" -> ", 1)[1]
    return entry.strip().strip('"')


def _require_clean(repository: Path) -> None:
    """Fail when the work tree has changes other than this run's `target/`."""
    status = _git(repository, "status", "--porcelain").stdout
    for line in status.splitlines():
        path = _status_path(line)
        if line.startswith("??") and (path == "target" or path.startswith("target/")):
            continue
        raise RoadieError("cannot release: uncommitted changes")


def _upstream(repository: Path) -> tuple[str, str]:
    completed = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RoadieError("cannot release: no upstream")
    remote, _, branch = completed.stdout.strip().partition("/")
    if not branch:
        raise RoadieError("cannot release: no upstream")
    return remote, branch


def _require_current(repository: Path, remote: str, branch: str) -> None:
    _git(repository, "fetch", remote)
    count = _git(repository, "rev-list", "--count", f"HEAD..{remote}/{branch}").stdout
    if int(count.strip() or "0") > 0:
        raise RoadieError("cannot release: behind its upstream")


def _latest_tag(repository: Path) -> tuple[tuple[int, int, int], str] | None:
    listed = _git(repository, "tag", "--list", "v*", "--sort=-v:refname").stdout
    for line in listed.splitlines():
        match = _TAG.fullmatch(line.strip())
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return version, match.group(0)
    return None


def _parse_version(version: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(version)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _chosen_version(repository: Path, current: str) -> str:
    latest = _latest_tag(repository)
    if latest is None:
        return current
    number, tag = latest
    head = _git(repository, "rev-parse", "HEAD").stdout.strip()
    tagged = _git(repository, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    if head == tagged:
        raise RoadieError("cannot release: nothing to release")
    parsed = _parse_version(current)
    if parsed is None:
        raise RoadieError(f"cannot release: version {current} is not X.Y.Z")
    if parsed < number:
        raise RoadieError(f"cannot release: version {current} is lower than {tag}")
    if parsed == number:
        major, minor, build = parsed
        return f"{major}.{minor}.{build + 1}"
    return current


def _commit(repository: Path, paths: list[Path], version: str) -> None:
    _git(repository, "add", "--", *[os.fspath(path) for path in paths])
    if not _git(repository, "diff", "--cached", "--name-only").stdout.strip():
        return
    _git(repository, "commit", "-m", f"v{version} release")


def _tracks(directory: Path, name: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", name],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


# (spec) Step kinds
class PyProject(Input):
    """A version source read from `[project] version`.

    The fingerprint is the file's content. A missing or dynamic version fails
    with `<path> has no static [project] version`. Writing runs
    `uv version --frozen <version>`, or `uv version <version>` when git tracks
    a `uv.lock` beside the file; that lockfile then joins the release commit,
    because `uv version` otherwise creates or rewrites it.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = path

    def __repr__(self) -> str:
        shown = self.path if isinstance(self.path, str) else os.fspath(self.path)
        return f"PyProject({shown!r})"

    def _file(self, base_dir: Path) -> Path:
        path = Path(self.path)
        return path if path.is_absolute() else (base_dir / path).resolve()

    def fingerprint(self, base_dir: Path) -> str:
        file = self._file(base_dir)
        if not file.is_file():
            return hashlib.sha256(b"missing").hexdigest()
        return hashlib.sha256(file.read_bytes()).hexdigest()

    def read_version(self, base_dir: Path) -> str:
        file = self._file(base_dir)
        try:
            data = tomllib.loads(file.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise RoadieError(f"{file} has no static [project] version") from error
        project = data.get("project") if isinstance(data, dict) else None
        dynamic = project.get("dynamic", []) if isinstance(project, dict) else []
        version = project.get("version") if isinstance(project, dict) else None
        listed = dynamic if isinstance(dynamic, list) else []
        if not isinstance(version, str) or "version" in listed:
            raise RoadieError(f"{file} has no static [project] version")
        return version

    def write_version(self, base_dir: Path, version: str) -> tuple[Path, ...]:
        file = self._file(base_dir)
        directory = file.parent
        tracked = _tracks(directory, "uv.lock")
        command = ["uv", "version", version]
        if not tracked:
            command = ["uv", "version", "--frozen", version]
        try:
            completed = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise RoadieError(f"cannot release: {error}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            reason = detail or f"uv exited with status {completed.returncode}"
            raise RoadieError(f"cannot release: {reason}")
        if tracked:
            return (file, directory / "uv.lock")
        return (file,)


# (spec) Step kinds
class Release(Step[str]):
    """Release the primary version; the value is that version.

    Never reused and keeps no record, so the output fingerprint is the step's
    own fingerprint. It fails, before changing anything, with `cannot release:
    uncommitted changes` (untracked files under `target/` do not count; the
    run writes its lock there), `cannot release: no upstream`, or `cannot
    release: behind its upstream` after `git fetch`.

    The latest tag is the first `v<X>.<Y>.<Z>` in `git tag --list v*
    --sort=-v:refname`; any other form, such as `v0.2.0-rc1`, is skipped. With
    no such tag, the primary version is released as it is. The same commit as
    that tag fails with `cannot release: nothing to release`. A primary version
    lower than the tag fails with `cannot release: version <version> is lower
    than <tag>`. A version that is not `X.Y.Z` then fails with `cannot release:
    version <version> is not X.Y.Z`. An equal version bumps the build part,
    `Z`. A higher version is released as it is.

    The version is written to every source that differs. When any file
    changed, those files are committed as `v<X.Y.Z> release`. The step then
    tags `v<X.Y.Z>`, pushes the branch and the tag atomically to the upstream,
    and prints `released v<X.Y.Z>`.
    """

    reusable: ClassVar[bool] = False
    recorded: ClassVar[bool] = False

    def __init__(
        self,
        primary: PyProject,
        *secondary: PyProject,
        requires: tuple[Step[object], ...] = (),
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.requires = requires

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        named: dict[str, object] = {}
        if self.requires:
            named["requires"] = self.requires
        return [self.primary, *self.secondary], named

    def build(self, run: "Run") -> str:
        repository = _repository(self.base_dir)
        _require_clean(repository)
        remote, branch = _upstream(repository)
        _require_current(repository, remote, branch)
        version = _chosen_version(repository, self.primary.read_version(self.base_dir))
        paths: list[Path] = []
        for source in (self.primary, *self.secondary):
            if source.read_version(self.base_dir) != version:
                paths.extend(source.write_version(self.base_dir, version))
        if paths:
            _commit(repository, paths, version)
        _git(repository, "tag", f"v{version}")
        _git(repository, "push", "--atomic", remote, f"HEAD:{branch}", f"v{version}")
        print(f"released v{version}", flush=True)
        return version
