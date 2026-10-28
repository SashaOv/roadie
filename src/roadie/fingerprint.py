"""Step fingerprints from class, code, inputs, and dependencies."""

import hashlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forge.environment import repository_root

import roadie
from roadie.errors import RoadieError
from roadie.inputs import Input, digest
from roadie.step import Step


def fingerprint(
    step: Step[Any],
    *,
    outputs: Mapping[Any, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    attributes: dict[str, str] = {}
    inputs: dict[str, Input] = {}
    dependencies: dict[str, Step[Any]] = {}
    for name, value in step.attributes().items():
        _classify(step, name, value, attributes, inputs, dependencies)
    output_hashes = {
        key if isinstance(key, int) else id(key): output
        for key, output in (outputs or {}).items()
    }
    label = step.name or type(step).__name__
    dependency_hashes: dict[str, str] = {}
    for name, dependency in dependencies.items():
        try:
            dependency_hashes[name] = output_hashes[id(dependency)]
        except KeyError:
            raise RoadieError(f"{label} dependency {name} has no output fingerprint") from None
    return digest(
        {
            "class": f"{type(step).__module__}.{type(step).__qualname__}",
            "code": _code_hash(step),
            "base_dir": _relative(step.base_dir),
            "env": dict(step.env_values if env is None else env),
            "attributes": attributes,
            "inputs": {name: item.fingerprint(step.base_dir) for name, item in inputs.items()},
            "dependencies": dependency_hashes,
        }
    )


def _classify(
    step: Step[Any],
    name: str,
    value: object,
    attributes: dict[str, str],
    inputs: dict[str, Input],
    dependencies: dict[str, Step[Any]],
) -> None:
    if isinstance(value, Step):
        dependencies[name] = value
        return
    if isinstance(value, Input):
        inputs[name] = value
        return
    if isinstance(value, tuple) and any(isinstance(item, (Step, Input)) for item in value):
        for index, item in enumerate(value):
            _classify(step, f"{name}[{index}]", item, attributes, inputs, dependencies)
        return
    if _plain(value):
        attributes[name] = repr(value)
        return
    label = step.name or type(step).__name__
    raise RoadieError(f"{label} attribute {name} must be a plain value, an input, or a step")


def _plain(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool, Path)):
        return True
    if isinstance(value, tuple):
        return all(item is None or isinstance(item, (str, int, float, bool, Path)) for item in value)
    return False


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        root = repository_root().resolve()
    except RuntimeError:
        return str(resolved)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    return relative.as_posix() if relative.parts else "."


def _code_hash(step: Step[Any]) -> str:
    paths: set[Path] = set()
    for cls in type(step).mro():
        module = sys.modules.get(cls.__module__)
        file = getattr(module, "__file__", None) if module is not None else None
        if isinstance(file, str) and file.endswith(".py"):
            paths.add(Path(file).resolve())
    package = Path(roadie.__file__).resolve().parent
    paths.update(path.resolve() for path in package.glob("*.py"))
    hashed = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        hashed.update(path.as_posix().encode())
        hashed.update(b"\0")
        hashed.update(path.read_bytes())
        hashed.update(b"\0")
    return hashed.hexdigest()
