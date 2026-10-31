from pathlib import Path

import pytest
from support import Value, execute, plan_names, run_cli, write_build

from roadie.build import Group, RoadieError
from roadie.engine.graph import load


# (spec) Step kinds
def test_group_value_contains_its_steps_values() -> None:
    first, second = Value("first"), Value("second")
    group = Group(first, second)
    assert execute([first, second, group]) == ("first", "second")
    assert execute([Group()]) == ()


# (spec) Step kinds
def test_group_rejects_a_non_step(tmp_path: Path) -> None:
    write_build(tmp_path, 'result = Group(42)\n')
    with pytest.raises(RoadieError, match="Group.*steps"):
        load(tmp_path)


# (spec) Dry run output
# (spec) Step kinds
# (spec) Command line
def test_group_plans_and_prints_steps_as_variadic_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("roadie.build.rust.CargoPackage.fingerprint", lambda self, base_dir: f"package:{self.name}")
    write_build(tmp_path, 'first = RustBinary(CargoPackage("first"))\nsecond = RustBinary(CargoPackage("second"))\nall_steps = Group(first, second)\n')
    assert plan_names(tmp_path) == ["first", "second", "all_steps"]
    run_cli(["--dry", "-f", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "all_steps = Group(first, second)  # run"
