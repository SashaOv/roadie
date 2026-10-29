from dataclasses import dataclass
from pathlib import Path

import pytest
from support import plan_names, write_build

from roadie.build import CargoPackage, RoadieError, Step
from roadie.engine.graph import Graph, load
from roadie.engine.run import Run


# (spec) Loading a build file
# (spec) Results
def test_load_records_each_result_variable_and_build_directory(tmp_path: Path) -> None:
    write_build(tmp_path / "services", 'server = RustBinary(CargoPackage("server"))\n')
    graph = load(tmp_path / "services")
    server = graph.result("server")

    assert server.variable == "server"
    assert server.build_dir == (tmp_path / "services").resolve()


# (spec) Results
def test_default_result_is_the_last_step_called_not_the_last_variable(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path,
        'server = RustBinary(CargoPackage("first"))\n'
        "api = Group(server)\n"
        'server = RustBinary(CargoPackage("second"))\n',
    )
    graph = load(tmp_path)

    assert graph.result().package == CargoPackage("second")
    assert plan_names(tmp_path, "api") == [None, "api"]


# (spec) Including build files
def test_include_names_results_by_directory_and_loads_each_file_once(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path / "module",
        'server = RustBinary(CargoPackage("server"))\napi = Group(server)\n',
    )
    write_build(
        tmp_path,
        'first = include("module")\n'
        'second = include("module")\n'
        "fuzzed = Group(\n"
        "    first.api, Group(second.server)\n"
        ")\n",
    )

    assert plan_names(tmp_path) == ["module:server", "module:api", None, "fuzzed"]


# (spec) Loading a build file
def test_missing_build_file_names_the_directory(tmp_path: Path) -> None:
    with pytest.raises(RoadieError, match=f"{tmp_path} has no build.py"):
        load(tmp_path)


# (spec) Results
def test_build_file_without_results_fails(tmp_path: Path) -> None:
    write_build(tmp_path, "answer = 42\n")

    with pytest.raises(RoadieError, match="build.py defines no results"):
        load(tmp_path)


# (spec) Including build files
def test_include_cycle_names_the_files(tmp_path: Path) -> None:
    write_build(tmp_path / "a", 'b = include("../b")\n')
    write_build(tmp_path / "b", 'a = include("../a")\n')
    a, b = (tmp_path / name / "build.py" for name in "ab")

    with pytest.raises(
        RoadieError, match=f"build files include each other: {a} -> {b} -> {a}$"
    ):
        load(tmp_path / "a")


# (spec) Dependencies and attributes
# (spec) Plan order
def test_graph_finds_dependencies_from_step_attributes(tmp_path: Path) -> None:
    @dataclass(eq=False)
    class Custom(Step[str]):
        argument: object
        other: object = 42

        def build(self, run: Run) -> str:
            raise AssertionError("planning ran a step")

    first = Custom("input")
    second = Custom(first, other=Custom("another"))
    assert Graph(tmp_path / "build.py", {"result": second}, tmp_path).plan() == [
        first,
        second.other,
        second,
    ]


# (spec) Plan order
def test_step_dependency_cycle_names_the_steps(tmp_path: Path) -> None:
    @dataclass(eq=False)
    class Custom(Step[None]):
        argument: object = None

        def build(self, run: Run) -> None:
            pass

    first, second = Custom(), Custom()
    first.name, second.name = "first", "second"
    first.argument, second.argument = second, first
    with pytest.raises(RoadieError, match="steps depend on each other: first -> second -> first"):
        Graph(tmp_path / "build.py", {"first": first}, tmp_path).plan()
