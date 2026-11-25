"""Verify command resolution and Windows batch quoting."""

import sys
from pathlib import Path

import pytest
from support import posix_only

from roadie.system import command, run_query
from roadie.system.command import CannotRun, _batch_command_line, prepare_command


# (spec) Step helpers
def test_batch_command_line_quotes_arguments() -> None:
    line = _batch_command_line(
        "tool",
        r"C:\tools\tool.cmd",
        ["hello world", "a&b", "(x)", "trail\\"],
        comspec="cmd.exe",
    )
    assert line == (
        'cmd.exe /d /s /c '
        '""C:\\tools\\tool.cmd" "hello world" "a&b" "(x)" "trail\\\\""'
    )


# (spec) Step helpers
@pytest.mark.parametrize("character", ['"', "%", "\n", "\r"])
def test_batch_command_line_refuses_a_character(character: str) -> None:
    argument = f"pre{character}post"
    with pytest.raises(OSError) as caught:
        _batch_command_line("tool", r"C:\tools\tool.cmd", [argument], comspec="cmd.exe")
    assert str(caught.value) == (
        f"cannot run tool: cmd.exe would read {character} in {argument}"
    )


# (spec) Step helpers
@posix_only
def test_prepare_command_is_unchanged_on_posix() -> None:
    assert prepare_command(["pnpm", "--version"], {}) == ["pnpm", "--version"]


def _script(directory: Path, body: str) -> Path:
    script = directory / "tool.py"
    script.write_text(body)
    return script


def test_run_query_returns_standard_output(tmp_path: Path) -> None:
    script = _script(tmp_path, "import sys; sys.stdout.write('hello\\n')\n")
    assert run_query(tmp_path, sys.executable, str(script)) == "hello\n"


def test_run_query_keeps_undecodable_bytes(tmp_path: Path) -> None:
    script = _script(
        tmp_path,
        "import sys; sys.stdout.buffer.write(b'\\xff\\n')\n",
    )
    output = run_query(tmp_path, sys.executable, str(script))
    assert output.encode("utf-8", "surrogateescape") == b"\xff\n"


def test_run_query_with_a_missing_program(tmp_path: Path) -> None:
    with pytest.raises(CannotRun) as caught:
        run_query(tmp_path, "roadie-no-such-program")
    assert str(caught.value).startswith("cannot run roadie-no-such-program: ")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("sys.stderr.write('broken\\n'); sys.stdout.write('other')", "broken"),
        ("sys.stdout.write('from out\\n')", "from out"),
        ("pass", f"{sys.executable} exited with status 3"),
    ],
)
def test_run_query_with_an_unsuccessful_exit(tmp_path: Path, body: str, message: str) -> None:
    script = _script(tmp_path, f"import sys; {body}; sys.exit(3)\n")
    with pytest.raises(Exception) as caught:
        run_query(tmp_path, sys.executable, str(script))
    assert not isinstance(caught.value, OSError)
    assert type(caught.value) is command.ProgramFailed
    assert str(caught.value) == message
