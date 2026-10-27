"""Run a result of a Wright build file and the steps it depends on."""

from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from wright.graph import WrightError, load
from wright.run import execute
from wright.step import Step
from wright.steps import Group

app = App(help=__doc__, version_flags=())


@app.default
def run(
    directory: Path = Path("."),
    result: str | None = None,
    *,
    base_dir: Annotated[Path | None, Parameter(name="--base_dir", help="Resolve step paths and commands from this directory")] = None,
    dry_run: Annotated[
        bool,
        Parameter(
            name=["--dry-run", "--dry"],
            negative=False,
            help="Print the steps the result needs and run none",
        ),
    ] = False,
) -> None:
    """Run `result` of `<directory>/build.py`, by default the last one defined."""
    try:
        steps = load(directory, base_dir=base_dir).plan(result)
    except WrightError as error:
        raise SystemExit(str(error)) from error
    if dry_run:
        for step in steps:
            print(f"{step.name} = {call(step)}" if step.name else call(step))
        return
    try:
        execute(steps)
    except WrightError as error:
        raise SystemExit(str(error)) from error


def call(result: Step[Any]) -> str:
    values = result.steps if isinstance(result, Group) else result.attributes().values()
    arguments = ", ".join(argument(value) for value in values)
    return f"{type(result).__name__}({arguments})"


def argument(value: object) -> str:
    if isinstance(value, Step):
        return reference(value)
    if isinstance(value, tuple):
        items = ", ".join(argument(item) for item in value)
        return f"({items}{',' if len(value) == 1 else ''})"
    return repr(value)


def reference(result: Step[Any]) -> str:
    return result.name or call(result)


def main() -> None:
    app()
