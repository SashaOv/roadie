import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import plan_names, write_build

from roadie.build import CargoPackage, Group, RoadieError, Step, default_step, include
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


# (spec) Command line
def test_default_step_is_the_result_when_none_is_named(tmp_path: Path) -> None:
    write_build(
        tmp_path / "module",
        "api = default_step(Group())\nlater = Group(api)\n",
    )
    write_build(
        tmp_path,
        'module = include("module")\n'
        "checks = default_step(Group(module.api))\n"
        "release = Group(checks)\n",
    )

    assert plan_names(tmp_path) == ["module:api", "checks"]
    assert plan_names(tmp_path, "release") == ["module:api", "checks", "release"]
    assert plan_names(tmp_path / "module") == ["api"]


# (spec) Command line
def test_marking_two_default_results_names_both(tmp_path: Path) -> None:
    write_build(tmp_path, "a = default_step(Group())\nb = default_step(Group())\n")
    path = (tmp_path / "build.py").resolve()

    with pytest.raises(
        RoadieError,
        match=re.escape(f"{path} marks more than one default result: a, b"),
    ):
        load(tmp_path)


# (spec) Command line
def test_an_unbound_default_result_fails(tmp_path: Path) -> None:
    write_build(tmp_path, "other = Group()\ndefault_step(Group())\n")
    path = (tmp_path / "build.py").resolve()

    with pytest.raises(
        RoadieError,
        match=re.escape(f"{path}: the default result is not bound to a variable"),
    ):
        load(tmp_path)


# (spec) Command line
def test_default_step_outside_a_build_file_fails() -> None:
    with pytest.raises(
        RoadieError,
        match=r"default_step\(\) works only in a build.py that Roadie loads",
    ):
        default_step(Group())


# (spec) Results
def test_an_unknown_result_names_the_file_and_its_results(tmp_path: Path) -> None:
    write_build(tmp_path, "item = Check('true')\nother = Check('true')\n")
    graph = load(tmp_path)

    with pytest.raises(RoadieError, match="has no result named 'missing'") as error:
        graph.result("missing")

    message = str(error.value)
    assert str(graph.path) in message
    assert "item" in message
    assert "other" in message


# (spec) Including build files
def test_include_outside_a_loading_build_file_fails() -> None:
    with pytest.raises(
        RoadieError,
        match=r"include\(\) works only in a build.py that Roadie loads",
    ):
        include("other")


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


# (spec) Including build files
def test_include_stands_for_the_default_result_and_keeps_the_others(tmp_path: Path) -> None:
    write_build(
        tmp_path / "lib",
        "extra = Check('true')\nitem = default_step(Check('true'))\n",
    )
    write_build(
        tmp_path / "app",
        "item = default_step(Check('true'))\n",
    )
    write_build(
        tmp_path,
        'lib = include("lib")\napp = include("app")\nboth = Group(lib, app)\n',
    )
    graph = load(tmp_path)
    assert graph.results["lib"].extra.name == "lib:extra"
    assert plan_names(tmp_path, "both") == ["lib:item", "lib", "app:item", "app", "both"]
    assert plan_names(tmp_path, "lib") == ["lib:item", "lib"]
    assert "lib:extra" not in plan_names(tmp_path, "lib")


# (spec) Including build files
def test_an_included_file_without_a_default_fails_when_a_plan_holds_it(tmp_path: Path) -> None:
    write_build(tmp_path / "lib", "item = Check('true')\n")
    write_build(tmp_path, 'lib = include("lib")\nkeep = Check("true")\n')
    path = (tmp_path / "lib" / "build.py").resolve()
    assert plan_names(tmp_path, "keep") == ["keep"]
    with pytest.raises(RoadieError, match=re.escape(f"{path} marks no default result")):
        plan_names(tmp_path, "lib")


# (spec) Including build files
def test_an_included_result_named_like_a_step_member_fails(tmp_path: Path) -> None:
    write_build(tmp_path / "lib", "build = Check('true')\n")
    write_build(tmp_path, 'lib = include("lib")\n')
    path = (tmp_path / "lib" / "build.py").resolve()
    with pytest.raises(
        RoadieError,
        match=re.escape(f"{path}: result build is reserved by Roadie in an included file"),
    ):
        load(tmp_path)


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
