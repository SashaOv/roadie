"""Verify command resolution and Windows batch quoting."""

import pytest
from support import posix_only

from roadie.system.command import _batch_command_line, prepare_command


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
