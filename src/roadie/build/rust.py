"""Rust: Cargo packages, workspace queries, and binaries built with cargo."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from roadie.build.binary import Binary
from roadie.build.files import Files
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest, run_state

if TYPE_CHECKING:
    from roadie.engine.run import Run

_CARGO_ENV_EXACT = frozenset({"RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTFLAGS"})
_CARGO_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_")
_ROOT_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".cargo/config.toml")


@dataclass(frozen=True)
class RustPackage:
    name: str
    root: Path
    local_dependencies: tuple[str, ...]


@dataclass(frozen=True)
class CargoWorkspace:
    root: Path
    packages: tuple[RustPackage, ...]


# (spec) Cargo workspace queries
def cargo_rust_packages(directory: Path) -> CargoWorkspace:
    result = subprocess.run(
        ("cargo", "metadata", "--no-deps", "--format-version", "1"),
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(result.stdout)
    root = Path(metadata["workspace_root"]).resolve()
    workspace_names = {package["name"] for package in metadata["packages"]}
    packages = []
    for package in metadata["packages"]:
        manifest = Path(package["manifest_path"])
        try:
            package_root = manifest.parent.resolve().relative_to(root)
        except ValueError:
            continue
        local_dependencies = tuple(
            dependency["name"]
            for dependency in package["dependencies"]
            if dependency["name"] in workspace_names
        )
        packages.append(RustPackage(package["name"], package_root, local_dependencies))
    return CargoWorkspace(root, tuple(packages))


# (spec) Cargo workspace queries
def select_rust_packages(
    packages: Sequence[RustPackage],
    changed_paths: Sequence[Path],
) -> tuple[str, ...]:
    """Packages whose files changed, plus every workspace package that depends on them."""
    if any(path.as_posix() in {"Cargo.toml", "Cargo.lock"} for path in changed_paths):
        return tuple(package.name for package in packages)

    selected = {
        package.name
        for package in packages
        if any(
            path == package.root or package.root in path.parents
            for path in changed_paths
        )
    }
    changed = True
    while changed:
        changed = False
        for package in packages:
            if package.name in selected:
                continue
            if selected.intersection(package.local_dependencies):
                selected.add(package.name)
                changed = True
    return tuple(package.name for package in packages if package.name in selected)


@dataclass(frozen=True)
class CargoSnapshot:
    workspace: CargoWorkspace
    rustc: str


def _load_snapshot(directory: Path) -> CargoSnapshot:
    version = subprocess.run(
        ("rustc", "--version"),
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    return CargoSnapshot(cargo_rust_packages(directory), version.stdout.strip())


def _snapshot(directory: Path) -> CargoSnapshot:
    directory = directory.resolve()
    snapshots: dict[Path, CargoSnapshot] = run_state(CargoSnapshot, dict)
    cached = _cached_snapshot(snapshots, directory)
    if cached is not None:
        return cached
    loaded = _load_snapshot(directory)
    return snapshots.setdefault(loaded.workspace.root, loaded)


def _cached_snapshot(
    cache: dict[Path, CargoSnapshot], directory: Path
) -> CargoSnapshot | None:
    for root, snapshot in cache.items():
        if directory.is_relative_to(root):
            return snapshot
    return None


def _cargo_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(os.environ.items())
        if key in _CARGO_ENV_EXACT or key.startswith(_CARGO_ENV_PREFIXES)
    }


def _closure(packages: Sequence[RustPackage], name: str) -> tuple[RustPackage, ...]:
    by_name = {package.name: package for package in packages}
    if name not in by_name:
        raise RoadieError(f"cargo package {name!r} is not in the workspace")
    selected: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current in selected:
            continue
        selected.add(current)
        pending.extend(by_name[current].local_dependencies)
    return tuple(package for package in packages if package.name in selected)


def _git_files(root: Path, paths: Sequence[str]) -> list[str]:
    if not paths:
        return []
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [item for item in result.stdout.decode().split("\0") if item]


def _content(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


# (spec) Cargo packages
@dataclass(frozen=True)
class CargoPackage(Input):
    name: str

    def fingerprint(self, base_dir: Path) -> str:
        snapshot = _snapshot(base_dir)
        root = snapshot.workspace.root
        packages = _closure(snapshot.workspace.packages, self.name)
        listed = set(_git_files(root, [package.root.as_posix() for package in packages]))
        for name in _ROOT_FILES:
            if (root / name).exists():
                listed.add(name)
        return digest(
            {
                "package": self.name,
                "rustc": snapshot.rustc,
                "env": _cargo_environment(),
                "files": {path: _content(root, path) for path in listed},
            }
        )


# (spec) Step kinds
@dataclass(eq=False)
class RustBinary(Binary):
    """Build the binary target of a Cargo package.

    Run `cargo build --locked -p <package> --message-format=json` in the base
    directory, take the executable cargo reports for the package's binary
    target, and copy it, keeping its mode, to the step's artifact (with `.exe`
    on Windows). Fail with `cargo build --locked -p <package> produced no
    executable` when cargo reports none, and with `... produced multiple
    executables` when it reports several different ones.
    """

    package: CargoPackage
    files: Files | None = None

    def build(self, run: Run) -> Path:
        completed = self._cmd(
            "cargo",
            "build",
            "--locked",
            "-p",
            self.package.name,
            "--message-format=json",
            capture_output=True,
        )
        source = _executable_from_cargo_messages(completed.stdout, self.package.name)
        destination = run.artifact(self, ".exe" if os.name == "nt" else "")
        shutil.copyfile(source, destination)
        os.chmod(destination, source.stat().st_mode)
        return destination


def _executable_from_cargo_messages(output: str, package: str) -> Path:
    """Return the bin executable cargo reported for `package`."""
    found: str | None = None
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            continue
        if parsed.get("reason") != "compiler-artifact":
            continue
        if not _is_package(parsed.get("package_id"), package):
            continue
        target = parsed.get("target")
        if not isinstance(target, dict):
            continue
        kinds = target.get("kind")
        if not isinstance(kinds, list) or "bin" not in kinds:
            continue
        executable = parsed.get("executable")
        if not isinstance(executable, str) or not executable:
            continue
        if found is not None and executable != found:
            raise RuntimeError(
                f"cargo build --locked -p {package} produced multiple executables"
            )
        found = executable
    if found is None:
        raise RuntimeError(f"cargo build --locked -p {package} produced no executable")
    return Path(found)


def _is_package(package_id: object, package: str) -> bool:
    """True for cargo's `#name@version` id and its `/name#version` id."""
    if not isinstance(package_id, str):
        return False
    if f"#{package}@" in package_id:
        return True
    marker = f"/{package}#"
    index = package_id.rfind(marker)
    if index < 0:
        return False
    rest = package_id[index + len(marker) :]
    return bool(rest) and rest[0].isdigit()
