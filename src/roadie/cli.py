"""Run a result of a Roadie build file and the steps it depends on."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from roadie.engine.graph import RoadieError, load
from roadie.engine.run import execute, reuse_markers
from roadie.engine.step import Step

app = App(help=__doc__, version_flags=())


# (spec) Command line
@app.default
def run(
    directory: Path = Path("."),
    result: str | None = None,
    *,
    base_dir: Annotated[Path | None, Parameter(name="--base_dir", help="Resolve step paths and commands from this directory")] = None,
    locked: Annotated[bool, Parameter(negative=False, help="Fail when generated tracked files would change")] = False,
    env: Annotated[
        Sequence[Path] | None,
        Parameter(name="--env", help="KEY=VALUE environment file; may be repeated", negative=False),
    ] = None,
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
        graph = load(directory, base_dir=base_dir)
        steps = graph.plan(result)
    except RoadieError as error:
        raise SystemExit(str(error)) from error
    if dry_run:
        try:
            marks = reuse_markers(steps, root=graph.root, env=env or ())
        except RoadieError as error:
            raise SystemExit(str(error)) from error
        for step, reused in zip(steps, marks, strict=True):
            line = f"{step.name} = {call(step)}" if step.name else call(step)
            print(f"{line}  # {'reuse' if reused else 'run'}")
        return
    try:
        execute(steps, root=graph.root, locked=locked, env=env or ())
    except RoadieError as error:
        raise SystemExit(str(error)) from error


# (spec) Dry run output
def call(result: Step[Any]) -> str:
    positional, named = result.call_arguments()
    arguments = [argument(value) for value in positional]
    arguments.extend(f"{key}={argument(value)}" for key, value in named.items())
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
