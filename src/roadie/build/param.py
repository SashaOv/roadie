"""The `Param` input: a value the command line passes to a step."""

from typing import Any

from roadie.engine.errors import RoadieError
from roadie.engine.parameters import Parameter

_MISSING: Any = object()


# (spec) Parameters
class Param(Parameter):
    """A command-line parameter.

    `name` is an identifier. `default`, when given, is a string or `None`;
    without it the parameter is required. Inside `build`, `value` is the
    step's value for this run: its own parameter, else the run-wide one, else
    this default. An unset optional parameter differs from an empty one.
    Outside a run, `value` fails with `parameter <name> has no value outside
    a run`.
    """

    def __init__(self, name: str, default: Any = _MISSING) -> None:
        if not isinstance(name, str) or not name.isidentifier():
            raise RoadieError(f"Param takes an identifier: got {name!r}")
        if default is _MISSING:
            super().__init__(name, default=None, required=True)
            self._shows_default = False
            return
        if default is not None and not isinstance(default, str):
            raise RoadieError(
                f"Param default takes a string or None: got {type(default).__name__}"
            )
        super().__init__(name, default=default, required=False)
        self._shows_default = True

    def __repr__(self) -> str:
        if not self._shows_default:
            return f"Param({self.name!r})"
        return f"Param({self.name!r}, default={self.default!r})"
