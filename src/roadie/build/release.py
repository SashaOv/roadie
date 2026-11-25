"""Release a version from a `pyproject.toml` and push the tag."""

import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from roadie.build.manifests import (
    CargoManifest,
    PackageJson,
    PlannedVersion,
    PyProject,
    Versionless,
    release_errors,
)
from roadie.build.param import Param
from roadie.engine.errors import RoadieError
from roadie.engine.many import one_or_many
from roadie.engine.step import Step
from roadie.system import run_query

if TYPE_CHECKING:
    from roadie.engine.run import Run

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def _git(repository: Path, *args: str) -> str:
    with release_errors():
        return run_query(repository, "git", *args)


def _repository(base_dir: Path) -> Path:
    return Path(_git(base_dir, "rev-parse", "--show-toplevel").strip()).resolve()


def _status_path(line: str) -> str:
    entry = line[3:]
    if " -> " in entry:
        entry = entry.split(" -> ", 1)[1]
    return entry.strip().strip('"')


def _require_clean(repository: Path) -> None:
    """Fail when the work tree has changes other than this run's `target/`."""
    status = _git(repository, "status", "--porcelain")
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
    count = _git(repository, "rev-list", "--count", f"HEAD..{remote}/{branch}")
    if int(count.strip() or "0") > 0:
        raise RoadieError("cannot release: behind its upstream")


def _latest_tag(repository: Path) -> tuple[tuple[int, int, int], str] | None:
    listed = _git(repository, "tag", "--list", "v*", "--sort=-v:refname")
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
    head = _git(repository, "rev-parse", "HEAD").strip()
    tagged = _git(repository, "rev-parse", f"{tag}^{{commit}}").strip()
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


def _commit(repository: Path, paths: list[Path], version: str, message: str | None) -> None:
    _git(repository, "add", "--", *[os.fspath(path) for path in paths])
    if not _git(repository, "diff", "--cached", "--name-only").strip():
        return
    subject = f"v{version} release"
    if message:
        _git(repository, "commit", "-m", subject, "-m", message)
        return
    _git(repository, "commit", "-m", subject)


_ManifestKind = PyProject | CargoManifest | PackageJson
_MANIFESTS: tuple[tuple[str, type[_ManifestKind]], ...] = (
    ("pyproject.toml", PyProject),
    ("Cargo.toml", CargoManifest),
    ("package.json", PackageJson),
)
_MANIFEST_KINDS = dict(_MANIFESTS)


class _Manifest:
    def __init__(self, source: _ManifestKind, name: str, version: str | None) -> None:
        self.source = source
        self.name = name
        self.version = version

    def file(self, base_dir: Path) -> Path:
        return self.source.file(base_dir).resolve()

    def read(self, base_dir: Path) -> str:
        if self.version is None:
            self.version = self.source.read_version(base_dir)
        return self.version


def _version_or_none(kind: type[_ManifestKind], file: Path, base_dir: Path) -> str | None:
    try:
        return kind(file).read_version(base_dir)
    except Versionless:
        return None


def _in_directory(directory: Path, base_dir: Path) -> list[_Manifest]:
    found: list[_Manifest] = []
    for name, kind in _MANIFESTS:
        file = directory / name
        if not file.is_file():
            continue
        version = _version_or_none(kind, file, base_dir)
        if version is None:
            continue
        found.append(_Manifest(kind(file), name, version))
    return found


def _one_source(base_dir: Path, source: str) -> list[_Manifest]:
    raw = Path(source)
    path = raw.resolve() if raw.is_absolute() else (base_dir / raw).resolve()
    if path.is_dir():
        found = _in_directory(path, base_dir)
        if not found:
            raise RoadieError(
                f"cannot release: {source} has no versioned pyproject.toml, "
                "Cargo.toml or package.json"
            )
        return found
    kind = _MANIFEST_KINDS.get(path.name)
    if kind is not None and (path.is_file() or not path.exists()):
        return [_Manifest(kind(source), path.name, None)]
    raise RoadieError(
        "Release takes pyproject.toml, Cargo.toml, or package.json files: got " + source
    )


