"""Run a result of a Roadie build file and the steps it depends on."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from roadie.engine.display import format_step
from roadie.engine.errors import RoadieError
from roadie.engine.graph import load
from roadie.engine.lock import clean_project
from roadie.engine.root import find_project_root
from roadie.engine.run import execute, reuse_markers

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
    clean: Annotated[
        bool,
        Parameter(negative=False, help="Delete target/roadie and exit"),
    ] = False,
) -> None:
    """Run `result` of `<directory>/build.py`, by default the last one defined."""
    try:
        if clean:
            clean_project(find_project_root(directory.resolve()))
            return
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
            print(f"{format_step(step)}  # {'reuse' if reused else 'run'}")
        return
    try:
        execute(steps, root=graph.root, locked=locked, env=env or ())
    except RoadieError as error:
        raise SystemExit(str(error)) from error


def main() -> None:
    app()
