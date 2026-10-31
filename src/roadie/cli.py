"""Run a result of a Roadie build file and the steps it depends on."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from roadie.engine.errors import RoadieError
from roadie.engine.graph import load
from roadie.engine.lock import clean_project
from roadie.engine.root import find_project_root
from roadie.engine.run import execute

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
            help="Print how a run would report each step, and build nothing",
        ),
    ] = False,
    clean: Annotated[
        bool,
        Parameter(negative=False, help="Delete target/roadie and exit"),
    ] = False,
    jobs: Annotated[
        int,
        Parameter(name=["--jobs", "-j"], help="Run up to N independent steps at once"),
    ] = 1,
) -> None:
    """Run `result` of `<directory>/build.py`, by default the last one defined."""
    try:
        if jobs < 1:
            raise RoadieError("--jobs must be at least 1")
        if clean:
            clean_project(find_project_root(directory.resolve()))
            return
        graph = load(directory, base_dir=base_dir)
        steps = graph.plan(result)
    except RoadieError as error:
        raise SystemExit(str(error)) from error
    try:
        execute(steps, root=graph.root, locked=locked, env=env or (), dry=dry_run, jobs=jobs)
    except RoadieError as error:
        raise SystemExit(str(error)) from error


def main() -> None:
    app()
