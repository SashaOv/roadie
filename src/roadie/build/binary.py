"""Steps that build executables."""

from pathlib import Path

from roadie.build.path import PathStep


# (spec) Step kinds
class Binary(PathStep):
    """A step that builds an executable; its value is the executable's path.

    The output fingerprint is the step's own fingerprint.
    """

    def output_fingerprint(self, fingerprint: str, value: Path) -> str:
        return fingerprint
