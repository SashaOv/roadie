"""Show a step as the call `--dry` prints."""

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
