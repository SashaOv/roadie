from pathlib import Path

from roadie.build import Check, Group, Pytest, Release
from roadie.engine.graph import load

ROOT = Path(__file__).resolve().parents[2]


# (spec) Results
# (spec) Step kinds
def test_roadie_checks_itself_and_releases_only_when_asked() -> None:
    graph = load(ROOT)
    assert graph.result().name == "checks"
    assert [step.name for step in graph.plan()] == ["ruff", "mypy", "pytest", "checks"]
    assert [step.name for step in graph.plan("release")] == [
        "ruff",
        "mypy",
        "pytest",
        "checks",
        "release",
    ]

    ruff, mypy, pytest_step, checks = graph.plan()
    assert isinstance(ruff, Check)
    assert ruff.command == ("uv", "run", "ruff", "check")
    assert isinstance(mypy, Check)
    assert mypy.command == ("uv", "run", "mypy", "src", "build.py")
    assert isinstance(pytest_step, Pytest)
    assert pytest_step.args == ()
    assert isinstance(checks, Group)
    release = graph.plan("release")[-1]
    assert isinstance(release, Release)
    assert release.requires == (checks,)
