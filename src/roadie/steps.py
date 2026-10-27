import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wright.process import RunningProcess, available_port
from wright.run import Run
from wright.step import Binary, Deployment, File, Service, Verdict


@dataclass(frozen=True)
class CargoPackage:
    name: str


@dataclass(frozen=True)
class DevDeploymentEnvironment:
    """The local machine."""


@dataclass(frozen=True)
class RunningService(RunningProcess):
    storage: Path

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            shutil.rmtree(self.storage)


@dataclass(eq=False)
class RustBinary(Binary):
    package: CargoPackage

    def build(self, run: Run) -> Path:
        completed = self._cmd(
            "cargo",
            "build",
            "--locked",
            "-p",
            self.package.name,
            "--message-format=json",
            capture_output=True,
        )
        return executable_from_cargo_messages(completed.stdout, self.package.name)


@dataclass(eq=False)
class OpenApi(File):
    binary: Binary

    def build(self, run: Run) -> Path:
        destination = run.artifact(self, ".yaml")
        completed = self._cmd(str(run[self.binary]), "openapi", capture_output=True)
        destination.write_text(completed.stdout)
        return destination


@dataclass(eq=False)
class JammerDevServer(Service):
    binary: Binary
    check_disk_capacity: bool = True

    def build(self, run: Run) -> Deployment:
        # Defer this import while the harness still owns Jammer's environment.
        from forge.build_script import (
            dev_server_environment,
            prepare_dev_server_storage,
        )

        port = available_port()
        storage = Path(tempfile.mkdtemp(prefix="wright-deploy-"))
        try:
            prepare_dev_server_storage(storage)
            variables = dev_server_environment(
                database_path=storage / "db" / "jammer.db",
                storage_root=storage,
                port=port,
            )
            if not self.check_disk_capacity:
                variables["VOLUME_CAPACITY_RATIO"] = "1"
            server = self._start_cmd(
                str(run[self.binary]), "serve", environment=variables, port=port
            )
            return RunningService(server.process, server.url, storage)
        except BaseException:
            shutil.rmtree(storage)
            raise


@dataclass(eq=False)
class ContractFuzz(Verdict):
    api: File
    server: Service

    def build(self, run: Run) -> None:
        self._cmd(
            "uv",
            "run",
            "schemathesis",
            "run",
            str(run[self.api]),
            "--url",
            run[self.server].url,
            "--phases",
            "examples,coverage,fuzzing",
            "--checks",
            "all",
            "--exclude-checks",
            "positive_data_acceptance",
            "--exclude-operation-id",
            "getSession",
            "--exclude-operation-id",
            "controlHeapProfiling",
            "--exclude-operation-id",
            "dumpHeapProfile",
            "--exclude-path",
            "/inbound",
            "--exclude-path",
            "/unsubscribe/{token}",
            "--header",
            "Authorization: Bearer dev-ops-token",
        )


def executable_from_cargo_messages(output: str, package: str) -> Path:
    """Return the bin executable cargo reported for `package`."""
    found: str | None = None
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            continue
        if parsed.get("reason") != "compiler-artifact":
            continue
        if not _is_package(parsed.get("package_id"), package):
            continue
        target = parsed.get("target")
        if not isinstance(target, dict):
            continue
        kinds = target.get("kind")
        if not isinstance(kinds, list) or "bin" not in kinds:
            continue
        executable = parsed.get("executable")
        if not isinstance(executable, str) or not executable:
            continue
        if found is not None and executable != found:
            raise RuntimeError(
                f"cargo build --locked -p {package} produced multiple executables"
            )
        found = executable
    if found is None:
        raise RuntimeError(f"cargo build --locked -p {package} produced no executable")
    return Path(found)


def _is_package(package_id: object, package: str) -> bool:
    """True for cargo's `#name@version` id and its `/name#version` id."""
    if not isinstance(package_id, str):
        return False
    if f"#{package}@" in package_id:
        return True
    marker = f"/{package}#"
    index = package_id.rfind(marker)
    if index < 0:
        return False
    rest = package_id[index + len(marker) :]
    return bool(rest) and rest[0].isdigit()
