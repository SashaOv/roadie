"""Run a result of a Wright build file and the steps it depends on."""

from dataclasses import fields, is_dataclass
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
    locked: Annotated[bool, Parameter(negative=False, help="Fail when generated tracked files would change")] = False,
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
        execute(steps, locked=locked)
    except WrightError as error:
        raise SystemExit(str(error)) from error


def call(result: Step[Any]) -> str:
    if isinstance(result, Group):
        arguments = [argument(value) for value in result.steps]
    else:
        attributes = result.attributes()
        keywords: set[str] = {item.name for item in fields(result) if item.kw_only} if is_dataclass(result) else set()
        arguments = [
            argument(value)
            for key, value in attributes.items()
            if key not in keywords and value is not None
        ]
        arguments.extend(
            f"{key}={argument(value)}"
            for key, value in attributes.items()
            if key in keywords and value is not None
        )
    return f"{type(result).__name__}({', '.join(arguments)})"


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
