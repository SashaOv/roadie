from pathlib import Path

import pytest
from support import execute

from roadie.build import Files, Pytest
from roadie.build import TestResults as Results
from roadie.engine.errors import StepFailed


def _suite(*args: str, inputs: tuple[Files, ...] = ()) -> Pytest:
    step = Pytest(*args, inputs=inputs)
    step.name = step.variable = "suite"
    step.base_dir = Path.cwd()
    return step


# (spec) Step kinds
def test_pytest_passes_and_is_reused_until_an_input_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "test_ok.py"
    source.write_text(
        "from pathlib import Path\n"
        "def test_ok():\n"
        "    path = Path('runs.txt')\n"
        "    previous = int(path.read_text()) if path.exists() else 0\n"
        "    path.write_text(str(previous + 1))\n"
    )
    step = _suite(str(source), inputs=(Files(source),))
    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "1"
    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "1"
    assert "reused suite" in capsys.readouterr().out

    source.write_text(source.read_text() + "# changed\n")
    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "2"


# (spec) Step kinds
def test_pytest_reads_failures_and_errors_from_the_junit_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n"
        "    assert True\n"
        "def test_bad():\n"
        "    assert 1 == 2\n"
        "def test_boom():\n"
        '    raise RuntimeError("exploded")\n'
    )
    (tmp_path / "test_error.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def broken():\n"
        '    raise RuntimeError("fixture broke")\n'
        "def test_uses(broken):\n"
        "    pass\n"
    )
    with pytest.raises(StepFailed) as failed:
        execute([_suite(str(tmp_path / "test_sample.py"))], root=tmp_path)
    assert str(failed.value) == (
        "suite failed: test_sample::test_bad: assert 1 == 2\n"
        "test_sample::test_boom: RuntimeError: exploded"
    )
    assert str(tmp_path) not in str(failed.value)

    with pytest.raises(StepFailed) as errored:
        execute([_suite(str(tmp_path / "test_error.py"))], root=tmp_path)
    assert str(errored.value) == (
        'suite failed: test_error::test_uses: failed on setup with "RuntimeError: fixture broke"'
    )
    assert str(tmp_path) not in str(errored.value)


# (spec) Step kinds
def test_pytest_rejects_usage_errors_and_collecting_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(StepFailed) as none:
        execute([_suite(str(empty))], root=tmp_path)
    assert str(none.value) == "suite failed: pytest exited with status 5"

    with pytest.raises(StepFailed) as usage:
        execute([_suite("--not-a-real-option")], root=tmp_path)
    assert "pytest exited with status 4: " in str(usage.value)
    assert "unrecognized arguments: --not-a-real-option" in str(usage.value)


# (spec) Step kinds
def test_pytest_other_status_keeps_the_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "conftest.py").write_text(
        "import sys\n"
        "def pytest_configure(config):\n"
        "    sys.stderr.write('z' * 2500 + 'END')\n"
        "    raise SystemExit(3)\n"
    )
    (tmp_path / "test_one.py").write_text("def test_one():\n    assert True\n")
    with pytest.raises(StepFailed) as error:
        execute([_suite(str(tmp_path / "test_one.py"))], root=tmp_path)
    prefix = "suite failed: pytest exited with status 3: "
    assert str(error.value).startswith(prefix)
    tail = str(error.value)[len(prefix) :]
    # A step failure strips the reason, so stderr's trailing newline is gone.
    assert len(tail) == 1999
    assert "END" in tail
    assert tail.endswith("caught unexpected SystemExit!")
    assert "z" * 2001 not in str(error.value)
