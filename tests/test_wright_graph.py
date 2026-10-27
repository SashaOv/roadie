import ast
from dataclasses import dataclass
from pathlib import Path

import pytest
from forge.environment import repository_root
from wright import RustBinary, Step, cli, config
from wright.graph import Graph, WrightError, include, load
from wright.run import Run
from wright.steps import CargoPackage

JAMMER = repository_root() / "jammer"


def write_build(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from wright import *\nfrom jammer_build.steps import *\n\n{source}")
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


def test_load_records_each_result_variable_and_build_directory(tmp_path: Path) -> None:
    write_build(tmp_path / "jammer", 'server = RustBinary(CargoPackage("server"))\n')
    graph = load(tmp_path / "jammer")
    server = graph.result("server")

    assert server.variable == "server"
    assert server.build_dir == (tmp_path / "jammer").resolve()


def test_default_result_is_the_last_step_called_not_the_last_variable(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path,
        'server = RustBinary(CargoPackage("first"))\n'
        "api = OpenApi(server)\n"
        'server = RustBinary(CargoPackage("second"))\n',
    )
    graph = load(tmp_path)

    assert graph.result().package == CargoPackage("second")
    assert plan_names(tmp_path, "api") == [None, "api"]


def test_include_names_results_by_directory_and_loads_each_file_once(
    tmp_path: Path,
) -> None:
    write_build(
        tmp_path / "module",
        'server = RustBinary(CargoPackage("server"))\napi = OpenApi(server)\n',
    )
    write_build(
        tmp_path,
        'first = include("module")\n'
        'second = include("module")\n'
        "fuzzed = ContractFuzz(\n"
        "    first.api, JammerDevServer(second.server)\n"
        ")\n",
    )

    assert plan_names(tmp_path) == ["module:server", "module:api", None, "fuzzed"]


@pytest.mark.parametrize("flag", ["--dry", "--dry-run"])
def test_dry_run_prints_the_steps_in_dependency_order(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_cli([flag, str(JAMMER), "fuzzed"])

    assert capsys.readouterr().out.splitlines() == [
        "jammer_server = RustBinary(CargoPackage(name='jammer_server'))",
        "api = OpenApi(jammer_server, output='server/api.yaml')",
        "deployed = JammerDevServer(jammer_server, False)",
        "fuzzed = ContractFuzz(api, deployed)",
    ]


def test_cli_reports_an_unknown_result_with_the_known_ones() -> None:
    with pytest.raises(
        SystemExit,
        match="has no result named 'nope'; results: jammer_server, snap_viewer, api, python_models, web_schema, viewer_api, viewer_web_schema, generated, deployed, fuzzed",
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


def test_graph_finds_dependencies_from_step_attributes(tmp_path: Path) -> None:
    @dataclass(eq=False)
    class Custom(Step[str]):
        argument: object
        other: object = 42

        def build(self, run: Run) -> str:
            raise AssertionError("planning ran a step")

    first = Custom("input")
    second = Custom(first, other=Custom("another"))
    assert Graph(tmp_path / "build.py", {"result": second}).plan() == [
        first,
        second.other,
        second,
    ]


def test_step_dependency_cycle_names_the_steps(tmp_path: Path) -> None:
    @dataclass(eq=False)
    class Custom(Step[None]):
        argument: object = None

        def build(self, run: Run) -> None:
            pass

    first, second = Custom(), Custom()
    first.name, second.name = "first", "second"
    first.argument, second.argument = second, first
    with pytest.raises(WrightError, match="steps depend on each other: first -> second -> first"):
        Graph(tmp_path / "build.py", {"first": first}).plan()


def test_wright_does_not_import_harness_or_jammer() -> None:
    forbidden = ("forge.build_script", "jammer_build")
    violations: list[str] = []
    for path in (repository_root() / "forge/wright").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                names.extend(f"{node.module}.{alias.name}" for alias in node.names)
            if any(name == prefix or name.startswith(prefix + ".") for name in names for prefix in forbidden):
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == []


@pytest.mark.parametrize("override", [None, "override"])
@pytest.mark.parametrize("root_configured", [False, True])
@pytest.mark.parametrize("module_configured", [False, True])
def test_steps_record_base_directory_per_file_and_config(
    override: str | None, root_configured: bool, module_configured: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "project"
    root_config = 'config(base_dir="root-work")\n' if root_configured else ""
    module_config = 'config(base_dir="module-work")\n' if module_configured else ""
    write_build(root / "module", module_config + 'server = RustBinary(CargoPackage("module"))\n')
    write_build(root, 'before = RustBinary(CargoPackage("before"))\n' + root_config + 'module = include("module")\nincluded_result = module.server\nafter = RustBinary(CargoPackage("after"))\n')
    graph = load(root, base_dir=Path(override) if override else None)
    default = tmp_path / override if override else root
    assert graph.results["before"].base_dir == default
    assert graph.results["after"].base_dir == (root / "root-work" if root_configured else default)
    included = graph.results["included_result"]
    assert included.base_dir == (root / "module/module-work" if module_configured else tmp_path / override if override else root / "module")
    after = graph.results["after"]
    assert after.resolve("input.yaml") == after.base_dir / "input.yaml"


def test_cli_base_dir_applies_to_included_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project/module", 'server = RustBinary(CargoPackage("module"))\n')
    write_build(tmp_path / "project", 'module = include("module")\napi = OpenApi(module.server)\n')
    observed: list[Path] = []
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: observed.extend(step.base_dir for step in steps))
    run_cli(["--base_dir", "work", "project", "api"])
    assert observed == [tmp_path / "work", tmp_path / "work"]


def test_config_changes_only_subsequent_steps_and_is_released_after_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project", 'before = RustBinary(CargoPackage("before"))\nconfig(base_dir="work")\nafter = RustBinary(CargoPackage("after"))\n')
    graph = load(tmp_path / "project")
    assert graph.results["before"].base_dir == tmp_path / "project"
    assert graph.results["after"].base_dir == tmp_path / "project/work"
    assert RustBinary(CargoPackage("outside")).base_dir == tmp_path
    with pytest.raises(WrightError, match="config.*only.*build.py"):
        config(base_dir="outside")
    write_build(tmp_path / "bad", 'config(base_dir="work")\nraise RuntimeError("broken build")\n')
    with pytest.raises(RuntimeError, match="broken build"):
        load(tmp_path / "bad")
    assert RustBinary(CargoPackage("outside")).base_dir == tmp_path


def test_unknown_config_name_lists_known_names(tmp_path: Path) -> None:
    write_build(tmp_path, 'config(unknown="value")\nserver = RustBinary(CargoPackage("server"))\n')
    with pytest.raises(WrightError, match="unknown.*known.*base_dir"):
        load(tmp_path)


@pytest.mark.parametrize("value", ["None", "42"])
def test_config_rejects_a_non_path_base_directory(value: str, tmp_path: Path) -> None:
    write_build(tmp_path, f'config(base_dir={value})\nserver = RustBinary(CargoPackage("server"))\n')
    with pytest.raises(WrightError, match="base_dir must be a directory path"):
        load(tmp_path)


def test_tuple_attributes_declare_dependencies_and_dry_run_prints_them(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(tmp_path, '''from dataclasses import dataclass
from wright.run import Run

@dataclass(eq=False)
class Combined(Step[None]):
    inputs: tuple[Step, ...]
    def build(self, run: Run) -> None:
        pass

first = RustBinary(CargoPackage("first"))
second = RustBinary(CargoPackage("second"))
combined = Combined((first, second))
''')
    assert plan_names(tmp_path) == ["first", "second", "combined"]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "combined = Combined((first, second))"


def test_group_plans_and_prints_steps_as_variadic_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(tmp_path, 'first = RustBinary(CargoPackage("first"))\nsecond = RustBinary(CargoPackage("second"))\nall_steps = Group(first, second)\n')
    assert plan_names(tmp_path) == ["first", "second", "all_steps"]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "all_steps = Group(first, second)"


def test_group_rejects_a_non_step(tmp_path: Path) -> None:
    write_build(tmp_path, 'result = Group(42)\n')
    with pytest.raises(WrightError, match="Group.*steps"):
        load(tmp_path)


def test_cli_passes_locked_and_prints_keyword_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(tmp_path, 'server = RustBinary(CargoPackage("server"))\napi = OpenApi(server, output="api.yaml")\n')
    observed: list[bool] = []
    monkeypatch.setattr(cli, "execute", lambda steps, *, locked: observed.append(locked))
    run_cli(["--locked", str(tmp_path)])
    assert observed == [True]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "api = OpenApi(server, output='api.yaml')"


def test_file_output_is_keyword_only(tmp_path: Path) -> None:
    write_build(tmp_path, 'server = RustBinary(CargoPackage("server"))\napi = OpenApi(server, "api.yaml")\n')
    with pytest.raises(TypeError):
        load(tmp_path)


def test_jammer_generated_plan_contains_five_outputs_in_dependency_order() -> None:
    assert plan_names(JAMMER, "generated") == ["jammer_server", "api", "python_models", "web_schema", "snap_viewer", "viewer_api", "viewer_web_schema", "generated"]


def test_dry_run_omits_attributes_whose_value_is_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "from wright.run import Run\n\n"
        "@dataclass(eq=False)\n"
        "class Setting(Step[None]):\n"
        "    label: str | None\n"
        "    note: str | None = None\n"
        "    def build(self, run: Run) -> None:\n"
        "        pass\n\n"
        "item = Setting(None)\n",
    )
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "item = Setting()"
