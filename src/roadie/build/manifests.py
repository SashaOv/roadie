"""Manifest formats a release reads and writes."""

import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input
from roadie.system import ProgramFailed, run_query


class CannotRelease(RoadieError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"cannot release: {reason}")


class Versionless(RoadieError):
    """A manifest without a static version, which a directory skips."""


@dataclass(frozen=True)
class PlannedVersion:
    file: Path
    text: str | None = None
    command: tuple[str, ...] | None = None
    lockfile: Path | None = None
    cargo_root: Path | None = None


class _ManifestSource(Input):
    def __init__(self, path: str | Path) -> None:
        self.path = path

    def __repr__(self) -> str:
        shown = self.path if isinstance(self.path, str) else os.fspath(self.path)
        return f"{type(self).__name__}({shown!r})"

    def file(self, base_dir: Path) -> Path:
        path = Path(self.path)
        return path if path.is_absolute() else (base_dir / path).resolve()

    def fingerprint(self, base_dir: Path) -> str:
        file = self.file(base_dir)
        if not file.is_file():
            return hashlib.sha256(b"missing").hexdigest()
        return hashlib.sha256(file.read_bytes()).hexdigest()

    def read_version(self, base_dir: Path) -> str:
        raise NotImplementedError

    def plan_version(self, base_dir: Path, version: str) -> PlannedVersion:
        raise NotImplementedError

    def apply_version(self, planned: PlannedVersion, edited: list[Path]) -> None:
        if planned.command is not None:
            with release_errors():
                run_query(planned.file.parent, *planned.command)
            edited.append(planned.file)
            if planned.lockfile is not None:
                edited.append(planned.lockfile)
        if planned.text is not None:
            _write_text(planned.file, planned.text, edited)
            if planned.cargo_root is not None and planned.lockfile is not None:
                _cargo(planned.cargo_root, "update", "--workspace", "--offline")
                edited.append(planned.lockfile)

    def write_version(self, base_dir: Path, version: str) -> tuple[Path, ...]:
        changed: list[Path] = []
        self.apply_version(self.plan_version(base_dir, version), changed)
        return tuple(changed)


# (spec) Step kinds
class PyProject(_ManifestSource):
    """A version source read from `[project] version`.

    The fingerprint is the file's content. A file that cannot be read fails
    with `cannot read <path>: <reason>`; one that is not valid TOML fails
    with `<path> is not valid TOML`. A missing or dynamic version fails
    with `<path> has no static [project] version`. Writing runs
    `uv version --frozen <version>`, or `uv version <version>` when git tracks
    a `uv.lock` beside the file; that lockfile then joins the release commit,
    because `uv version` otherwise creates or rewrites it.
    """

    def read_version(self, base_dir: Path) -> str:
        file = self.file(base_dir)
        data = _toml_data(file)
        project = data.get("project") if isinstance(data, dict) else None
        dynamic = project.get("dynamic", []) if isinstance(project, dict) else []
        version = project.get("version") if isinstance(project, dict) else None
        listed = dynamic if isinstance(dynamic, list) else []
        if not isinstance(version, str) or "version" in listed:
            raise Versionless(f"{file} has no static [project] version")
        return version

    def plan_version(self, base_dir: Path, version: str) -> PlannedVersion:
        file = self.file(base_dir)
        directory = file.parent
        tracked = _tracks(directory, "uv.lock")
        if tracked:
            command: tuple[str, ...] = ("uv", "version", version)
            dry: tuple[str, ...] = ("uv", "version", "--dry-run", version)
            lockfile: Path | None = directory / "uv.lock"
        else:
            command = ("uv", "version", "--frozen", version)
            dry = ("uv", "version", "--frozen", "--dry-run", version)
            lockfile = None
        with release_errors():
            run_query(directory, *dry)
        return PlannedVersion(file, command=command, lockfile=lockfile)


_VERSION_LINE = re.compile(r"^(\s*version\s*=\s*)(['\"])(.*)\2(.*)$")


