"""Load build.py files into a graph of results and plan the steps a result needs.

Loading a build.py runs no steps: each step call records a step and returns
it. A result's position is the order its step was called, not the order of
the module's variables, so reassigning a name does not move its result.
"""

import runpy
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roadie.engine.config import file_settings
from roadie.engine.errors import RoadieError
from roadie.engine.root import find_project_root, use_project_root
from roadie.engine.step import Step, is_reserved

BUILD_FILE = "build.py"


@dataclass(frozen=True)
class Graph:
    path: Path
    results: Mapping[str, Step[Any]]
    root: Path
    default: Step[Any] | None = None

    # (spec) Command line
    def result(self, name: str | None = None) -> Step[Any]:
        if name is None:
            if self.default is not None:
                return self.default
            return max(self.results.values(), key=lambda result: result.serial)
        if name not in self.results:
            known = ", ".join(self.results)
            raise RoadieError(
                f"{self.path} has no result named {name!r}; results: {known}"
            )
        return self.results[name]

    # (spec) Plan order
    def plan(self, name: str | None = None) -> list[Step[Any]]:
        """Return the steps `name` needs, each after the steps it takes results from."""
        ordered: list[Step[Any]] = []
        seen: set[int] = set()
        visiting: list[Step[Any]] = []

        def visit(result: Step[Any]) -> None:
            if any(item is result for item in visiting):
                cycle = " -> ".join(item.name or type(item).__name__ for item in (*visiting, result))
                raise RoadieError(f"steps depend on each other: {cycle}")
            if id(result) in seen:
                return
            visiting.append(result)
            for dependency in result.dependencies():
                visit(dependency)
            visiting.pop()
            seen.add(id(result))
            ordered.append(result)

        visit(self.result(name))
        return ordered

    # (spec) Command line
    def plan_results(self, names: Sequence[str]) -> list[Step[Any]]:
        """The union of each named result's plan, in the order the names are given."""
        ordered: list[Step[Any]] = []
        seen: set[int] = set()
        for name in names:
            for step in self.plan(name):
                if id(step) not in seen:
                    seen.add(id(step))
                    ordered.append(step)
        return ordered


@dataclass
class _Session:
    root: Path
    project: Path
    base_dir: Path | None = None
    graphs: dict[Path, Graph] = field(default_factory=dict)
    loading: list[Path] = field(default_factory=list)
    marks: dict[Path, list[Step[Any]]] = field(default_factory=dict)


_session: ContextVar[_Session | None] = ContextVar("roadie_session", default=None)


def load(directory: Path, *, base_dir: Path | None = None) -> Graph:
    resolved = directory.resolve()
    project = find_project_root(resolved)
    session = _Session(
        root=resolved,
        project=project,
        base_dir=base_dir.resolve() if base_dir is not None else None,
    )
    token = _session.set(session)
    try:
        with use_project_root(project):
            return _load(session, session.root)
    finally:
        _session.reset(token)


# (spec) Command line
def default_step(step: Step[Any]) -> Step[Any]:
    """Return `step` and mark it as the result run when no result is named."""
    session = _session.get()
    if session is None:
        raise RoadieError("default_step() works only in a build.py that Roadie loads")
    session.marks.setdefault(session.loading[-1], []).append(step)
    return step


# (spec) Including build files
def include(directory: str) -> Step[Any]:
    """Load `<directory>/build.py` and return a step for its default result.

    The file's results are attributes (`lib.test`) and are not dependencies.
    A result named like a `Step` member fails with `<path>: result <name> is
    reserved by Roadie in an included file`. A plan that holds the returned
    step fails with `<path> marks no default result` when the file has no
    `default_step` mark.
    """
    session = _session.get()
    if session is None:
        raise RoadieError("include() works only in a build.py that Roadie loads")
    graph = _load(session, (session.loading[-1] / directory).resolve())
    for name in graph.results:
        if is_reserved(name):
            raise RoadieError(
                f"{graph.path}: result {name} is reserved by Roadie in an included file"
            )
    return _Included(graph.path, graph.default, graph.results)


# (spec) Loading a build file
def _load(session: _Session, directory: Path) -> Graph:
    if directory in session.graphs:
        return session.graphs[directory]
    path = directory / BUILD_FILE
    if directory in session.loading:
        cycle = " -> ".join(
            str(step / BUILD_FILE) for step in (*session.loading, directory)
        )
        raise RoadieError(f"build files include each other: {cycle}")
    if not path.is_file():
        raise RoadieError(f"{directory} has no {BUILD_FILE}")

    with _loading(session, directory), file_settings(directory, session.base_dir):
        namespace = runpy.run_path(str(path), run_name=f"roadie:{path}")
    results = {
        name: value for name, value in namespace.items() if isinstance(value, Step)
    }
    default = _marked_default(session, directory, path, results)
    if not results:
        raise RoadieError(f"{path} defines no results")

    prefix = _prefix(session.root, directory)
    names = frozenset(results)
    for name, result in results.items():
        # An included step keeps the variable from the file that defined it.
        if result.variable is None:
            result.variable = name
            result.build_variables = names
        if result.name is None:
            result.name = f"{prefix}{name}"
    graph = Graph(path=path, results=results, root=session.project, default=default)
    session.graphs[directory] = graph
    return graph


@contextmanager
def _loading(session: _Session, directory: Path) -> Iterator[None]:
    session.loading.append(directory)
    try:
        yield
    finally:
        session.loading.pop()


def _marked_default(
    session: _Session,
    directory: Path,
    path: Path,
    results: Mapping[str, Step[Any]],
) -> Step[Any] | None:
    marked = session.marks.get(directory, [])
    if not marked:
        return None
    bound: list[tuple[str, Step[Any]]] = []
    for step in marked:
        name = next((item for item, value in results.items() if value is step), None)
        if name is None:
            raise RoadieError(f"{path}: the default result is not bound to a variable")
        if all(item is not step for _, item in bound):
            bound.append((name, step))
    if len(bound) > 1:
        names = ", ".join(name for name, _ in bound)
        raise RoadieError(f"{path} marks more than one default result: {names}")
    return bound[0][1]


class _Included(Step[tuple[object, ...]]):
    """A group of the included file's default result, with its results as attributes."""

    recorded = False

    def __init__(
        self,
        path: Path,
        default: Step[Any] | None,
        results: Mapping[str, Step[Any]],
    ) -> None:
        self._path = path
        self._results = dict(results)
        self.steps = () if default is None else (default,)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._results[name]
        except KeyError:
            raise AttributeError(name) from None

    def attributes(self) -> dict[str, object]:
        return {"steps": self.steps}

    def dependencies(self) -> Iterator[Step[Any]]:
        if not self.steps:
            raise RoadieError(f"{self._path} marks no default result")
        yield self.steps[0]

    def build(self, run: Any) -> tuple[object, ...]:
        if not self.steps:
            raise RoadieError(f"{self._path} marks no default result")
        values = tuple(run[step] for step in self.steps)
        if all(id(step) in run.reused for step in self.steps):
            print(f"reused {self.name or type(self).__name__}", flush=True)
        return values

    def call_arguments(self) -> tuple[list[object], dict[str, object]]:
        return list(self.steps), {}


def _prefix(root: Path, directory: Path) -> str:
    if directory == root:
        return ""
    if directory.is_relative_to(root):
        return f"{directory.relative_to(root).as_posix()}:"
    return f"{directory}:"
