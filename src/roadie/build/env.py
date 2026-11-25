import os
from pathlib import Path

from roadie.engine.errors import RoadieError
from roadie.engine.inputs import Input, digest


# (spec) Environment variables
class Env(Input):
    """See docs/spec.md, "Environment variables"."""

    def __init__(self, *names: str) -> None:
        if not names:
            raise RoadieError("Env takes at least one variable name")
        for name in names:
            if not isinstance(name, str):
                raise RoadieError(
                    f"Env takes variable names: got {type(name).__name__}"
                )
        self.names = names

    def __repr__(self) -> str:
        rendered = ", ".join(repr(name) for name in self.names)
        return f"Env({rendered})"

    def fingerprint(self, base_dir: Path) -> str:
        return digest([(name, os.environ.get(name)) for name in self.names])
