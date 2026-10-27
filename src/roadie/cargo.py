"""Cargo workspace packages and the local dependency closure."""

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from forge.environment import repository_root


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
