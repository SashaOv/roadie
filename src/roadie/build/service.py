"""Steps that start a long-running process."""

import subprocess
from collections.abc import Mapping
from typing import ClassVar, Protocol

from roadie.engine.errors import StepFailed
from roadie.engine.process import RunningProcess
from roadie.engine.step import Step


class Deployment(Protocol):
    @property
    def process(self) -> subprocess.Popen[bytes]: ...

    @property
    def url(self) -> str: ...

    def stop(self) -> None: ...


# (spec) Step kinds
class Service(Step[Deployment]):
    """A step that starts a process; its value is a `Deployment`.

    Tearing the step down calls `stop()`. A service is never reused and is
    deferred; its output fingerprint is its own fingerprint, so its consumers
    can be reused without it running.

    As the requested result, a service prints `<name> is up: <url> (Ctrl-C to
    stop)` and waits on its process. Ctrl-C and SIGTERM end the wait. A process
    that exits with status 0 prints `<name> exited`; any other status fails the
    step with `exited with status <n>`. A deployment without a `process` is not
    waited on. A service may be requested only once per run.
    """

    reusable: ClassVar[bool] = False
    deferred: ClassVar[bool] = True
    once_per_run: ClassVar[bool] = True

    def teardown(self, value: Deployment) -> None:
        value.stop()

    def output_fingerprint(self, fingerprint: str, value: Deployment) -> str:
        return fingerprint

    def serve(self, value: Deployment) -> None:
        name = self.name or type(self).__name__
        print(f"{name} is up: {getattr(value, 'url', '')} (Ctrl-C to stop)", flush=True)
        process = getattr(value, "process", None)
        if process is None:
            return
        try:
            status = process.wait()
        except KeyboardInterrupt:
            return
        if status == 0:
            print(f"{name} exited", flush=True)
            return
        raise StepFailed(name, f"exited with status {status}")


# (spec) Step kinds
class HttpService(Service):
    """A service whose `_start_cmd` waits for `health_path` (default `/`) to
    answer 200, unless the call names another path."""

    health_path: ClassVar[str] = "/"

    def _start_cmd(
        self,
        *args: str,
        port: int,
        environment: Mapping[str, str] | None = None,
        timeout: float = 120,
        health_path: str | None = None,
    ) -> RunningProcess:
        return super()._start_cmd(
            *args,
            port=port,
            environment=environment,
            timeout=timeout,
            health_path=self.health_path if health_path is None else health_path,
        )
