import importlib.metadata
import platform
from pathlib import Path

from roadie.engine.inputs import Input, digest


# (spec) Python environment
class PythonEnv(Input):
    """See docs/spec.md, "Python environment"."""

    def __repr__(self) -> str:
        return "PythonEnv()"

    def fingerprint(self, base_dir: Path) -> str:
        return digest(
            {"python": platform.python_version(), "distributions": _distributions()}
        )


def _distributions() -> list[tuple[str, str]]:
    installed = [
        (dist.name, dist.version)
        for dist in importlib.metadata.distributions()
        if dist.name
    ]
    installed.sort()
    return installed
