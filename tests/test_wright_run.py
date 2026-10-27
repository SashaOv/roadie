import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from forge.build_script import dev_server_environment
from forge.environment import repository_root
from wright import cli, steps
from wright.steps import CargoPackage, DevDeploymentEnvironment, RunningService

FIXTURES = Path(__file__).parent / "fixtures"
JAMMER = repository_root() / "jammer"


def write_build(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.py").write_text(f"from wright import *\n\n{source}")
    return directory


def run_cli(arguments: list[str]) -> None:
    command, bound, _ = cli.app.parse_args(arguments)
    command(*bound.args, **bound.kwargs)


def listening_server(path: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import socket\n"
        "import time\n"
        "port = int(os.environ['PORT'])\n"
        "sock = socket.socket()\n"
        "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "sock.bind(('127.0.0.1', port))\n"
        "sock.listen(1)\n"
        "open(os.environ['STORAGE_ROOT'] + '/listening', 'w').close()\n"
        "time.sleep(120)\n"
    )
    path.chmod(0o755)


def contract_printer(path: Path, text: str) -> None:
    path.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({text!r})\n")
    path.chmod(0o755)


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.parametrize(
    ("package", "fixture", "executable"),
    [
        (
            "jammer_server",
            "cargo-build-jammer-server.jsonl",
            "/tmp/wright-cargo-ws-o7sjppt2/target/debug/jammer-server",
        ),
        (
            "sample",
            "cargo-build-sample.jsonl",
            "/tmp/wright-cargo-sample-48b05n8f/sample/target/debug/sample",
        ),
    ],
)
def test_rust_binary_picks_the_executable_from_cargo_json(
    package: str,
    fixture: str,
    executable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captured from cargo 1.96.1 `cargo build --locked -p <name> --message-format=json`.

    The jammer_server sample is a workspace package whose bin is named
    jammer-server, as in jammer/server/Cargo.toml. The sample package uses the
    directory-named package id. Libraries and build scripts are in both logs.
    """
    sample = (FIXTURES / fixture).read_text()
    seen: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> Completed:
        seen["command"] = command
        seen["cwd"] = kwargs.get("cwd")
        return Completed(0, stdout=sample)

    monkeypatch.setattr("wright.steps.subprocess.run", fake_run)
    from wright.steps import build_rust_binary

    assert build_rust_binary(CargoPackage(package)) == Path(executable)
    assert seen["command"] == (
        "cargo",
        "build",
        "--locked",
        "-p",
        package,
        "--message-format=json",
    )
    assert seen["cwd"] == repository_root()


def test_rust_binary_does_not_invent_an_executable_from_other_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = (FIXTURES / "cargo-build-jammer-server.jsonl").read_text()
    without_bin = "\n".join(
        line for line in sample.splitlines() if '"kind":["bin"]' not in line
    )

    def fake_run(command: tuple[str, ...], **kwargs: object) -> Completed:
        return Completed(0, stdout=without_bin)

    monkeypatch.setattr("wright.steps.subprocess.run", fake_run)
    from wright.steps import build_rust_binary

    with pytest.raises(RuntimeError, match="jammer_server produced no executable"):
        build_rust_binary(CargoPackage("jammer_server"))


def test_steps_run_in_plan_order_with_earlier_results_as_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = Path("/usr/bin/true")
    contract = Path("/tmp/contract.yaml")
    calls: list[object] = []

    class Handle:
        def stop(self) -> None:
            calls.append("stop")

    handle = Handle()
    monkeypatch.setattr(
        "wright.steps.build_rust_binary",
        lambda package: calls.append(("rust_binary", package)) or binary,
    )
    monkeypatch.setattr(
        "wright.steps.generate_openapi",
        lambda server: calls.append(("openapi", server)) or contract,
    )
    monkeypatch.setattr(
        "wright.steps.start_service",
        lambda binary, environment: calls.append(("deploy", binary, environment))
        or handle,
    )
    monkeypatch.setattr(
        "wright.steps.fuzz_contract",
        lambda api, service: calls.append(("contract_fuzz", api, service)),
    )
    write_build(
        tmp_path,
        'server = rust_binary(CargoPackage("jammer_server"))\n'
        "api = openapi(server)\n"
        "deployed = deploy(server, DevDeploymentEnvironment())\n"
        "fuzzed = contract_fuzz(api, deployed)\n",
    )

    run_cli([str(tmp_path), "fuzzed"])

    assert calls == [
        ("rust_binary", CargoPackage("jammer_server")),
        ("openapi", binary),
        ("deploy", binary, DevDeploymentEnvironment()),
        ("contract_fuzz", contract, handle),
        "stop",
    ]


def test_api_deploys_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "wright.steps.build_rust_binary",
        lambda package: calls.append("rust_binary") or Path("/usr/bin/true"),
    )
    monkeypatch.setattr(
        "wright.steps.generate_openapi",
        lambda server: calls.append("openapi") or Path("/tmp/contract.yaml"),
    )
    monkeypatch.setattr(
        "wright.steps.start_service",
        lambda binary, environment: calls.append("deploy"),
    )
    monkeypatch.setattr(
        "wright.steps.fuzz_contract",
        lambda api, service: calls.append("contract_fuzz"),
    )

    run_cli([str(JAMMER), "api"])

    assert calls == ["rust_binary", "openapi"]


def test_a_failing_step_is_named_and_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "wright.steps.build_rust_binary",
        lambda package: calls.append("rust_binary") or Path("/usr/bin/true"),
    )

    def fail_openapi(server: Path) -> Path:
        calls.append("openapi")
        raise RuntimeError("no contract")

    monkeypatch.setattr("wright.steps.generate_openapi", fail_openapi)
    monkeypatch.setattr(
        "wright.steps.start_service",
        lambda binary, environment: calls.append("deploy"),
    )
    write_build(
        tmp_path,
        'server = rust_binary(CargoPackage("jammer_server"))\n'
        "api = openapi(server)\n"
        "fuzzed = contract_fuzz(api, deploy(server, DevDeploymentEnvironment()))\n",
    )

    with pytest.raises(SystemExit, match="^api failed: no contract$") as raised:
        run_cli([str(tmp_path)])

    assert raised.value.code == "api failed: no contract"
    assert calls == ["rust_binary", "openapi"]


def test_a_failing_step_stays_named_when_stopping_the_service_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Handle:
        def stop(self) -> None:
            raise RuntimeError("stop broke")

    monkeypatch.setattr(
        "wright.steps.build_rust_binary", lambda package: Path("/usr/bin/true")
    )
    monkeypatch.setattr(
        "wright.steps.generate_openapi", lambda server: Path("/tmp/contract.yaml")
    )
    monkeypatch.setattr(
        "wright.steps.start_service", lambda binary, environment: Handle()
    )

    def fail(api: Path, service: object) -> None:
        raise RuntimeError("rejected")

    monkeypatch.setattr("wright.steps.fuzz_contract", fail)
    write_build(
        tmp_path,
        'server = rust_binary(CargoPackage("jammer_server"))\n'
        "api = openapi(server)\n"
        "fuzzed = contract_fuzz(api, deploy(server, DevDeploymentEnvironment()))\n",
    )

    with pytest.raises(SystemExit, match="^fuzzed failed: rejected$"):
        run_cli([str(tmp_path), "fuzzed"])


def test_openapi_writes_the_binary_stdout_under_the_result_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "jammer-server"
    contract_printer(binary, "openapi: 1\n")
    monkeypatch.setattr("wright.steps.build_rust_binary", lambda package: binary)
    monkeypatch.setattr("wright.steps.repository_root", lambda: tmp_path)
    write_build(
        tmp_path / "proj",
        'server = rust_binary(CargoPackage("jammer_server"))\napi = openapi(server)\n',
    )

    run_cli([str(tmp_path / "proj"), "api"])

    assert (tmp_path / "target" / "wright" / "api.yaml").read_text() == "openapi: 1\n"


def test_deploy_serves_on_a_free_port_with_fresh_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setattr(
        "wright.steps.tempfile.mkdtemp", lambda prefix: storage.mkdir() or str(storage)
    )
    monkeypatch.setattr("wright.steps.available_port", lambda: 43210)
    captured: dict[str, object] = {}

    class Exited:
        pid = 0

        def poll(self) -> int:
            return 0

    def spawn(command: tuple[str, ...], **kwargs: object) -> Exited:
        captured["command"] = command
        captured["environment"] = kwargs["environment"]
        return Exited()

    def wait(process: Exited, *, port: int, timeout: float = 120) -> None:
        captured["port"] = port

    monkeypatch.setattr("wright.steps.spawn_server", spawn)
    monkeypatch.setattr("wright.steps.wait_for_dev_server", wait)
    from wright.steps import start_service

    service = start_service(Path("/bin/jammer-server"), DevDeploymentEnvironment())
    try:
        assert captured["command"] == ("/bin/jammer-server", "serve")
        assert captured["port"] == 43210
        expected = dev_server_environment(
            database_path=storage / "db" / "jammer.db",
            storage_root=storage,
            port=43210,
        )
        expected["VOLUME_CAPACITY_RATIO"] = "1"
        assert captured["environment"] == expected
        assert (storage / "db").is_dir()
        assert service.url == "http://127.0.0.1:43210"
    finally:
        service.stop()

    assert not storage.exists()


def test_deploy_reports_a_server_that_exits_before_it_listens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setattr(
        "wright.steps.tempfile.mkdtemp", lambda prefix: storage.mkdir() or str(storage)
    )
    binary = tmp_path / "exits"
    contract_printer(binary, "")
    monkeypatch.setattr("wright.steps.build_rust_binary", lambda package: binary)
    write_build(
        tmp_path / "proj",
        'server = rust_binary(CargoPackage("jammer_server"))\n'
        "deployed = deploy(server, DevDeploymentEnvironment())\n",
    )

    with pytest.raises(
        SystemExit,
        match=(
            r"^deployed failed: Jammer exited with status 0 before becoming ready$"
        ),
    ):
        run_cli([str(tmp_path / "proj"), "deployed"])

    assert not storage.exists()


@pytest.mark.parametrize("ending", ["success", "failure", "interrupt"])
def test_service_is_stopped_and_its_storage_removed(
    ending: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if ending == "interrupt" and os.name != "posix":
        pytest.skip("signals are POSIX-only")
    binary = tmp_path / "jammer-server"
    listening_server(binary)
    started: list[RunningService] = []
    original_start = steps.start_service

    def recording(binary: Path, environment: DevDeploymentEnvironment) -> RunningService:
        service = original_start(binary, environment)
        started.append(service)
        return service

    entered = threading.Event()
    monkeypatch.setattr("wright.steps.build_rust_binary", lambda package: binary)
    monkeypatch.setattr(
        "wright.steps.generate_openapi", lambda server: Path("/tmp/contract.yaml")
    )
    monkeypatch.setattr("wright.steps.start_service", recording)

    def succeed(api: Path, service: RunningService) -> None:
        return None

    def fail(api: Path, service: RunningService) -> None:
        raise RuntimeError("rejected")

    def block(api: Path, service: RunningService) -> None:
        entered.set()
        signal.pause()

    endings = {"success": succeed, "failure": fail, "interrupt": block}
    monkeypatch.setattr("wright.steps.fuzz_contract", endings[ending])
    write_build(
        tmp_path / "proj",
        'server = rust_binary(CargoPackage("jammer_server"))\n'
        "api = openapi(server)\n"
        "deployed = deploy(server, DevDeploymentEnvironment())\n"
        "fuzzed = contract_fuzz(api, deployed)\n",
    )

    def signal_when_ready() -> None:
        entered.wait(timeout=10)
        os.kill(os.getpid(), signal.SIGTERM)

    if ending == "interrupt":
        threading.Thread(target=signal_when_ready, daemon=True).start()

    try:
        if ending == "success":
            run_cli([str(tmp_path / "proj"), "fuzzed"])
        elif ending == "failure":
            with pytest.raises(SystemExit, match="^fuzzed failed: rejected$"):
                run_cli([str(tmp_path / "proj"), "fuzzed"])
        else:
            with pytest.raises(KeyboardInterrupt):
                run_cli([str(tmp_path / "proj"), "fuzzed"])
        assert len(started) == 1
        assert started[0].process.poll() is not None
        assert not started[0].storage.exists()
    finally:
        for service in started:
            if service.process.poll() is None or service.storage.exists():
                service.stop()


def test_contract_fuzz_uses_the_generated_contract_and_the_service_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> Completed:
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return Completed(0)

    monkeypatch.setattr("wright.steps.subprocess.run", fake_run)
    from wright.steps import fuzz_contract

    service = RunningService(
        process=subprocess.Popen([sys.executable, "-c", "pass"]),
        url="http://127.0.0.1:43210",
        storage=Path("/tmp/wright-storage"),
    )
    try:
        fuzz_contract(Path("/tmp/wright/api.yaml"), service)
    finally:
        service.process.wait(timeout=5)

    command = captured["command"]
    assert isinstance(command, tuple)
    assert command[:4] == ("uv", "run", "schemathesis", "run")
    assert "/tmp/wright/api.yaml" in command
    assert "http://127.0.0.1:43210" in command
    assert "jammer/server/api.yaml" not in command
    assert "http://127.0.0.1:8811" not in command
    assert ("--exclude-operation-id", "getSession") == (
        command[command.index("--exclude-operation-id")],
        command[command.index("--exclude-operation-id") + 1],
    )
    assert captured["cwd"] == repository_root()


def test_dry_run_does_not_call_implementations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry run called a step")

    monkeypatch.setattr("wright.steps.build_rust_binary", boom)
    monkeypatch.setattr("wright.steps.generate_openapi", boom)
    write_build(tmp_path, 'server = rust_binary(CargoPackage("jammer_server"))\n')

    run_cli(["--dry", str(tmp_path)])

    assert "rust_binary" in capsys.readouterr().out
