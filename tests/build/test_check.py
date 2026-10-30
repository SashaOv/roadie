import shlex
import sys
from pathlib import Path

import pytest
from support import execute

from roadie.build import Check, Files
from roadie.build import TestResults as Results
from roadie.engine.errors import StepFailed
from roadie.engine.fingerprint import fingerprint


def _check(*command: str, inputs: tuple[Files, ...] = ()) -> Check:
    step = Check(*command, inputs=inputs)
    step.name = step.variable = "lint"
    step.base_dir = Path.cwd()
    return step


# (spec) Step kinds
def test_check_passes_and_is_reused_until_an_input_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("one")
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.stderr.write('warn')\n"
        "path = Path('runs.txt')\n"
        "path.write_text(str(int(path.read_text()) + 1 if path.exists() else 1))\n"
    )
    step = _check(sys.executable, "-c", code, inputs=(Files("src"),))
    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "1"

    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "1"
    assert "reused lint" in capsys.readouterr().out

    (source / "a.py").write_text("two")
    assert execute([step], root=tmp_path) == Results()
    assert (tmp_path / "runs.txt").read_text() == "2"


# (spec) Step kinds
def test_check_failure_names_the_command_and_keeps_the_output_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code = "import sys; sys.stdout.write('y' * 2500); sys.stderr.write('TAIL'); sys.exit(1)"
    step = _check(sys.executable, "-c", code)
    output = "y" * 2500 + "TAIL"
    tail = output[-2000:]
    command = shlex.join((sys.executable, "-c", code))
    with pytest.raises(StepFailed) as error:
        execute([step], root=tmp_path)
    assert str(error.value) == f"lint failed: {command}: {tail}"
    assert "y" * 2001 not in str(error.value)


# (spec) Step kinds
def test_check_fails_when_the_command_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    step = _check("roadie-missing-command")
    with pytest.raises(StepFailed, match="roadie-missing-command") as error:
        execute([step], root=tmp_path)
    assert "No such file or directory" in str(error.value)


# (spec) Step kinds
# (spec) Files input
def test_check_inputs_change_its_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("one")
    step = _check("true", inputs=(Files("src"),))
    before = fingerprint(step)
    (source / "a.py").write_text("two")
    assert fingerprint(step) != before
