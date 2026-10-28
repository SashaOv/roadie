"""Cargo workspace packages and the local dependency closure."""

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from forge.environment import repository_root

from roadie.errors import RoadieError
from roadie.inputs import Input, digest

_CARGO_ENV_EXACT = frozenset({"RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTFLAGS"})
_CARGO_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_")
_ROOT_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".cargo/config.toml")
_EMPTY = object()
_cache: ContextVar[object] = ContextVar("roadie_cargo_snapshot", default=None)


@dataclass(frozen=True)
class RustPackage:
    name: str
    root: Path
    local_dependencies: tuple[str, ...]


def cargo_rust_packages() -> tuple[RustPackage, ...]:
    root = repository_root()
    result = subprocess.run(
        ("cargo", "metadata", "--no-deps", "--format-version", "1"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(result.stdout)
    workspace_names = {package["name"] for package in metadata["packages"]}
    packages = []
    for package in metadata["packages"]:
        manifest = Path(package["manifest_path"])
        try:
            package_root = manifest.parent.relative_to(root)
        except ValueError:
            continue
        local_dependencies = tuple(
            dependency["name"]
            for dependency in package["dependencies"]
            if dependency["name"] in workspace_names
        )
        packages.append(RustPackage(package["name"], package_root, local_dependencies))
    return tuple(packages)


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
    packages: tuple[RustPackage, ...]
    rustc: str


@contextmanager
def cargo_snapshot() -> Iterator[None]:
    """Reuse cargo metadata and rustc --version for every fingerprint in the block."""
    token = _cache.set(_EMPTY)
    try:
        yield
    finally:
        _cache.reset(token)


def _load_snapshot() -> CargoSnapshot:
    version = subprocess.run(
        ("rustc", "--version"),
        cwd=repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return CargoSnapshot(cargo_rust_packages(), version.stdout.strip())


def _snapshot() -> CargoSnapshot:
    current = _cache.get()
    if isinstance(current, CargoSnapshot):
        return current
    loaded = _load_snapshot()
    if current is _EMPTY:
        _cache.set(loaded)
    return loaded


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


@dataclass(frozen=True)
class CargoPackage(Input):
    name: str

    def fingerprint(self, base_dir: Path) -> str:
        del base_dir
        root = repository_root()
        snapshot = _snapshot()
        packages = _closure(snapshot.packages, self.name)
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
