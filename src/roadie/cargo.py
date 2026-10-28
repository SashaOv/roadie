"""Cargo workspace packages and the local dependency closure."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from roadie.errors import RoadieError
from roadie.inputs import Input, digest

_CARGO_ENV_EXACT = frozenset({"RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTFLAGS"})
_CARGO_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_")
_ROOT_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".cargo/config.toml")
_cache: ContextVar[dict[Path, CargoSnapshot] | None] = ContextVar(
    "roadie_cargo_snapshot", default=None
)


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


@contextmanager
def cargo_snapshot() -> Iterator[None]:
    """Reuse cargo metadata and rustc --version for every fingerprint in the block."""
    token = _cache.set({})
    try:
        yield
    finally:
        _cache.reset(token)


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
    current = _cache.get()
    if isinstance(current, dict):
        cached = _cached_snapshot(current, directory)
        if cached is not None:
            return cached
    loaded = _load_snapshot(directory)
    if isinstance(current, dict):
        current.setdefault(loaded.workspace.root, loaded)
        return current[loaded.workspace.root]
    return loaded


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
