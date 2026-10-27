import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import pytest
from forge.environment import repository_root
from wright import Binary, Step
from wright.errors import StepFailed
from wright.process import RunningProcess
from wright.run import Run, execute
from wright.steps import ContractFuzz, JammerDevServer, OpenApi


@dataclass(eq=False)
class PathValue(Binary):
    value: Path

    def build(self, run: Run) -> Path:
        return self.value


@pytest.mark.parametrize("fails", [False, True])
def test_openapi_writes_stdout_or_reports_failure(
    fails: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wright.run.repository_root", lambda: tmp_path)
    calls: list[tuple[str, ...]] = []

    def command(
        self: Step[object], *args: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if fails:
            raise RuntimeError("cannot generate contract")
        return subprocess.CompletedProcess(args, 0, "openapi: 1\n", "")

    monkeypatch.setattr(Step, "_cmd", command)
    binary = PathValue(tmp_path / "server")
    api = OpenApi(binary)
    api.name = "api"
    if fails:
        with pytest.raises(StepFailed, match="api failed: cannot generate contract"):
            execute([binary, api])
        assert not (tmp_path / "target/wright/api.yaml").exists()
    else:
        assert execute([binary, api]) == tmp_path / "target/wright/api.yaml"
        assert (tmp_path / "target/wright/api.yaml").read_text() == "openapi: 1\n"
    assert calls == [(str(binary.value), "openapi")]


@pytest.mark.parametrize("capacity", [None, False, True])
@pytest.mark.parametrize("ending", ["success", "failure", "interrupt"])
def test_dev_server_environment_and_storage_cleanup(
    capacity: bool | None, ending: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setattr(
        "wright.steps.tempfile.mkdtemp", lambda prefix: storage.mkdir() or str(storage)
    )
    monkeypatch.setattr("wright.steps.available_port", lambda: 43210)
    seen: dict[str, object] = {}
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)

    def start(self: Step[object], *args: str, **kwargs: object) -> RunningProcess:
        seen["command"] = args
        seen.update(kwargs)
        assert (storage / "db").is_dir()
        return RunningProcess(process, "http://127.0.0.1:43210")

    monkeypatch.setattr(Step, "_start_cmd", start)
    binary = PathValue(tmp_path / "server")
    server = (
        JammerDevServer(binary)
        if capacity is None
        else JammerDevServer(binary, check_disk_capacity=capacity)
    )
    @dataclass(eq=False)
    class Consumer(Step[None]):
        source: JammerDevServer

        def build(self, run: Run) -> None:
            assert run[self.source].url == "http://127.0.0.1:43210"
            if ending == "failure":
                raise RuntimeError("rejected")
            if ending == "interrupt":
                raise KeyboardInterrupt

    try:
        if ending == "success":
            execute([binary, server, Consumer(server)])
        else:
            with pytest.raises(StepFailed if ending == "failure" else KeyboardInterrupt):
                execute([binary, server, Consumer(server)])
    finally:
        process.wait(timeout=5)
    environment = seen["environment"]
    assert isinstance(environment, dict)
    assert seen["command"] == (str(binary.value), "serve")
    assert seen["port"] == 43210
    assert environment["DATABASE_PATH"] == str(storage / "db/jammer.db")
    assert environment["STORAGE_ROOT"] == str(storage)
    assert environment.get("VOLUME_CAPACITY_RATIO") == (
        "1" if capacity is False else None
    )
    assert not storage.exists()


def test_failed_start_removes_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    monkeypatch.setattr(
        "wright.steps.tempfile.mkdtemp", lambda prefix: storage.mkdir() or str(storage)
    )

    def fail(*args: object, **kwargs: object) -> RunningProcess:
        raise RuntimeError("could not start")

    monkeypatch.setattr(Step, "_start_cmd", fail)
    binary = PathValue(tmp_path / "server")
    with pytest.raises(StepFailed, match="could not start"):
        execute([binary, JammerDevServer(binary)])
    assert not storage.exists()


def test_contract_fuzz_uses_contract_and_url_and_excludes_unsupported_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        Step,
        "_cmd",
        lambda self, *args, **kwargs: calls.append(args)
        or subprocess.CompletedProcess(args, 0, "", ""),
    )
    api = PathValue(repository_root() / "target/wright/generated.yaml")
    server = JammerDevServer(api)
    run = Run()
    run._values[id(api)] = api.value

    # No server is launched to inspect the fuzz command.
    class Deployment:
        url = "http://127.0.0.1:43210"

    run._values[id(server)] = Deployment()
    ContractFuzz(api, server).build(run)
    command = calls[0]
    assert str(api.value) in command
    assert ("--url", Deployment.url) in pairwise(command)
    for operation in ("getSession", "controlHeapProfiling", "dumpHeapProfile"):
        assert ("--exclude-operation-id", operation) in pairwise(command)
    assert ("--exclude-path", "/inbound") in pairwise(command)
    assert "getTerms" not in command
