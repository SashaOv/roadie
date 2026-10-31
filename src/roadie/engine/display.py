"""Show a step as the call `--dry` prints."""

import sys
from typing import Any

from roadie.engine.step import Step


def format_step(step: Step[Any]) -> str:
    """`<name> = <Class>(<arguments>)`, or `<Class>(<arguments>)` when unnamed."""
    text = _call(step)
    return f"{step.name} = {text}" if step.name else text


def announce(step: Step[Any]) -> None:
    """Print `build ...` for a step that keeps a record, just before it builds."""
    if type(step).recorded:
        print(f"build {format_step(step)}", flush=True)


def format_elapsed(seconds: float) -> str:
    """`<m:ss>`, whole minutes and two digits of seconds, rounded down."""
    whole = max(int(seconds), 0)
    minutes, secs = divmod(whole, 60)
    return f"{minutes}:{secs:02d}"


def report_built(step: Step[Any], elapsed: float) -> None:
    """Print `built <name> in <m:ss>` after a step that keeps a record builds."""
    sys.stdout.flush()
    print(f"built {step.name or type(step).__name__} in {format_elapsed(elapsed)}", flush=True)


def _call(result: Step[Any]) -> str:
    positional, named = result.call_arguments()
    arguments = [_argument(value) for value in positional]
    arguments.extend(f"{key}={_argument(value)}" for key, value in named.items())
    return f"{type(result).__name__}({', '.join(arguments)})"


def _argument(value: object) -> str:
    if isinstance(value, Step):
        return value.name or _call(value)
    if isinstance(value, tuple):
        items = ", ".join(_argument(item) for item in value)
        return f"({items}{',' if len(value) == 1 else ''})"
    return repr(value)
