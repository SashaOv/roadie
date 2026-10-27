import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from forge.environment import repository_root

from wright.graph import (
    Binary,
    File,
    Result,
    Service,
    Verdict,
    WrightError,
    current_result,
)
from wright.process import (
    available_port,
    spawn_server,
    stop_dev_server,
    wait_for_dev_server,
)

_CONTRACT_PATH = "jammer/server/api.yaml"
_PLACEHOLDER_URL = "http://127.0.0.1:8811"


@dataclass(frozen=True)
class CargoPackage:
    name: str


@dataclass(frozen=True)
class DevDeploymentEnvironment:
    """The local machine: a process on a free loopback port with local storage."""


@dataclass(frozen=True)
class RunningService:
    process: subprocess.Popen[bytes]
    url: str
    storage: Path

    def stop(self) -> None:
        stop_dev_server(self.process)
        shutil.rmtree(self.storage, ignore_errors=True)


def rust_binary(package: CargoPackage) -> Binary:
    return Binary("rust_binary", {"package": package}, build_rust_binary)


def openapi(server: Binary) -> File:
    return File("openapi", {"server": server}, generate_openapi)


def deploy(binary: Binary, environment: DevDeploymentEnvironment) -> Service:
    return Service(
        "deploy",
        {"binary": binary, "environment": environment},
        start_service,
    )


def contract_fuzz(api: File, service: Service) -> Verdict:
    return Verdict("contract_fuzz", {"api": api, "service": service}, fuzz_contract)


def build_rust_binary(package: CargoPackage) -> Path:
    command = (
        "cargo",
        "build",
        "--locked",
        "-p",
        package.name,
        "--message-format=json",
    )
    print("+", shlex.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=repository_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        message = (
            f"cargo build --locked -p {package.name} "
            f"exited with status {completed.returncode}"
        )
        if detail:
            message = f"{message}: {detail[-2000:]}"
        raise RuntimeError(message)
    return executable_from_cargo_messages(completed.stdout, package.name)


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
        raise RuntimeError(
            f"cargo build --locked -p {package} produced no executable"
        )
    return Path(found)


def generate_openapi(server: Path) -> Path:
    result = current_result.get()
    if result is None:
        raise WrightError("openapi ran outside a Wright run")
    destination = _artifact_path(result, ".yaml")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = (str(server), "openapi")
    print("+", shlex.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=repository_root(),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise RuntimeError(
            detail or f"{server} openapi exited with status {completed.returncode}"
        )
    destination.write_bytes(completed.stdout)
    return destination


def start_service(binary: Path, environment: DevDeploymentEnvironment) -> RunningService:
    """Serve `binary` on a free loopback port with fresh storage."""
    # Defer the harness import until its process helpers have finished importing.
    from forge.build_script import dev_server_environment, prepare_dev_server_storage

    if not isinstance(environment, DevDeploymentEnvironment):
        raise TypeError("deploy runs on the local machine")
    port = available_port()
    storage = Path(tempfile.mkdtemp(prefix="wright-deploy-"))
    url = f"http://127.0.0.1:{port}"
    process: subprocess.Popen[bytes] | None = None
    try:
        prepare_dev_server_storage(storage)
        variables = dev_server_environment(
            database_path=storage / "db" / "jammer.db",
            storage_root=storage,
            port=port,
        )
        # Contract fuzzing checks API behavior, not the CI runner's live disk
        # capacity. Dedicated system-health tests retain threshold coverage.
        variables["VOLUME_CAPACITY_RATIO"] = "1"
        print(f"Jammer dev server: {url}", flush=True)
        process = spawn_server((str(binary), "serve"), environment=variables)
        wait_for_dev_server(process, port=port)
        return RunningService(process=process, url=url, storage=storage)
    except BaseException:
        if process is not None:
            stop_dev_server(process)
        shutil.rmtree(storage, ignore_errors=True)
        raise


def fuzz_contract(api: Path, service: RunningService) -> None:
    from forge.build_script import SCHEMATHESIS_COMMAND

    command = tuple(
        str(api) if argument == _CONTRACT_PATH
        else service.url if argument == _PLACEHOLDER_URL
        else argument
        for argument in SCHEMATHESIS_COMMAND
    )
    print("+", shlex.join(command), flush=True)
    completed = subprocess.run(command, cwd=repository_root(), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"schemathesis exited with status {completed.returncode}")


def _artifact_path(result: Result, suffix: str) -> Path:
    stem = (result.name or result.step).replace(":", "_").replace("/", "_")
    return repository_root() / "target" / "wright" / f"{stem}{suffix}"


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
