"""The `PythonEnv` input: the interpreter and its installed distributions."""

import importlib.metadata
import platform
from pathlib import Path

from roadie.engine.inputs import Input, digest


def _distributions() -> list[tuple[str, str]]:
    installed = [
        (dist.name, dist.version)
        for dist in importlib.metadata.distributions()
        if dist.name
    ]
    installed.sort()
    return installed


# (spec) Python environment
class PythonEnv(Input):
    """The interpreter running Roadie and the distributions installed in it.

    The fingerprint is that interpreter's version and the name and version of
    every distribution. A check that takes `PythonEnv()` reruns when one of
    them changes, such as a mypy upgrade.
    """

    def __repr__(self) -> str:
        return "PythonEnv()"

    def fingerprint(self, base_dir: Path) -> str:
        return digest(
            {"python": platform.python_version(), "distributions": _distributions()}
        )
