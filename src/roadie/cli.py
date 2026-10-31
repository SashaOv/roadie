"""Run results of a Roadie build file and the steps they depend on."""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from roadie.engine.errors import RoadieError
from roadie.engine.graph import Graph, load
from roadie.engine.lock import clean_project
from roadie.engine.root import find_project_root
from roadie.engine.run import execute
from roadie.engine.step import Step

app = App(help=__doc__, version_flags=())


# (spec) Command line
@app.default
def run(
    *results: str,
    file: Annotated[
        Path,
        Parameter(
            name=["--file", "-f"],
            help="Build file, or a directory containing build.py. The default is .",
        ),
    ] = Path("."),
    base_dir: Annotated[
        Path | None,
        Parameter(name="--base-dir", help="Resolve step paths and commands from this directory"),
    ] = None,
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
        int | None,
        Parameter(
            name=["--jobs", "-j"],
            help="Run up to N independent steps at once. The default is the number of CPUs Roadie may use",
        ),
    ] = None,
) -> None:
    """Run the named results of a build file, by default its default result."""
    try:
        if jobs is None:
            jobs = os.process_cpu_count() or 1
        if jobs < 1:
            raise RoadieError("--jobs must be at least 1")
        directory = _build_directory(file)
        if clean:
            clean_project(find_project_root(directory.resolve()))
            return
        graph = load(directory, base_dir=base_dir)
        targets = _targets(graph, results)
        _one_requested(targets)
        steps = graph.plan_results(results) if results else graph.plan()
    except RoadieError as error:
        raise SystemExit(str(error)) from error
    try:
        execute(
            steps,
            root=graph.root,
            locked=locked,
            env=env or (),
            dry=dry_run,
            jobs=jobs,
            targets=targets,
        )
    except RoadieError as error:
        raise SystemExit(str(error)) from error


def _build_directory(file: Path) -> Path:
    if not file.exists():
        raise RoadieError(f"build file does not exist: {file}")
    return file.parent if file.is_file() else file


def _targets(graph: Graph, results: tuple[str, ...]) -> list[Step[Any]]:
    if not results:
        return [graph.result()]
    chosen: list[Step[Any]] = []
    for word in results:
        if word not in graph.results and Path(word).is_dir():
            raise RoadieError(
                f"{graph.path} has no result named {word!r}; "
                f"to use the build file in {word}, pass -f {word}"
            )
        chosen.append(graph.result(word))
    return chosen


def _one_requested(targets: list[Step[Any]]) -> None:
    limited: list[Step[Any]] = []
    for step in targets:
        if type(step).once_per_run and all(item is not step for item in limited):
            limited.append(step)
    if len(limited) < 2:
        return
    labels = [step.name or type(step).__name__ for step in limited]
    if len(labels) == 2:
        shown = f"{labels[0]} and {labels[1]}"
    else:
        shown = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    raise RoadieError(f"{shown} can each be requested only once per run; request one of them")


def main() -> None:
    app()
