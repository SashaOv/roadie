"""Release a version from a `pyproject.toml` and push the tag."""

import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from roadie.build.param import Param
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input
from roadie.engine.many import one_or_many
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


def _commit(repository: Path, paths: list[Path], version: str, message: str | None) -> None:
    _git(repository, "add", "--", *[os.fspath(path) for path in paths])
    if not _git(repository, "diff", "--cached", "--name-only").stdout.strip():
        return
    subject = f"v{version} release"
    if message:
        _git(repository, "commit", "-m", subject, "-m", message)
        return
    _git(repository, "commit", "-m", subject)


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


_VERSION_LINE = re.compile(r"^(\s*version\s*=\s*)(['\"])(.*)\2(.*)$")


def _table_name(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
        return stripped[1:-1].strip()
    return None


def _replace_version(text: str, table: str, old: str, new: str) -> str:
    current: str | None = None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        header = _table_name(line)
        if header is not None:
            current = header
            continue
        if current != table:
            continue
        ending = ""
        body = line
        if body.endswith("\r\n"):
            body, ending = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, ending = body[:-1], "\n"
        match = _VERSION_LINE.match(body)
        if match is None or match.group(3) != old:
            continue
        quote = match.group(2)
        lines[index] = f"{match.group(1)}{quote}{new}{quote}{match.group(4)}{ending}"
        return "".join(lines)
    raise RoadieError(f"cannot release: {table} version line is missing")


def _cargo(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["cargo", *args],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RoadieError(f"cannot release: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        reason = detail or f"cargo exited with status {completed.returncode}"
        raise RoadieError(f"cannot release: {reason}")
    return completed


# (spec) Step kinds
class CargoManifest(Input):
    """A version source read from a `Cargo.toml`.

    The version is `[workspace.package] version`, or, without that table,
    `[package] version`. A manifest with neither as a string fails with
    `<path> has no static Cargo version`. The fingerprint is the file's
    content. Writing replaces the version string on that table's `version`
    line and leaves the rest of the file. When git tracks the workspace
    `Cargo.lock`, from `cargo locate-project --workspace`, writing then runs
    `cargo update --workspace --offline` and that lockfile joins the release
    commit. A failing cargo fails with `cannot release: <cargo's message>`.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = path

    def _file(self, base_dir: Path) -> Path:
        path = Path(self.path)
        return path if path.is_absolute() else (base_dir / path).resolve()

    def fingerprint(self, base_dir: Path) -> str:
        file = self._file(base_dir)
        if not file.is_file():
            return hashlib.sha256(b"missing").hexdigest()
        return hashlib.sha256(file.read_bytes()).hexdigest()

    def _located(self, file: Path) -> tuple[str, str]:
        try:
            data = tomllib.loads(file.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise RoadieError(f"{file} has no static Cargo version") from error
        workspace = data.get("workspace") if isinstance(data, dict) else None
        package_table = workspace.get("package") if isinstance(workspace, dict) else None
        if isinstance(workspace, dict) and "package" in workspace:
            version = package_table.get("version") if isinstance(package_table, dict) else None
            if isinstance(version, str):
                return "workspace.package", version
            raise RoadieError(f"{file} has no static Cargo version")
        package = data.get("package") if isinstance(data, dict) else None
        version = package.get("version") if isinstance(package, dict) else None
        if not isinstance(version, str):
            raise RoadieError(f"{file} has no static Cargo version")
        return "package", version

    def read_version(self, base_dir: Path) -> str:
        return self._located(self._file(base_dir))[1]

    def write_version(self, base_dir: Path, version: str) -> tuple[Path, ...]:
        file = self._file(base_dir)
        table, current = self._located(file)
        file.write_text(_replace_version(file.read_text(), table, current, version))
        located = _cargo(
            file.parent, "locate-project", "--workspace", "--message-format", "plain"
        )
        root = Path(located.stdout.strip()).parent
        if not _tracks(root, "Cargo.lock"):
            return (file,)
        _cargo(root, "update", "--workspace", "--offline")
        return (file, root / "Cargo.lock")


def _replace_json_version(text: str, old: str, new: str) -> str:
    """Replace the top-level `version` string and leave every other byte."""
    depth = 0
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < size:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    break
                end += 1
            if depth == 1 and text[index + 1 : end] == "version":
                cursor = end + 1
                while cursor < size and text[cursor] in " \t\r\n":
                    cursor += 1
                if cursor < size and text[cursor] == ":":
                    cursor += 1
                    while cursor < size and text[cursor] in " \t\r\n":
                        cursor += 1
                    if text.startswith(f'"{old}"', cursor):
                        return text[: cursor + 1] + new + text[cursor + 1 + len(old) :]
            index = end + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    raise RoadieError("cannot release: package.json version string was not found")


# (spec) Step kinds
class PackageJson(Input):
    """A version source read from a `package.json`.

    The version is the top-level `version` string. A file without one fails
    with `<path> has no version`. The fingerprint is the file's content.
    Writing replaces that string and leaves the rest of the file. A pnpm
    lockfile is left untouched. When git tracks a `package-lock.json` beside
    the file, the release fails before changing anything with `cannot release:
    <path> has a package-lock.json, which Roadie does not update`.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = path

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
            data = json.loads(file.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RoadieError(f"{file} has no version") from error
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str):
            raise RoadieError(f"{file} has no version")
        return version

    def write_version(self, base_dir: Path, version: str) -> tuple[Path, ...]:
        file = self._file(base_dir)
        if _tracks(file.parent, "package-lock.json"):
            raise RoadieError(
                f"cannot release: {file} has a package-lock.json, which Roadie does not update"
            )
        current = self.read_version(base_dir)
        file.write_text(_replace_json_version(file.read_text(), current, version))
        return (file,)


def _source(path: object) -> PyProject | CargoManifest | PackageJson:
    kinds: dict[str, type[PyProject | CargoManifest | PackageJson]] = {
        "pyproject.toml": PyProject,
        "Cargo.toml": CargoManifest,
        "package.json": PackageJson,
    }
    if isinstance(path, (str, os.PathLike)):
        shown = os.fspath(path)
        kind = kinds.get(Path(shown).name)
        if kind is not None:
            return kind(shown)
        accepted = ", ".join(tuple(kinds)[:-1]) + ", or " + tuple(kinds)[-1]
        raise RoadieError(f"Release takes {accepted} files: got {shown}")
    raise RoadieError(f"Release takes paths: got {type(path).__name__}")


# (spec) Step kinds
class Release(Step[str]):
    """Release the primary version; the value is that version.

    `primary` and each further source are paths, relative to the step's base
    directory.     The file name says the kind: `pyproject.toml` is a Python
    project, read and written as `PyProject` does, `Cargo.toml` is a Cargo
    manifest, and `package.json` is a JS package. Any other name fails with
    `Release takes pyproject.toml, Cargo.toml, or package.json files: got
    <path>`. A value that is not a path fails with `Release takes paths:
    got <type>`.

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
    changed, those files are committed as `v<X.Y.Z> release`. `message` is a
    string, a `Param`, or `None` (the default). Another kind fails with
    `Release message takes a string or a Param: got <type>`. A message that
    is not empty is the body of that commit and the message of an annotated
    tag `v<X.Y.Z>`; otherwise the tag is lightweight. The step then pushes
    the branch and the tag atomically to the upstream, and prints
    `released v<X.Y.Z>`. `requires` is one step or any iterable of them,
    stored as a tuple. A value of the wrong kind fails with `Release requires
    takes steps: got <type>`.
    """

    reusable: ClassVar[bool] = False
    recorded: ClassVar[bool] = False

    def __init__(
        self,
        primary: str | os.PathLike[str],
        *secondary: str | os.PathLike[str],
        requires: Step[Any] | Iterable[Step[Any]] = (),
        message: str | Param | None = None,
    ) -> None:
        if message is not None and not isinstance(message, (str, Param)):
            raise RoadieError(
                f"Release message takes a string or a Param: got {type(message).__name__}"
            )
        self.primary = _source(primary)
        self.secondary = tuple(_source(path) for path in secondary)
        self.requires = one_or_many(
            requires, Step, owner="Release", parameter="requires", takes="steps"
        )
        self.message = message

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        named: dict[str, object] = {}
        if self.requires:
            named["requires"] = self.requires
        if self.message is not None:
            named["message"] = self.message
        return [self.primary.path, *(source.path for source in self.secondary)], named

    def _release_message(self) -> str | None:
        message = self.message.value if isinstance(self.message, Param) else self.message
        if not message:
            return None
        return message

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
        message = self._release_message()
        if paths:
            _commit(repository, paths, version, message)
        if message:
            _git(repository, "tag", "-a", "-m", message, f"v{version}")
        else:
            _git(repository, "tag", f"v{version}")
        _git(repository, "push", "--atomic", remote, f"HEAD:{branch}", f"v{version}")
        print(f"released v{version}", flush=True)
        return version
