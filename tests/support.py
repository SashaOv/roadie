"""Helpers shared by Roadie's tests."""

import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from roadie import cli
from roadie.build import File, Service, Step
from roadie.engine.graph import load
from roadie.engine.run import Run
from roadie.engine.run import execute as _execute

FIXTURES = Path(__file__).parent / "fixtures"
_forced_root: Path | None = None
_sandbox_root: Path | None = None


def execute(steps, **kwargs):
    """Run `steps`.

    Without `root`, records go to a sandbox outside this checkout. The suite
    runs under `roadie`, which already holds the checkout's lock.
    """
    root = kwargs.pop("root", None)
    if root is None:
        root = _forced_root if _forced_root is not None else _sandbox()
    return _execute(steps, root=root, **kwargs)


def _sandbox() -> Path:
    global _sandbox_root
    if _sandbox_root is None:
        _sandbox_root = Path(tempfile.mkdtemp(prefix="roadie-test-root-"))
    return _sandbox_root


def isolate_repo(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Run `execute` with `root` as the project root."""
    monkeypatch.setattr(f"{__name__}._forced_root", root)


def write_build(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from roadie.build import *\n\n{source}")
    return directory


def plan_names(directory: Path, result: str | None = None) -> list[str | None]:
    return [step.name for step in load(directory).plan(result)]


def run_cli(arguments: list[str]) -> None:
    command, bound, _ = cli.app.parse_args(arguments)
    command(*bound.args, **bound.kwargs)


def git_init(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)


@dataclass(eq=False)
class Value(Step[str]):
    value: str

    def build(self, run: Run) -> str:
        return self.value


@dataclass(eq=False)
class TextFile(File):
    text: str

    def build(self, run: Run) -> Path:
        artifact = run.artifact(self, ".txt")
        artifact.write_text(self.text)
        return artifact


EMITTER = """
from dataclasses import dataclass
from pathlib import Path
from roadie.engine.run import Run

@dataclass(eq=False)
class Emitter(File):
    text: str = "v1"
    def build(self, run: Run) -> Path:
        path = run.artifact(self, ".txt")
        path.write_text(self.text)
        (self.build_dir / "ran.txt").write_text("ran")
        return path

api = Emitter()
"""


class _Exited:
    def wait(self) -> int:
        return 0

    def poll(self) -> int:
        return 0


class Stopped:
    """A deployment whose process has already exited."""

    def __init__(self) -> None:
        self.url = "http://127.0.0.1:9"
        self.process = _Exited()

    def stop(self) -> None:
        return None


class FakeDeployment:
    """A deployment whose process exits with `status`, or waits to be interrupted."""

    def __init__(self, status: int | None = 0, interrupt: bool = False) -> None:
        self.url = "http://127.0.0.1:9"
        self.process = self
        self.status = status
        self.interrupt = interrupt
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.stopped = False

    def wait(self) -> int:
        if self.interrupt:
            self.waiting.set()
            if not self.release.wait(5):
                raise AssertionError("the service was not interrupted")
            raise KeyboardInterrupt
        self.waiting.set()
        assert self.status is not None
        return self.status

    def stop(self) -> None:
        self.stopped = True


def fake_server(deployment: object) -> Service:
    class Server(Service):
        def build(self, run: Run) -> object:
            return deployment

    server = Server()
    server.name = "server"
    return server