# (spec) Step kinds
class CargoManifest(_ManifestSource):
    """A version source read from a `Cargo.toml`.

    The version is `[workspace.package] version`, or, without that table,
    `[package] version`. A file that cannot be read fails with
    `cannot read <path>: <reason>`; one that is not valid TOML fails with
    `<path> is not valid TOML`. A manifest with neither as a string fails with
    `<path> has no static Cargo version`. The fingerprint is the file's
    content. Writing replaces the version string on that table's `version`
    line and leaves the rest of the file. When git tracks the workspace
    `Cargo.lock`, from `cargo locate-project --workspace`, writing then runs
    `cargo update --workspace --offline` and that lockfile joins the release
    commit. A failing cargo fails with `cannot release: <cargo's message>`.
    """

    def _located(self, file: Path) -> tuple[str, str]:
        data = _toml_data(file)
        workspace = data.get("workspace") if isinstance(data, dict) else None
        package_table = workspace.get("package") if isinstance(workspace, dict) else None
        if isinstance(workspace, dict) and "package" in workspace:
            version = package_table.get("version") if isinstance(package_table, dict) else None
            if isinstance(version, str):
                return "workspace.package", version
            raise Versionless(f"{file} has no static Cargo version")
        package = data.get("package") if isinstance(data, dict) else None
        version = package.get("version") if isinstance(package, dict) else None
        if not isinstance(version, str):
            raise Versionless(f"{file} has no static Cargo version")
        return "package", version

    def read_version(self, base_dir: Path) -> str:
        return self._located(self.file(base_dir))[1]

    def plan_version(self, base_dir: Path, version: str) -> PlannedVersion:
        file = self.file(base_dir)
        table, current = self._located(file)
        text = _replace_version(_read_text(file), table, current, version)
        located = _cargo(
            file.parent, "locate-project", "--workspace", "--message-format", "plain"
        )
        root = Path(located.strip()).parent
        if not _tracks(root, "Cargo.lock"):
            return PlannedVersion(file, text=text)
        _cargo(root, "update", "--workspace", "--offline", "--dry-run")
        return PlannedVersion(
            file, text=text, lockfile=root / "Cargo.lock", cargo_root=root
        )


# (spec) Step kinds
class PackageJson(_ManifestSource):
    """A version source read from a `package.json`.

    The version is the top-level `version` string. A file that cannot be
    read fails with `cannot read <path>: <reason>`; one that is not valid
    JSON fails with `<path> is not valid JSON`. A file without one fails
    with `<path> has no version`. The fingerprint is the file's content.
    Writing replaces that string and leaves the rest of the file. A pnpm
    lockfile is left untouched. When git tracks a `package-lock.json` beside
    the file, the release fails before changing anything with `cannot release:
    <path> has a package-lock.json, which Roadie does not update`.
    """

    def read_version(self, base_dir: Path) -> str:
        file = self.file(base_dir)
        data = _json_data(file)
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str):
            raise Versionless(f"{file} has no version")
        return version

    def plan_version(self, base_dir: Path, version: str) -> PlannedVersion:
        file = self.file(base_dir)
        if _tracks(file.parent, "package-lock.json"):
            raise CannotRelease(
                f"{file} has a package-lock.json, which Roadie does not update"
            )
        current = self.read_version(base_dir)
        text = _replace_json_version(_read_text(file), current, version)
        return PlannedVersion(file, text=text)


@contextmanager
def release_errors(operation: str | None = None) -> Iterator[None]:
    """Convert OSError and ProgramFailed to CannotRelease, with optional context."""
    try:
        yield
    except (OSError, ProgramFailed) as cause:
        error = str(cause) if operation is None else f"{operation}: {cause}"
        raise CannotRelease(error) from cause


def _tracks(directory: Path, name: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", name],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _cargo(directory: Path, *args: str) -> str:
    with release_errors():
        return run_query(directory, "cargo", *args)


def _read_text(path: Path) -> str:
    try:
        with path.open(newline="") as handle:
            return handle.read()
    except OSError as error:
        raise RoadieError(f"cannot read {path}: {error}") from error


def _toml_data(file: Path) -> dict[str, object]:
    try:
        text = _read_text(file)
    except UnicodeDecodeError as error:
        raise RoadieError(f"{file} is not valid TOML") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RoadieError(f"{file} is not valid TOML") from error
    return data if isinstance(data, dict) else {}


def _json_data(file: Path) -> object:
    try:
        text = _read_text(file)
    except UnicodeDecodeError as error:
        raise RoadieError(f"{file} is not valid JSON") from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RoadieError(f"{file} is not valid JSON") from error


def _write_text(path: Path, text: str, changed: list[Path]) -> None:
    """Write `text`, adding `path` to `changed` once opening it has truncated it."""
    with release_errors(f"cannot write {path}"), path.open("w", newline="") as handle:
        changed.append(path)
        handle.write(text)


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
    raise CannotRelease(f"{table} version line is missing")


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
    raise CannotRelease("package.json version string was not found")
