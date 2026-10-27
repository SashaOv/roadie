"""Run a result of a Wright build file and the steps it depends on."""

from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from wright.graph import WrightError, load
from wright.run import execute
from wright.step import Step

app = App(help=__doc__, version_flags=())


@app.default
def run(
    directory: Path = Path("."),
    result: str | None = None,
    *,
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
        steps = load(directory).plan(result)
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
    arguments = ", ".join(
        reference(value) if isinstance(value, Step) else repr(value)
        for value in result.attributes().values()
    )
    return f"{type(result).__name__}({arguments})"


def reference(result: Step[Any]) -> str:
    return result.name or call(result)


def main() -> None:
    app()
