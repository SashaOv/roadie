"""Load build.py files into a graph of results and plan the steps a result needs.

Loading a build.py runs no steps: each step call records a Result and returns
it. A result's position is the order its step was called, not the order of
the module's variables, so reassigning a name does not move its result.
"""

import itertools
import runpy
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

BUILD_FILE = "build.py"

_serials = itertools.count()


class WrightError(Exception):
    pass


@dataclass(eq=False)
class Result:
    step: str
    inputs: Mapping[str, object]
    implementation: Callable[..., object]
    serial: int = field(default_factory=lambda: next(_serials), init=False)
    name: str | None = field(default=None, init=False)

    def dependencies(self) -> Iterator["Result"]:
        return (value for value in self.inputs.values() if isinstance(value, Result))


class Binary(Result):
    pass


class File(Result):
    pass


class Service(Result):
    pass


class Verdict(Result):
    pass


# Set for the step whose implementation is running, so it can name its output.
current_result: ContextVar[Result | None] = ContextVar("wright_current_result", default=None)


@dataclass(frozen=True)
class Graph:
    path: Path
    results: Mapping[str, Result]

    def result(self, name: str | None = None) -> Result:
        if name is None:
            return max(self.results.values(), key=lambda result: result.serial)
        if name not in self.results:
            known = ", ".join(self.results)
            raise WrightError(f"{self.path} has no result named {name!r}; results: {known}")
        return self.results[name]

    def plan(self, name: str | None = None) -> list[Result]:
        """Return the steps `name` needs, each after the steps it takes results from."""
        ordered: list[Result] = []
        seen: set[int] = set()

        def visit(result: Result) -> None:
            if id(result) in seen:
                return
            seen.add(id(result))
            for dependency in result.dependencies():
                visit(dependency)
            ordered.append(result)

        visit(self.result(name))
        return ordered


@dataclass
class _Session:
    root: Path
    graphs: dict[Path, Graph] = field(default_factory=dict)
    loading: list[Path] = field(default_factory=list)


_session: ContextVar[_Session | None] = ContextVar("wright_session", default=None)


def load(directory: Path) -> Graph:
    session = _Session(root=directory.resolve())
    token = _session.set(session)
    try:
        return _load(session, session.root)
    finally:
        _session.reset(token)


def include(directory: str) -> SimpleNamespace:
    """Load `<directory>/build.py`, relative to the including file, and return its results."""
    session = _session.get()
    if session is None:
        raise WrightError("include() works only in a build.py that Wright loads")
    graph = _load(session, (session.loading[-1] / directory).resolve())
    return SimpleNamespace(**graph.results)


def _load(session: _Session, directory: Path) -> Graph:
    if directory in session.graphs:
        return session.graphs[directory]
    path = directory / BUILD_FILE
    if directory in session.loading:
        cycle = " -> ".join(str(step / BUILD_FILE) for step in (*session.loading, directory))
        raise WrightError(f"build files include each other: {cycle}")
    if not path.is_file():
        raise WrightError(f"{directory} has no {BUILD_FILE}")

    with _loading(session, directory):
        namespace = runpy.run_path(str(path), run_name=f"wright:{path}")
    results = {
        name: value for name, value in namespace.items() if isinstance(value, Result)
    }
    if not results:
        raise WrightError(f"{path} defines no results")

    prefix = _prefix(session.root, directory)
    for name, result in results.items():
        if result.name is None:
            result.name = f"{prefix}{name}"
    graph = Graph(path=path, results=results)
    session.graphs[directory] = graph
    return graph


@contextmanager
def _loading(session: _Session, directory: Path) -> Iterator[None]:
    session.loading.append(directory)
    try:
        yield
    finally:
        session.loading.pop()


def _prefix(root: Path, directory: Path) -> str:
    if directory == root:
        return ""
    if directory.is_relative_to(root):
        return f"{directory.relative_to(root).as_posix()}:"
    return f"{directory}:"
