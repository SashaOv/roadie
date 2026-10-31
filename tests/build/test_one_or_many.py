import subprocess
import sys
from pathlib import Path

import pytest
from support import Value, plan_names, write_build

from roadie.build import (
    Check,
    Files,
    Group,
    PyProject,
    Pytest,
    PythonEnv,
    Release,
    RoadieError,
)
from roadie.engine.fingerprint import fingerprint
from roadie.engine.graph import load


# (spec) Step kinds
def test_requires_and_inputs_take_one_value_or_many() -> None:
    sources = Files("src")
    environment = PythonEnv()
    gate = Check("true", inputs=sources)
    suite = Pytest(inputs=(sources, environment))
    checks = Group(gate, suite)

    assert gate.inputs == (sources,)
    assert suite.inputs == (sources, environment)
    assert Release(PyProject("pyproject.toml"), requires=checks).requires == (checks,)
    assert Release(PyProject("pyproject.toml"), requires=(gate, suite)).requires == (gate, suite)


# (spec) Step kinds
def test_requires_one_group_plans_the_group(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        'gate = Check("true")\n'
        "checks = Group(gate)\n"
        'release = Release(PyProject("pyproject.toml"), requires=checks)\n',
    )

    assert plan_names(tmp_path, "release") == ["gate", "checks", "release"]


# (spec) Step kinds
def test_a_check_takes_a_step_in_inputs(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "item = Group()\n"
        "check = Check('true', inputs=(Files('src'), item))\n"
        "suite = Pytest(inputs=item)\n",
    )

    assert plan_names(tmp_path, "check") == ["item", "check"]
    assert plan_names(tmp_path, "suite") == ["item", "suite"]
    source = Value("built")
    gate = Check("true", inputs=source)
    original = fingerprint(gate, outputs={source: "a" * 64})
    assert fingerprint(gate, outputs={source: "b" * 64}) != original


# (spec) Step kinds
@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("Check('true', inputs=1)", "Check inputs takes inputs or steps: got int"),
        ("Check('true', inputs=(Files('src'), 1))", "Check inputs takes inputs or steps: got int"),
        ("Pytest(inputs=1)", "Pytest inputs takes inputs or steps: got int"),
        ('Release(PyProject("pyproject.toml"), requires=1)', "Release requires takes steps: got int"),
        (
            'Release(PyProject("pyproject.toml"), requires=(Check("true"), 1))',
            "Release requires takes steps: got int",
        ),
    ],
)
def test_a_value_of_the_wrong_kind_names_the_parameter(call: str, message: str) -> None:
    with pytest.raises(RoadieError, match=f"^{message}$"):
        eval(call)


# (spec) Settings
def test_config_env_takes_one_path_or_many(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        'config(env="one.env")\n'
        'one = Check("true")\n'
        'config(env=["two.env", "three.env"])\n'
        'many = Check("true")\n',
    )
    graph = load(tmp_path)
    assert graph.results["one"].env_files == ((tmp_path / "one.env").resolve(),)
    assert graph.results["many"].env_files == (
        (tmp_path / "two.env").resolve(),
        (tmp_path / "three.env").resolve(),
    )


# (spec) Settings
@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("config(env=1)\n", "config env takes paths: got int"),
        ('config(env=["one.env", 1])\n', "config env takes paths: got int"),
    ],
)
def test_config_env_rejects_a_non_path(source: str, message: str, tmp_path: Path) -> None:
    write_build(tmp_path, source + 'step = Check("true")\n')
    with pytest.raises(RoadieError, match=f"^{message}$"):
        load(tmp_path)


# (spec) Step kinds
def test_one_step_or_a_tuple_of_different_steps_type_checks(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from roadie.build import Check, Files, Group, PyProject, Pytest, PythonEnv, Release\n"
        "\n"
        "sources = Files('src')\n"
        "environment = PythonEnv()\n"
        "ruff = Check('ruff', inputs=sources)\n"
        "mypy = Check('mypy', inputs=(sources, environment))\n"
        "pytest = Pytest(inputs=(sources, environment))\n"
        "checks = Group(ruff, mypy, pytest)\n"
        "Release(PyProject('pyproject.toml'), requires=checks)\n"
        "Release(PyProject('pyproject.toml'), requires=(ruff, mypy, pytest))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", str(sample)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
