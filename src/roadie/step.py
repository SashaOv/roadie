"""Declare steps and the commands they run."""

import itertools
import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Self

from wright.config import base_directory
from wright.errors import StepFailed
from wright.process import RunningProcess
from wright.testing import TestResults

if TYPE_CHECKING:
    from wright.run import Run

_serials = itertools.count()


class Step[T](ABC):
    serial: int
    base_dir: Path
    name: str | None

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        instance = super().__new__(cls)
        instance.serial = next(_serials)
        instance.name = None
        instance.base_dir = base_directory()
        return instance

    def attributes(self) -> dict[str, object]:
        return {
            key: value
            for key, value in vars(self).items()
            if key not in {"serial", "name", "base_dir"}
        }

    def resolve(self, path: str | Path) -> Path:
        return (self.base_dir / path).resolve()

    def dependencies(self) -> Iterator["Step[Any]"]:
        return (
            value for value in self.attributes().values() if isinstance(value, Step)
        )

    @abstractmethod
    def build(self, run: "Run") -> T:
        """Build this step from its dependencies' values."""

    def teardown(self, value: T) -> None:
        pass

    def _cmd(
        self,
        *args: str,
        capture_output: bool = False,
        environment: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        print("+", shlex.join(args), flush=True)
        completed = subprocess.run(
            args,
            cwd=self.base_dir,
            env=None if environment is None else dict(environment),
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check:
            self._check_cmd(completed)
        return completed

    def _check_cmd(self, completed: subprocess.CompletedProcess[str]) -> None:
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[-2000:]
            reason = f"{shlex.join(completed.args)} exited with status {completed.returncode}"
            raise StepFailed(
                self.name or type(self).__name__,
                f"{reason}: {detail}" if detail else reason,
            )

    def _start_cmd(
        self,
        *args: str,
        port: int,
        environment: Mapping[str, str] | None = None,
        timeout: float = 120,
    ) -> RunningProcess:
        print("+", shlex.join(args), flush=True)
        return RunningProcess.start(
            args, port=port, environment=environment, timeout=timeout, cwd=self.base_dir
        )


class Binary(Step[Path]):
    pass


class File(Step[Path]):
    pass


class Deployment(Protocol):
    @property
    def process(self) -> subprocess.Popen[bytes]: ...

    @property
    def url(self) -> str: ...

    def stop(self) -> None: ...


class Service(Step[Deployment]):
    def teardown(self, value: Deployment) -> None:
        value.stop()


class Tests(Step[TestResults]):
    pass
