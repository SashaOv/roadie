"""Node: the `NodeEnv` and `PnpmPackage` inputs and the pnpm workspace they read."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from roadie.build.files import listed_files
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest, run_state
from roadie.system import cannot_run_message, prepare_command

_NODE_ENV = ("NODE_ENV", "NODE_OPTIONS")
_ROOT_FILES = (
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    ".npmrc",
    "node_modules/.pnpm/lock.yaml",
)
_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)


@dataclass(frozen=True)
class NodeProject:
    name: str | None
    root: Path
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class NodeSnapshot:
    root: Path
    projects: tuple[NodeProject, ...]
    node: str
    pnpm: str


def _workspace_root(base_dir: Path) -> Path:
    directories = (base_dir.resolve(), *base_dir.resolve().parents)
    for marker in ("pnpm-workspace.yaml", "package.json"):
        for directory in directories:
            if (directory / marker).is_file():
                return directory
    raise RoadieError(f"no package.json at or above {base_dir}")


def _output(root: Path, label: str, *args: str) -> str:
    try:
        completed = subprocess.run(
            prepare_command(args),
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise RoadieError(cannot_run_message(args[0], error)) from error
    if completed.returncode != 0:
        raise RoadieError(f"{label} failed: {completed.stderr.strip()}")
    return completed.stdout


def _project_roots(root: Path) -> tuple[Path, ...]:
    if not (root / "pnpm-workspace.yaml").is_file():
        return (Path("."),)
    listed = json.loads(
        _output(root, "pnpm ls", "pnpm", "ls", "-r", "--json", "--depth", "-1")
    )
    return tuple(Path(project["path"]).resolve().relative_to(root) for project in listed)


def _projects(root: Path) -> tuple[NodeProject, ...]:
    project_roots = _project_roots(root)
    manifests = [
        json.loads((root / project / "package.json").read_text(encoding="utf-8"))
        for project in project_roots
    ]
    names = {manifest.get("name") for manifest in manifests} - {None}
    return tuple(
        NodeProject(
            manifest.get("name"),
            project,
            tuple(
                sorted(
                    {
                        dependency
                        for field in _DEPENDENCY_FIELDS
                        for dependency in manifest.get(field, {})
                        if dependency in names
                    }
                )
            ),
        )
        for project, manifest in zip(project_roots, manifests, strict=True)
    )


def _load_snapshot(root: Path) -> NodeSnapshot:
    node = _output(root, "node --version", "node", "--version").strip()
    pnpm = _output(root, "pnpm --version", "pnpm", "--version").strip()
    return NodeSnapshot(root, _projects(root), node, pnpm)


def _snapshot(base_dir: Path) -> NodeSnapshot:
    root = _workspace_root(base_dir)
    snapshots: dict[Path, NodeSnapshot] = run_state(NodeSnapshot, dict)
    if root not in snapshots:
        snapshots[root] = _load_snapshot(root)
    return snapshots[root]


def _file_digests(root: Path, relatives: list[str]) -> dict[str, str]:
    return {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in relatives
        if (root / relative).is_file()
    }


def _node_environment(snapshot: NodeSnapshot) -> dict[str, object]:
    manifests = [
        (project.root / "package.json").as_posix() for project in snapshot.projects
    ]
    return {
        "node": snapshot.node,
        "pnpm": snapshot.pnpm,
        "env": {name: os.environ[name] for name in _NODE_ENV if name in os.environ},
        "files": _file_digests(snapshot.root, [*manifests, *_ROOT_FILES]),
    }


# (spec) Node environment
class NodeEnv(Input):
    """The Node and pnpm versions, and the workspace's manifests and lockfile.

    A step that takes `NodeEnv()` reruns after a Node or pnpm upgrade, an
    edited `package.json` in any workspace project, or a changed lockfile.
    """

    def __repr__(self) -> str:
        return "NodeEnv()"

    def fingerprint(self, base_dir: Path) -> str:
        return digest(_node_environment(_snapshot(base_dir)))


def _closure(projects: tuple[NodeProject, ...], name: str) -> tuple[NodeProject, ...]:
    by_name = {project.name: project for project in projects if project.name}
    if name not in by_name:
        raise RoadieError(f"pnpm package {name!r} is not in the workspace")
    selected: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current not in selected:
            selected.add(current)
            pending.extend(by_name[current].dependencies)
    return tuple(by_name[selected_name] for selected_name in sorted(selected))


# (spec) Pnpm packages
@dataclass(frozen=True)
class PnpmPackage(Input):
    name: str

    def fingerprint(self, base_dir: Path) -> str:
        snapshot = _snapshot(base_dir)
        listed = {
            path.relative_to(snapshot.root).as_posix()
            for project in _closure(snapshot.projects, self.name)
            for path in listed_files(snapshot.root / project.root)
        }
        return digest(
            {
                "package": self.name,
                "environment": _node_environment(snapshot),
                "files": _file_digests(snapshot.root, sorted(listed)),
            }
        )