@dataclass
class _ReleasePlan:
    repository: Path
    remote: str
    branch: str
    start: str
    version: str
    message: str | None
    changes: list[tuple[_Manifest, PlannedVersion]] = field(default_factory=list)


# (spec) Step kinds
class Release(Step[str]):
    """Release the primary version; the value is that version.

    Each source, relative to the base directory, is a directory or a manifest
    file. A directory releases each of `pyproject.toml`, `Cargo.toml` and
    `package.json` in it that has its own static version, read as
    `PyProject`, `CargoManifest` and `PackageJson` read them; one without
    such a version is skipped. Subdirectories are not read. A directory with
    none fails with `cannot release: <directory> has no versioned
    pyproject.toml, Cargo.toml or package.json`. A file names that manifest
    alone. A path that is neither fails with `Release takes pyproject.toml,
    Cargo.toml, or package.json files: got <path>`. A value that is not a
    path fails with `Release takes paths: got <type>`. With no source it
    fails with `Release takes a directory or a manifest file`.

    The primary version is the manifest `primary=<path>` names, relative to
    the base directory. A value that is not a path fails with `Release
    primary takes a path: got <type>`. Naming one the sources do not release
    fails with `Release primary <path> is not among its sources`. Without
    `primary`, it is the first source's manifest. When that source is a
    directory releasing several manifests, their versions must match;
    otherwise the release fails before changing anything with `cannot
    release: <directory> holds different versions: <file> <version>, <file>
    <version>; name one with primary=`.

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

    The version is written to every released manifest that differs. When any file
    changed, those files are committed as `v<X.Y.Z> release`. `message` is a
    string, a `Param`, or `None` (the default). Another kind fails with
    `Release message takes a string or a Param: got <type>`. A message that
    is not empty is the body of that commit and the message of an annotated
    tag `v<X.Y.Z>`; otherwise the tag is lightweight. The step then pushes
    the branch and the tag atomically to the upstream, and prints
    `released v<X.Y.Z>`. `requires` is one step or any iterable of them,
    stored as a tuple. A value of the wrong kind fails with `Release requires
    takes steps: got <type>`.

    A manifest that cannot be written fails with `cannot release: cannot
    write <path>: <reason>`. A failure after the first change keeps its
    message and appends
    `; left: <changes>; to undo: git reset --hard <start>`, followed by
    ` && git tag -d v<X.Y.Z>` when the tag was created. `<changes>` lists, in
    order and only when they happened, `edited <paths>` (relative to the
    repository, POSIX form, joined with `, `), `commit <sha>` and
    `tag v<X.Y.Z>`. `<start>` is the starting commit and `<sha>` the release
    commit, both as `git rev-parse --short` prints them. A failed push also
    appends `; to retry: git push --atomic <remote> HEAD:<branch> v<X.Y.Z>`.
    """

    reusable: ClassVar[bool] = False
    recorded: ClassVar[bool] = False

    def __init__(
        self,
        *sources: str | os.PathLike[str],
        primary: str | os.PathLike[str] | None = None,
        requires: Step[Any] | Iterable[Step[Any]] = (),
        message: str | Param | None = None,
    ) -> None:
        if message is not None and not isinstance(message, (str, Param)):
            raise RoadieError(
                f"Release message takes a string or a Param: got {type(message).__name__}"
            )
        if primary is not None and not isinstance(primary, (str, os.PathLike)):
            raise RoadieError(f"Release primary takes a path: got {type(primary).__name__}")
        for source in sources:
            if not isinstance(source, (str, os.PathLike)):
                raise RoadieError(f"Release takes paths: got {type(source).__name__}")
        if not sources:
            raise RoadieError("Release takes a directory or a manifest file")
        self.sources = tuple(os.fspath(source) for source in sources)
        self.primary = None if primary is None else os.fspath(primary)
        self.requires = one_or_many(
            requires, Step, owner="Release", parameter="requires", takes="steps"
        )
        self.message = message

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        named: dict[str, object] = {}
        if self.primary is not None:
            named["primary"] = self.primary
        if self.requires:
            named["requires"] = self.requires
        if self.message is not None:
            named["message"] = self.message
        return list(self.sources), named

    def _manifests(self) -> list[tuple[str, list[_Manifest]]]:
        return [(source, _one_source(self.base_dir, source)) for source in self.sources]

    def _primary_version(self, groups: list[tuple[str, list[_Manifest]]]) -> str:
        if self.primary is not None:
            target = self.resolve(self.primary)
            for _, found in groups:
                for item in found:
                    if item.file(self.base_dir) == target:
                        return item.read(self.base_dir)
            raise RoadieError(f"Release primary {self.primary} is not among its sources")
        source, found = groups[0]
        versions = [item.read(self.base_dir) for item in found]
        if len(set(versions)) > 1:
            shown = ", ".join(
                f"{item.name} {version}" for item, version in zip(found, versions, strict=True)
            )
            raise RoadieError(
                f"cannot release: {source} holds different versions: {shown}; "
                "name one with primary="
            )
        return versions[0]

    def _release_message(self) -> str | None:
        message = self.message.value if isinstance(self.message, Param) else self.message
        if not message:
            return None
        return message

    def build(self, run: "Run") -> str:
        plan = self._plan()
        edited: list[Path] = []
        commit: str | None = None
        tagged = False
        try:
            for item, planned in plan.changes:
                item.source.apply_version(planned, edited)
            if edited:
                _commit(plan.repository, edited, plan.version, plan.message)
                head = _git(plan.repository, "rev-parse", "--short", "HEAD").strip()
                if head != plan.start:
                    commit = head
            if plan.message:
                _git(plan.repository, "tag", "-a", "-m", plan.message, f"v{plan.version}")
            else:
                _git(plan.repository, "tag", f"v{plan.version}")
            tagged = True
            _git(
                plan.repository,
                "push",
                "--atomic",
                plan.remote,
                f"HEAD:{plan.branch}",
                f"v{plan.version}",
            )
        except RoadieError as error:
            if not edited and commit is None and not tagged:
                raise
            left = _left_message(plan, edited, commit, tagged=tagged)
            raise RoadieError(f"{error}; {left}") from error
        print(f"released v{plan.version}", flush=True)
        return plan.version

    def _plan(self) -> "_ReleasePlan":
        groups = self._manifests()
        repository = _repository(self.base_dir)
        _require_clean(repository)
        remote, branch = _upstream(repository)
        _require_current(repository, remote, branch)
        start = _git(repository, "rev-parse", "--short", "HEAD").strip()
        version = _chosen_version(repository, self._primary_version(groups))
        changes: list[tuple[_Manifest, PlannedVersion]] = []
        seen: set[Path] = set()
        for _, found in groups:
            for item in found:
                file = item.file(self.base_dir)
                if file in seen:
                    continue
                seen.add(file)
                if item.read(self.base_dir) != version:
                    changes.append((item, item.source.plan_version(self.base_dir, version)))
        return _ReleasePlan(
            repository=repository,
            remote=remote,
            branch=branch,
            start=start,
            version=version,
            message=self._release_message(),
            changes=changes,
        )


def _left_message(
    plan: _ReleasePlan, edited: Sequence[Path], commit: str | None, *, tagged: bool
) -> str:
    left: list[str] = []
    if edited:
        shown = ", ".join(
            path.relative_to(plan.repository).as_posix()
            if path.is_relative_to(plan.repository)
            else path.as_posix()
            for path in edited
        )
        left.append(f"edited {shown}")
    if commit is not None:
        left.append(f"commit {commit}")
    if tagged:
        left.append(f"tag v{plan.version}")
    message = f"left: {', '.join(left)}; to undo: git reset --hard {plan.start}"
    if tagged:
        message += (
            f" && git tag -d v{plan.version}; to retry: git push --atomic {plan.remote} "
            f"HEAD:{plan.branch} v{plan.version}"
        )
    return message
