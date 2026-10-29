from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, isolate_repo

from roadie.build import File
from roadie.build import TestFailure as Failure
from roadie.build import TestResults as Results
from roadie.build import Tests as Checks
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run


# (spec) Step kinds
@pytest.mark.parametrize("failed", [False, True])
def test_tests_step_reports_failures_and_messages(failed: bool, capsys: pytest.CaptureFixture[str]) -> None:
    class Check(Checks):
        def build(self, run: Run) -> Results:
            return Results(
                failures=(Failure("GET /health", "Received 200; documented 418"),) if failed else (),
                messages=("checked API",),
            )

    check = Check()
    check.name = "fuzzed"
    if failed:
        with pytest.raises(StepFailed, match="fuzzed failed:.*GET /health.*Received 200; documented 418"):
            execute([check])
    else:
        assert execute([check]) == Results(messages=("checked API",))
    assert "checked API" in capsys.readouterr().out


# (spec) Step hooks
def test_tests_consumer_is_reused_when_the_replayed_log_would_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    monkeypatch.chdir(root)
    built: list[str] = []

    @dataclass(eq=False)
    class Logged(Checks):
        def build(self, run: Run) -> Results:
            built.append("logged")
            return Results((), (f"log-{len(built)}",))

    @dataclass(eq=False)
    class Downstream(File):
        source: Checks

        def build(self, run: Run) -> Path:
            built.append("down")
            path = run.artifact(self, ".out")
            path.write_text("ok")
            return path

    logged = Logged()
    down = Downstream(logged)
    logged.name = logged.variable = "logged"
    down.name = down.variable = "down"
    execute([logged, down])
    built.clear()
    execute([logged, down])
    output = capsys.readouterr().out
    assert built == []
    assert "reused down" in output
    assert "log-1" in output
