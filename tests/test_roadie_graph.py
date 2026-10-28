from dataclasses import dataclass
from pathlib import Path

import pytest

from roadie import RustBinary, Step, cli, config
from roadie.graph import Graph, RoadieError, load
from roadie.run import Run
from roadie.steps import CargoPackage


def write_build(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from roadie import *\n\n{source}")
    return directory


def plan_names(directory: Path, result: str | None = None) -> list[str | None]:
    return [step.name for step in load(directory).plan(result)]


def run_cli(arguments: list[str]) -> None:
    command, bound, _ = cli.app.parse_args(arguments)
    command(*bound.args, **bound.kwargs)


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


# (spec) Settings
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


# (spec) Settings
# (spec) Command line
def test_cli_base_dir_applies_to_included_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project/module", 'server = RustBinary(CargoPackage("module"))\n')
    write_build(tmp_path / "project", 'module = include("module")\napi = Group(module.server)\n')
    observed: list[Path] = []
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: observed.extend(step.base_dir for step in steps))
    run_cli(["--base_dir", "work", "project", "api"])
    assert observed == [tmp_path / "work", tmp_path / "work"]


# (spec) Settings
def test_config_changes_only_subsequent_steps_and_is_released_after_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project", 'before = RustBinary(CargoPackage("before"))\nconfig(base_dir="work")\nafter = RustBinary(CargoPackage("after"))\n')
    graph = load(tmp_path / "project")
    assert graph.results["before"].base_dir == tmp_path / "project"
    assert graph.results["after"].base_dir == tmp_path / "project/work"
    assert RustBinary(CargoPackage("outside")).base_dir == tmp_path
    with pytest.raises(RoadieError, match="config.*only.*build.py"):
        config(base_dir="outside")
    write_build(tmp_path / "bad", 'config(base_dir="work")\nraise RuntimeError("broken build")\n')
    with pytest.raises(RuntimeError, match="broken build"):
        load(tmp_path / "bad")
    assert RustBinary(CargoPackage("outside")).base_dir == tmp_path


# (spec) Settings
# (spec) Environment files
def test_config_env_is_recorded_per_file_and_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(
        tmp_path / "project/module",
        'config(env=["module.env"])\nserver = RustBinary(CargoPackage("module"))\n',
    )
    write_build(
        tmp_path / "project",
        'config(env=["root.env"])\n'
        'before = RustBinary(CargoPackage("before"))\n'
        'module = include("module")\n'
        "included = module.server\n"
        'after = RustBinary(CargoPackage("after"))\n',
    )
    graph = load(tmp_path / "project")
    assert graph.results["before"].env_files == ((tmp_path / "project/root.env").resolve(),)
    assert graph.results["included"].env_files == ((tmp_path / "project/module/module.env").resolve(),)
    assert graph.results["after"].env_files == ((tmp_path / "project/root.env").resolve(),)
    assert RustBinary(CargoPackage("outside")).env_files == ()


# (spec) Environment files
# (spec) Command line
def test_cli_passes_env_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path, 'item = RustBinary(CargoPackage("server"))\n')
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: seen.update(kwargs))
    run_cli(["--env", "dev.env", "--env", "extra.env", str(tmp_path)])
    assert seen["env"] == [Path("dev.env"), Path("extra.env")] or seen["env"] == (
        Path("dev.env"),
        Path("extra.env"),
    )


# (spec) Settings
def test_unknown_config_name_lists_known_names(tmp_path: Path) -> None:
    write_build(tmp_path, 'config(unknown="value")\nserver = RustBinary(CargoPackage("server"))\n')
    with pytest.raises(RoadieError, match="unknown.*known settings: base_dir, env"):
        load(tmp_path)


# (spec) Settings
@pytest.mark.parametrize("value", ["None", "42"])
def test_config_rejects_a_non_path_base_directory(value: str, tmp_path: Path) -> None:
    write_build(tmp_path, f'config(base_dir={value})\nserver = RustBinary(CargoPackage("server"))\n')
    with pytest.raises(RoadieError, match="base_dir must be a directory path"):
        load(tmp_path)


# (spec) Dependencies and attributes
# (spec) Command line
def test_tuple_attributes_declare_dependencies_and_dry_run_prints_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("roadie.cargo.CargoPackage.fingerprint", lambda self, base_dir: f"package:{self.name}")
    write_build(tmp_path, '''from dataclasses import dataclass
from roadie.run import Run

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
    assert capsys.readouterr().out.splitlines()[-1] == "combined = Combined((first, second))  # run"


# (spec) Groups
# (spec) Command line
def test_group_plans_and_prints_steps_as_variadic_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("roadie.cargo.CargoPackage.fingerprint", lambda self, base_dir: f"package:{self.name}")
    write_build(tmp_path, 'first = RustBinary(CargoPackage("first"))\nsecond = RustBinary(CargoPackage("second"))\nall_steps = Group(first, second)\n')
    assert plan_names(tmp_path) == ["first", "second", "all_steps"]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "all_steps = Group(first, second)  # run"


# (spec) Groups
def test_group_rejects_a_non_step(tmp_path: Path) -> None:
    write_build(tmp_path, 'result = Group(42)\n')
    with pytest.raises(RoadieError, match="Group.*steps"):
        load(tmp_path)


# (spec) Command line
# (spec) Generated files
def test_cli_passes_locked_and_prints_keyword_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(eq=False, kw_only=True)\n"
        "class Note(File):\n"
        "    def build(self, run):\n"
        "        return self.output\n"
        "api = Note(output='api.yaml')\n",
    )
    observed: list[tuple[bool, object]] = []
    monkeypatch.setattr(
        cli,
        "execute",
        lambda steps, *, root, locked, env=(): observed.append((locked, env)),
    )
    run_cli(["--locked", str(tmp_path)])
    assert observed == [(True, ())]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "api = Note(output='api.yaml')  # run"


# (spec) File steps
def test_file_output_is_keyword_only(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(eq=False, kw_only=True)\n"
        "class Note(File):\n"
        "    def build(self, run):\n"
        "        return self.output\n"
        "api = Note('api.yaml')\n",
    )
    with pytest.raises(TypeError):
        load(tmp_path)


# (spec) Command line
def test_dry_run_omits_attributes_whose_value_is_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "from roadie.run import Run\n\n"
        "@dataclass(eq=False)\n"
        "class Setting(Step[None]):\n"
        "    label: str | None\n"
        "    note: str | None = None\n"
        "    def build(self, run: Run) -> None:\n"
        "        pass\n\n"
        "item = Setting(None)\n",
    )
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "item = Setting()  # run"
