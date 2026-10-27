import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wright.cargo import CargoPackage
from wright.errors import WrightError
from wright.run import Run
from wright.step import Binary, Step


class Group(Step[tuple[object, ...]]):
    def __init__(self, *steps: Step[Any]) -> None:
        if any(not isinstance(step, Step) for step in steps):
            raise WrightError("Group takes only steps")
        self.steps = steps

    def build(self, run: Run) -> tuple[object, ...]:
        return tuple(run[step] for step in self.steps)


@dataclass(eq=False)
class RustBinary(Binary):
    package: CargoPackage

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
        return executable_from_cargo_messages(completed.stdout, self.package.name)


def executable_from_cargo_messages(output: str, package: str) -> Path:
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
