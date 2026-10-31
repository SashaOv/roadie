"""Step fingerprints from class, code, inputs, and dependencies."""

import hashlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import roadie
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest
from roadie.engine.root import project_root
from roadie.engine.step import Step, reject_reserved


# (spec) Step fingerprints
def fingerprint(
    step: Step[Any],
    *,
    outputs: Mapping[Any, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    reject_reserved(step)
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
            "class": _class_id(step),
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
        attributes[name] = _attribute_text(value)
        return
    label = step.name or type(step).__name__
    raise RoadieError(f"{label} attribute {name} must be a plain value, an input, or a step")


def _plain(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool, Path)):
        return True
    if isinstance(value, tuple):
        return all(item is None or isinstance(item, (str, int, float, bool, Path)) for item in value)
    return False


def _class_id(step: Step[Any]) -> str:
    module = type(step).__module__
    prefix = "roadie:"
    if module.startswith(prefix):
        # The loader names a build file `roadie:<absolute path>`.
        module = prefix + _relative(Path(module.removeprefix(prefix)))
    return f"{module}.{type(step).__qualname__}"


def _attribute_text(value: object) -> str:
    if isinstance(value, Path):
        return _path_attribute(value)
    if isinstance(value, tuple):
        inner = ", ".join(_attribute_text(item) for item in value)
        return f"({inner}{',' if len(value) == 1 else ''})"
    return repr(value)


def _path_attribute(path: Path) -> str:
    resolved = path.resolve()
    try:
        root = project_root().resolve()
    except RoadieError:
        return resolved.as_posix()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return resolved.as_posix()
    return relative.as_posix() if relative.parts else "."


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        root = project_root().resolve()
    except RoadieError:
        return str(resolved)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return str(resolved)
    return relative.as_posix() if relative.parts else "."


def _code_hash(step: Step[Any]) -> str:
    files: dict[str, Path] = {}

    def add(name: str, path: Path) -> None:
        # The loader names a build file `roadie:<absolute path>`. Key it by
        # the path relative to the project root, like the class id.
        if name.startswith("roadie:"):
            name = "roadie:" + _relative(Path(name.removeprefix("roadie:")))
        files[name] = path.resolve()

    for cls in type(step).mro():
        module_name = cls.__module__
        if module_name.startswith("roadie:"):
            # runpy loads the build file and drops it from sys.modules. The
            # path is the module name after `roadie:`.
            add(module_name, Path(module_name.removeprefix("roadie:")))
            continue
        module = sys.modules.get(module_name)
        file = getattr(module, "__file__", None) if module is not None else None
        if isinstance(file, str) and file.endswith(".py"):
            add(module_name, Path(file))
    package = Path(roadie.__file__).resolve().parent
    for path in package.rglob("*.py"):
        add(_module_name(package, path), path)
    hashed = hashlib.sha256()
    for name in sorted(files):
        hashed.update(name.encode())
        hashed.update(b"\0")
        hashed.update(files[name].read_bytes())
        hashed.update(b"\0")
    return hashed.hexdigest()


def _module_name(package: Path, path: Path) -> str:
    relative = path.resolve().relative_to(package.resolve().parent)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
