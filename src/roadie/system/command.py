"""Find a program on Windows and start a batch file through cmd.exe."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence

_REFUSED = frozenset('"\r\n%')


class CannotRun(OSError):
    """The program was refused before it started.

    The message is the step reason `cannot run <program>: ...`.
    """


def prepare_command(
    args: Sequence[str], env: Mapping[str, str] | None = None
) -> list[str] | str:
    """Return the argv, or a raw command line, to pass to `Popen`.

    On Windows, a program named without a directory is found with
    `shutil.which` on `PATH` from `env` (the process `PATH` when `env` is
    omitted), trying each `PATHEXT` extension, the first match winning. A
    name not found is returned unchanged. A `.cmd` or `.bat` file runs as
    `cmd.exe /d /s /c "<file> <arguments>"`, with `cmd.exe` from `ComSpec`.
    POSIX is unchanged.
    """
    command = list(args)
    if sys.platform != "win32" or not command or os.path.dirname(command[0]):
        return command
    found = shutil.which(command[0], path=_search_path(env))
    if found is None:
        return command
    if not found.lower().endswith((".cmd", ".bat")):
        return [found, *command[1:]]
    return batch_command_line(command[0], found, command[1:], comspec=_comspec(env))


def cannot_run_message(program: str, error: OSError) -> str:
    """Return `cannot run <program>: <reason>`, the reason without its filename."""
    if isinstance(error, CannotRun):
        return str(error)
    reason = str(error)
    if error.filename is not None:
        reason = reason.removesuffix(f": {error.filename!r}")
    return f"cannot run {program}: {reason}"


def batch_command_line(
    program: str,
    script: str,
    arguments: Sequence[str],
    *,
    comspec: str,
) -> str:
    """Return `cmd.exe /d /s /c` for a batch file.

    Each argument is in double quotes, and backslashes before the closing
    quote are doubled. An argument holding `"`, `%` or a line break raises
    `CannotRun` with `cannot run <program>: cmd.exe would read <character>
    in <argument>`.
    """
    quoted = " ".join(_quote(program, part) for part in (script, *arguments))
    return f'{comspec} /d /s /c "{quoted}"'


def _quote(program: str, argument: str) -> str:
    for character in argument:
        if character in _REFUSED:
            raise CannotRun(
                f"cannot run {program}: cmd.exe would read {character} in {argument}"
            )
    end = len(argument)
    while end > 0 and argument[end - 1] == "\\":
        end -= 1
    trailing = len(argument) - end
    return '"' + argument[:end] + ("\\" * (trailing * 2)) + '"'


def _search_path(env: Mapping[str, str] | None) -> str | None:
    if env is None:
        return None
    value = _mapped(env, "PATH")
    return "" if value is None else value


def _comspec(env: Mapping[str, str] | None) -> str:
    value = None if env is None else _mapped(env, "COMSPEC")
    if value:
        return value
    return os.environ.get("ComSpec") or "cmd.exe"


def _mapped(env: Mapping[str, str], name: str) -> str | None:
    found = None
    for key, value in env.items():
        if key.upper() == name:
            found = value
    return found
