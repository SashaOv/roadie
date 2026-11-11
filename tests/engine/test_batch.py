"""Windows batch files started by name."""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import execute, posix_only, windows_only

from roadie.build import Service, Step
from roadie.engine.process import RunningProcess, available_port
from roadie.engine.run import Run
from roadie.system.command import batch_command_line, prepare_command


# (spec) Step helpers
def test_batch_command_line_quotes_arguments() -> None:
    line = batch_command_line(
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
        batch_command_line("tool", r"C:\tools\tool.cmd", [argument], comspec="cmd.exe")
    assert str(caught.value) == (
        f"cannot run tool: cmd.exe would read {character} in {argument}"
    )


# (spec) Step helpers
@posix_only
def test_prepare_command_is_unchanged_on_posix() -> None:
    assert prepare_command(["pnpm", "--version"], {}) == ["pnpm", "--version"]


# The batch file passes `%*` through, and Python records the arguments.
_ECHOARGS = """\
@echo off
"%ROADIE_PYTHON%" -c "import os,pathlib,sys; pathlib.Path(os.environ['OUT']).write_text(chr(10).join(sys.argv[1:]))" %*
"""

_HOLD = """\
@echo off
"%ROADIE_PYTHON%" -c "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(); exec('while True:\\n s.accept()[0].close()')" %~1
"""


# (spec) Step helpers
@windows_only
def test_cmd_starts_a_batch_file_by_name(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "echoargs.cmd").write_text(_ECHOARGS)
    out = tmp_path / "out.txt"

    class Echo(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(
                "echoargs",
                "hello world",
                "a&b",
                "(x)",
                "trail\\",
                environment={
                    "PATH": str(bindir),
                    "OUT": str(out),
                    "ROADIE_PYTHON": sys.executable,
                },
            )

    execute([Echo()])
    assert out.read_text().splitlines() == [
        "hello world",
        "a&b",
        "(x)",
        "trail\\",
    ]


# (spec) Step helpers
@windows_only
def test_a_cmd_service_starts_and_stops(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "holdport.cmd").write_text(_HOLD)
    started: list[RunningProcess] = []

    class Server(Service):
        def build(self, run: Run) -> RunningProcess:
            port = available_port()
            server = self._start_cmd(
                "holdport",
                str(port),
                port=port,
                timeout=10,
                environment={"PATH": str(bindir), "ROADIE_PYTHON": sys.executable},
            )
            started.append(server)
            return server

    @dataclass(eq=False)
    class After(Step[None]):
        server: Service

        def build(self, run: Run) -> None:
            assert run[self.server].process.poll() is None

    server = Server()
    try:
        execute([server, After(server)])
        assert started[0].process.poll() is not None
    finally:
        for child in started:
            child.stop()
