"""Windows batch files started by name."""

import sys
from dataclasses import dataclass
from pathlib import Path

from support import execute, windows_only

from roadie.build import Attribute, Service, Step
from roadie.engine.process import RunningProcess, available_port
from roadie.engine.run import Run

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
        server: Attribute[Service]

        def build(self, run: Run) -> None:
            assert run[self.server].process.poll() is None

    server = Server()
    try:
        execute([server, After(server)])
        assert started[0].process.poll() is not None
    finally:
        for child in started:
            child.stop()
