"""Work out the files a step reads but does not declare."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from roadie.engine.inputs import Input
from roadie.engine.step import Step


def iter_inputs(step: Step[Any]) -> list[Input]:
    """Return every input a step holds, in attribute order."""
    found: list[Input] = []

    def walk(value: object) -> None:
        if isinstance(value, Input):
            found.append(value)
        elif isinstance(value, tuple):
            for item in value:
                walk(item)

    for value in step.attributes().values():
        walk(value)
    return found


def declared_files(step: Step[Any]) -> set[Path]:
    """Files a step declares through its inputs."""
    declared: set[Path] = set()
    for item in iter_inputs(step):
        declared.update(item.declared_files(step.base_dir))
    return declared


def built_files(values: Mapping[int, object], step: Step[Any]) -> set[Path]:
    """Files a step's dependencies built: a path, or every file under a directory."""
    built: set[Path] = set()
    for dependency in step.dependencies():
        if id(dependency) not in values:
            continue
        _expand_value(values[id(dependency)], built)
    return built


def _expand_value(value: object, built: set[Path]) -> None:
    if isinstance(value, Path):
        _expand_path(value, built)
    elif isinstance(value, tuple):
        for item in value:
            _expand_value(item, built)


def _expand_path(path: Path, built: set[Path]) -> None:
    if path.is_symlink() or not path.is_dir():
        built.add(path)
        return
    stack = [path]
    seen: set[Path] = set()
    while stack:
        current = stack.pop()
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink() or not child.is_dir():
                built.add(child)
            else:
                stack.append(child)


def listed_project_files(root: Path) -> set[Path]:
    """Files the `Files` listing lists under the project root.

    In a git work tree those are the tracked and untracked, not ignored,
    files `git ls-files` names; otherwise every file under the root.
    """
    completed = None
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0:
        names = [item for item in completed.stdout.decode().split("\0") if item]
        return {root / name for name in names}
    listed: set[Path] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name == ".git" and child.is_dir() and not child.is_symlink():
                continue
            if child.is_symlink() or not child.is_dir():
                listed.add(child)
            else:
                stack.append(child)
    return listed


def undeclared_reads(
    *,
    reads: set[Path],
    written: set[Path],
    declared: set[Path],
    built: set[Path],
    listed: set[Path],
    root: Path,
) -> list[str]:
    """Relative POSIX paths a program read but the step does not declare.

    A read counts only when it is a regular file inside the project
    root that the `Files` listing lists, and no open in the same trace
    wrote it. Declared and dependency-built files are covered.
    """
    root_resolved = root.resolve()
    declared_resolved = {_resolve(path) for path in declared}
    built_resolved = {_resolve(path) for path in built}
    covered = declared_resolved | built_resolved
    written_resolved = {_resolve(path) for path in written}
    listed_resolved = {_resolve(path) for path in listed}
    missing: list[str] = []
    for path in reads:
        resolved = _resolve(path)
        if resolved in written_resolved or resolved in covered:
            continue
        if resolved not in listed_resolved:
            continue
        try:
            relative = resolved.relative_to(root_resolved)
        except ValueError:
            continue
        if not resolved.is_file():
            continue
        missing.append(relative.as_posix())
    return sorted(set(missing))


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path
