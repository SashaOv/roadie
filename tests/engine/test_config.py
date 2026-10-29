from pathlib import Path

import pytest
from support import write_build

from roadie.build import CargoPackage, RoadieError, RustBinary, config
from roadie.engine.graph import load


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
