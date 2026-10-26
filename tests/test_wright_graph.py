from pathlib import Path

import pytest
from forge.environment import repository_root
from wright import cli
from wright.graph import WrightError, include, load
from wright.steps import CargoPackage

JAMMER = repository_root() / "jammer"


def write_build(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from wright import *\n\n{source}")
    return directory


def plan_names(directory: Path, result: str | None = None) -> list[str | None]:
    return [step.name for step in load(directory).plan(result)]


def run_cli(arguments: list[str]) -> None:
    command, bound, _ = cli.app.parse_args(arguments)
    command(*bound.args, **bound.kwargs)


@pytest.mark.parametrize(
    ("result", "steps"),
    [
        ("fuzzed", ["jammer_server", "api", "deployed", "fuzzed"]),
        ("api", ["jammer_server", "api"]),
        ("deployed", ["jammer_server", "deployed"]),
        (None, ["jammer_server", "api", "deployed", "fuzzed"]),
    ],
)
def test_plan_holds_only_the_steps_a_result_depends_on(
    result: str | None, steps: list[str]
) -> None:
    assert plan_names(JAMMER, result) == steps


def test_default_result_is_the_last_step_called_not_the_last_variable(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path,
        'server = rust_binary(CargoPackage("first"))\n'
        "api = openapi(server)\n"
        'server = rust_binary(CargoPackage("second"))\n',
    )
    graph = load(tmp_path)

    assert graph.result().inputs == {"package": CargoPackage("second")}
    assert plan_names(tmp_path, "api") == [None, "api"]


def test_include_names_results_by_directory_and_loads_each_file_once(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path / "module",
        'server = rust_binary(CargoPackage("server"))\napi = openapi(server)\n',
    )
    write_build(
        tmp_path,
        'first = include("module")\n'
        'second = include("module")\n'
        "fuzzed = contract_fuzz(\n"
        "    first.api, deploy(second.server, DevDeploymentEnvironment())\n"
        ")\n",
    )

    assert plan_names(tmp_path) == ["module:server", "module:api", None, "fuzzed"]


@pytest.mark.parametrize("flag", ["--dry", "--dry-run"])
def test_dry_run_prints_the_steps_in_dependency_order(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli([flag, str(JAMMER), "fuzzed"])

    assert capsys.readouterr().out.splitlines() == [
        "jammer_server = rust_binary(CargoPackage(name='jammer_server'))",
        "api = openapi(jammer_server)",
        "deployed = deploy(jammer_server, DevDeploymentEnvironment())",
        "fuzzed = contract_fuzz(api, deployed)",
    ]


def test_cli_reports_an_unknown_result_with_the_known_ones() -> None:
    with pytest.raises(
        SystemExit,
        match="has no result named 'nope'; results: jammer_server, api, deployed, fuzzed",
    ):
        run_cli(["--dry", str(JAMMER), "nope"])


def test_missing_build_file_names_the_directory(tmp_path: Path) -> None:
    with pytest.raises(WrightError, match=f"{tmp_path} has no build.py"):
        load(tmp_path)


def test_build_file_without_results_fails(tmp_path: Path) -> None:
    write_build(tmp_path, "answer = 42\n")

    with pytest.raises(WrightError, match="build.py defines no results"):
        load(tmp_path)


def test_include_cycle_names_the_files(tmp_path: Path) -> None:
    write_build(tmp_path / "a", 'b = include("../b")\n')
    write_build(tmp_path / "b", 'a = include("../a")\n')
    a, b = (tmp_path / name / "build.py" for name in "ab")

    with pytest.raises(
        WrightError, match=f"build files include each other: {a} -> {b} -> {a}$"
    ):
        load(tmp_path / "a")


def test_include_works_only_while_wright_loads_a_build_file(tmp_path: Path) -> None:
    message = r"include\(\) works only in a build.py that Wright loads"
    with pytest.raises(WrightError, match=message):
        include("jammer")

    load(JAMMER)
    with pytest.raises(WrightError, match=message):
        include("jammer")

    write_build(tmp_path, 'missing = include("missing")\n')
    with pytest.raises(WrightError, match="has no build.py"):
        load(tmp_path)
    with pytest.raises(WrightError, match=message):
        include("jammer")
