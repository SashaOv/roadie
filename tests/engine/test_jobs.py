import _thread
import os
import subprocess
import sys
import threading
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import FakeDeployment, Note, execute, write_build

from roadie import cli
from roadie.build import Service, Step
from roadie.engine import process as processes
from roadie.engine.errors import RoadieError, StepFailed
from roadie.engine.inputs import run_state
from roadie.engine.lock import lock_path
from roadie.engine.process import RunningProcess, available_port
from roadie.engine.run import Run
from roadie.system import lock as lock_file
from roadie.system import unlock as unlock_file

_WAIT = 2


def _named(step: Step[object], name: str) -> Step[object]:
    step.name = step.variable = name
    return step


# (spec) Parallel steps
def test_one_job_leaves_output_unprefixed(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            print("from-step", flush=True)
            self._cmd(sys.executable, "-c", "print('from-prog', flush=True)")
            return "v"

    execute([_named(Note(), "note")], root=tmp_path, jobs=1)
    lines = capfd.readouterr().out.splitlines()
    assert "from-step" in lines
    assert "from-prog" in lines
    assert not any(line.startswith("[note] ") for line in lines)


# (spec) Parallel steps
def test_a_step_without_a_name_is_prefixed_with_its_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            print("partial", end="")
            return "v"

    execute([Note()], root=tmp_path, jobs=2)
    assert "[Note] partial" in capsys.readouterr().out.splitlines()


# (spec) Parallel steps
def test_parallel_output_prefixes_each_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    started = {"a": threading.Event(), "b": threading.Event()}

    @dataclass(eq=False)
    class Side(Step[str]):
        label: str

        def build(self, run: Run) -> str:
            print(f"step-{self.label}", flush=True)
            started[self.label].set()
            other = "b" if self.label == "a" else "a"
            assert started[other].wait(_WAIT)
            self._cmd(
                sys.executable,
                "-c",
                "import sys\n"
                f"label = {self.label!r}\n"
                "for i in range(10):\n"
                "    print(f'{label}-{i}', flush=True)\n",
            )
            return self.label

    execute([_named(Side("a"), "a"), _named(Side("b"), "b")], root=tmp_path, jobs=2)
    lines = capsys.readouterr().out.splitlines()
    assert "[a] step-a" in lines
    assert "[b] step-b" in lines
    for label in ("a", "b"):
        got = [line for line in lines if line.startswith(f"[{label}] {label}-")]
        assert got == [f"[{label}] {label}-{i}" for i in range(10)]


# (spec) Parallel steps
def test_captured_output_is_returned_and_not_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            completed = self._cmd(
                sys.executable,
                "-c",
                "print('from' + '-prog', flush=True)",
                capture_output=True,
            )
            return completed.stdout or ""

    assert execute([_named(Note(), "note")], root=tmp_path, jobs=2) == "from-prog\n"
    assert "from-prog" not in capsys.readouterr().out


# (spec) Parallel steps
def test_one_job_shows_stderr_unprefixed_and_hides_captured_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            completed = self._cmd(
                sys.executable,
                "-c",
                "import sys; print('from' + '-prog', flush=True); print('warn', file=sys.stderr)",
                capture_output=True,
            )
            return completed.stdout or ""

    assert execute([_named(Note(), "note")], root=tmp_path, jobs=1) == "from-prog\n"
    shown = capsys.readouterr()
    assert "from-prog" not in shown.out
    assert shown.err.splitlines() == ["warn"]


# (spec) Parallel steps
# (spec) Failures
def test_a_failing_program_shows_prefixed_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Bad(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(1)")

    with pytest.raises(StepFailed, match="boom"):
        execute([_named(Bad(), "bad")], root=tmp_path, jobs=2)
    assert "[bad] boom" in capsys.readouterr().err.splitlines()


# (spec) Parallel steps
def test_a_servers_output_is_prefixed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    class Up(Step[str]):
        def build(self, run: Run) -> str:
            port = available_port()
            server = self._start_cmd(
                sys.executable,
                "-c",
                "import socket\n"
                "print('booting', flush=True)\n"
                f"s = socket.create_server(('127.0.0.1', {port}))\n"
                "s.accept()\n",
                port=port,
            )
            server.stop()
            return "up"

    execute([_named(Up(), "up")], root=tmp_path, jobs=2)
    assert "[up] booting" in capsys.readouterr().out.splitlines()


# (spec) Parallel steps
def test_a_server_that_prints_five_megabytes_keeps_under_one(
    tmp_path: Path,
) -> None:
    started: list[RunningProcess] = []
    printed = tmp_path / "printed"

    class Up(Step[str]):
        def build(self, run: Run) -> str:
            port = available_port()
            server = self._start_cmd(
                sys.executable,
                "-c",
                "import socket\n"
                f"s = socket.create_server(('127.0.0.1', {port}))\n"
                "for _ in range(5000): print('z' * 999, flush=True)\n"
                f"open({str(printed)!r}, 'w').write('done')\n"
                "s.accept()\n",
                port=port,
            )
            started.append(server)
            deadline = time.monotonic() + 10
            while not printed.is_file():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            server.stop()
            return "up"

    tracemalloc.start()
    try:
        execute([_named(Up(), "up")], root=tmp_path, jobs=2)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
    server = started[0]
    assert server.readers
    assert not any(reader.is_alive() for reader in server.readers)


# (spec) Parallel steps
# (spec) Failures
def test_a_server_that_exits_shows_prefixed_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Up(Step[str]):
        def build(self, run: Run) -> str:
            self._start_cmd(
                sys.executable,
                "-c",
                "print('booting', flush=True)",
                port=available_port(),
            )
            return "up"

    with pytest.raises(StepFailed, match="before becoming ready"):
        execute([_named(Up(), "up")], root=tmp_path, jobs=2)
    assert "[up] booting" in capsys.readouterr().out.splitlines()


# (spec) Command line
def test_jobs_defaults_to_the_cpu_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_build(tmp_path, "from support import Note\nitem = Note()\n")
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(os, "process_cpu_count", lambda: 3)
    cli.run(file=tmp_path)
    assert seen["jobs"] == 3
    monkeypatch.setattr(os, "process_cpu_count", lambda: None)
    cli.run(file=tmp_path)
    assert seen["jobs"] == 1


# (spec) Command line
def test_jobs_below_one_fails(tmp_path: Path) -> None:
    with pytest.raises(RoadieError, match="--jobs must be at least 1"):
        execute([_named(Note(), "note")], root=tmp_path, jobs=0)
    with pytest.raises(SystemExit, match="--jobs must be at least 1"):
        cli.run(file=tmp_path, jobs=0)


# (spec) Parallel steps
def test_independent_steps_run_at_once(tmp_path: Path) -> None:
    started = {"a": threading.Event(), "b": threading.Event()}

    @dataclass(eq=False)
    class Side(Step[str]):
        label: str

        def build(self, run: Run) -> str:
            started[self.label].set()
            other = "b" if self.label == "a" else "a"
            assert started[other].wait(_WAIT), f"{other} did not start while {self.label} was running"
            return self.label

    left = _named(Side("a"), "a")
    right = _named(Side("b"), "b")
    assert execute([left, right], root=tmp_path, jobs=2) == "b"


# (spec) Parallel steps
def test_one_job_runs_steps_one_at_a_time(tmp_path: Path) -> None:
    gate = threading.Lock()

    class Side(Step[str]):
        def build(self, run: Run) -> str:
            if not gate.acquire(blocking=False):
                raise RuntimeError("overlapped")
            try:
                return "v"
            finally:
                gate.release()

    execute([_named(Side(), "a"), _named(Side(), "b")], root=tmp_path, jobs=1)


# (spec) Failures
# (spec) Records
def test_a_failure_waits_for_the_running_step_and_keeps_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    good_ready = threading.Event()
    bad_ready = threading.Event()
    holding = threading.Event()
    release = threading.Event()
    built: list[str] = []

    class Good(Step[str]):
        def build(self, run: Run) -> str:
            built.append("good")
            good_ready.set()
            assert bad_ready.wait(_WAIT)
            holding.set()
            assert release.wait(_WAIT)
            return "ok"

    class Bad(Step[None]):
        def build(self, run: Run) -> None:
            built.append("bad")
            bad_ready.set()
            assert good_ready.wait(_WAIT)
            assert holding.wait(_WAIT)
            raise RuntimeError("nope")

    original = print

    def watching(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        if args and args[0] == "waiting for good":
            release.set()

    monkeypatch.setattr("builtins.print", watching)
    good = _named(Good(), "good")
    bad = _named(Bad(), "bad")
    with pytest.raises(StepFailed, match="bad failed: nope"):
        execute([good, bad], root=root, jobs=2)
    assert capsys.readouterr().out.count("waiting for good") == 1
    assert set(built) == {"good", "bad"}

    class BadAgain(Step[None]):
        def build(self, run: Run) -> None:
            built.append("bad")
            raise RuntimeError("nope")

    built.clear()
    with pytest.raises(StepFailed, match="bad failed: nope"):
        execute([_named(Good(), "good"), _named(BadAgain(), "bad")], root=root, jobs=2)
    assert "good" not in built
    assert "reused good" in capsys.readouterr().out


# (spec) Failures
def test_a_later_failure_is_printed_and_the_first_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_ready = threading.Event()
    second_ready = threading.Event()
    holding = threading.Event()
    release = threading.Event()

    class First(Step[None]):
        def build(self, run: Run) -> None:
            first_ready.set()
            assert second_ready.wait(_WAIT)
            assert holding.wait(_WAIT)
            raise RuntimeError("first")

    class Second(Step[None]):
        def build(self, run: Run) -> None:
            second_ready.set()
            assert first_ready.wait(_WAIT)
            holding.set()
            assert release.wait(_WAIT)
            raise RuntimeError("second")

    original = print

    def watching(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        if args and args[0] == "waiting for second":
            release.set()

    monkeypatch.setattr("builtins.print", watching)
    with pytest.raises(StepFailed, match="first failed: first"):
        execute(
            [_named(First(), "first"), _named(Second(), "second")],
            root=tmp_path,
            jobs=2,
        )
    assert "second failed: second" in capsys.readouterr().err


# (spec) Deferred steps
def test_a_deferred_step_starts_once_while_consumers_run(tmp_path: Path) -> None:
    starts: list[str] = []
    consumers = {"one": threading.Event(), "two": threading.Event()}

    class Lazy(Step[str]):
        deferred = True

        def build(self, run: Run) -> str:
            starts.append("lazy")
            return "up"

    @dataclass(eq=False)
    class Uses(Step[str]):
        lazy: Step[str]
        label: str

        def build(self, run: Run) -> str:
            consumers[self.label].set()
            other = "two" if self.label == "one" else "one"
            assert consumers[other].wait(_WAIT)
            return run[self.lazy]

    lazy = Lazy()
    execute(
        [lazy, _named(Uses(lazy, "one"), "one"), _named(Uses(lazy, "two"), "two")],
        root=tmp_path,
        jobs=2,
    )
    assert starts == ["lazy"]


def _shared_api(starts: list[str], teardowns: list[str]) -> Step[str]:
    class Api(Step[str]):
        deferred = True

        def build(self, run: Run) -> str:
            starts.append("api")
            return "up"

        def teardown(self, value: str) -> None:
            teardowns.append("api")

    api = Api()
    api.name = api.variable = "api"
    return api


# (spec) Deferred steps
# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_shared_deferred_step_around_a_build_failure(
    jobs: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    starts: list[str] = []
    teardowns: list[str] = []
    api = _shared_api(starts, teardowns)
    first_failed = threading.Event()

    @dataclass(eq=False)
    class Uses(Step[str]):
        api: Step[str]
        label: str

        def build(self, run: Run) -> str:
            assert run[self.api] == "up"
            if self.label == "one":
                first_failed.set()
                raise RuntimeError("one broke")
            assert first_failed.wait(_WAIT)
            raise RuntimeError("two broke")

    with pytest.raises(StepFailed, match="one failed: one broke"):
        execute(
            [api, _named(Uses(api, "one"), "one"), _named(Uses(api, "two"), "two")],
            root=tmp_path,
            jobs=jobs,
        )
    assert starts == ["api"]
    assert teardowns == ["api"]
    if jobs == 2:
        assert "two failed: two broke" in capsys.readouterr().err


# (spec) Deferred steps
# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
@pytest.mark.parametrize("hook", ["publish", "report"])
def test_a_shared_deferred_step_around_a_consumer_hook_failure(
    jobs: int, hook: str, tmp_path: Path
) -> None:
    starts: list[str] = []
    teardowns: list[str] = []
    api = _shared_api(starts, teardowns)

    @dataclass(eq=False)
    class Uses(Step[str]):
        api: Step[str]

        def build(self, run: Run) -> str:
            return run[self.api]

        def publish(self, run: Run, value: str) -> str:
            if hook == "publish":
                raise RuntimeError("publish broke")
            return value

        def report(self, run: Run, value: str) -> None:
            if hook == "report":
                raise RuntimeError("report broke")

    @dataclass(eq=False)
    class Other(Step[str]):
        api: Step[str]

        def build(self, run: Run) -> str:
            return run[self.api]

    with pytest.raises(StepFailed, match=f"one failed: {hook} broke"):
        execute(
            [api, _named(Uses(api), "one"), _named(Other(api), "two")],
            root=tmp_path,
            jobs=jobs,
        )
    assert starts == ["api"]
    assert teardowns == ["api"]


# (spec) Deferred steps
# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_shared_deferred_step_around_an_interrupt(
    jobs: int, tmp_path: Path
) -> None:
    starts: list[str] = []
    teardowns: list[str] = []
    api = _shared_api(starts, teardowns)
    pidfile = tmp_path / "child.pid"

    @dataclass(eq=False)
    class Uses(Step[None]):
        api: Step[str]

        def build(self, run: Run) -> None:
            assert run[self.api] == "up"
            self._cmd(sys.executable, "-c", _sleep_until_stopped(pidfile))

    threading.Thread(
        target=_interrupt_when_pidfile_appears, args=(pidfile,), daemon=True
    ).start()
    with pytest.raises(KeyboardInterrupt):
        execute(
            [api, _named(Uses(api), "one"), _named(Uses(api), "two")],
            root=tmp_path,
            jobs=jobs,
        )
    assert starts == ["api"]
    assert teardowns == ["api"]


# (spec) Deferred steps
# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_a_shared_deferred_step_whose_teardown_fails_after_a_consumer(
    jobs: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    starts: list[str] = []

    class Api(Step[str]):
        deferred = True

        def build(self, run: Run) -> str:
            starts.append("api")
            return "up"

        def teardown(self, value: str) -> None:
            raise RuntimeError("stop broke")

    @dataclass(eq=False)
    class Uses(Step[str]):
        api: Step[str]

        def build(self, run: Run) -> str:
            assert run[self.api] == "up"
            raise RuntimeError("one broke")

    api = Api()
    api.name = api.variable = "api"
    with pytest.raises(StepFailed, match="one failed: one broke"):
        execute([api, _named(Uses(api), "one")], root=tmp_path, jobs=jobs)
    assert starts == ["api"]
    assert "api failed: teardown: stop broke" in capsys.readouterr().err


# (spec) Teardown
def test_a_step_is_torn_down_after_every_step_that_takes_it(tmp_path: Path) -> None:
    stopped = threading.Event()
    events: list[str] = []

    class Resource(Step[str]):
        def build(self, run: Run) -> str:
            return "held"

        def teardown(self, value: str) -> None:
            events.append("stop")
            stopped.set()

    @dataclass(eq=False)
    class Consumer(Step[None]):
        resource: Step[str]

        def build(self, run: Run) -> None:
            events.append("consume")

    class Slow(Step[None]):
        def build(self, run: Run) -> None:
            events.append("slow")
            assert stopped.wait(_WAIT)

    resource = Resource()
    execute([resource, Slow(), Consumer(resource)], root=tmp_path, jobs=2)
    assert events.index("consume") < events.index("stop")


# (spec) Plans and runs
def test_worker_threads_share_run_state_and_see_the_project_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ready = {"a": threading.Event(), "b": threading.Event()}

    @dataclass(eq=False)
    class Side(Step[tuple[str, ...]]):
        label: str

        def build(self, run: Run) -> tuple[str, ...]:
            run.artifact(self, ".txt")
            state = run_state("shared", list)
            state.append(self.label)
            ready[self.label].set()
            other = "b" if self.label == "a" else "a"
            assert ready[other].wait(_WAIT)
            return tuple(run_state("shared", list))

    left = execute(
        [_named(Side("a"), "a"), _named(Side("b"), "b")],
        root=root,
        jobs=2,
    )
    assert set(left) == {"a", "b"}


# (spec) Command line
def test_a_dry_run_prints_marks_in_plan_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    execute(
        [_named(Note(), "a"), _named(Note(), "b"), _named(Note(), "c")],
        root=tmp_path,
        jobs=2,
        dry=True,
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if line.endswith("# run")]
    assert [line.split(" = ")[0] for line in lines] == ["a", "b", "c"]


# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_stops_the_program_a_step_is_running(tmp_path: Path, jobs: int) -> None:
    root = tmp_path.resolve()
    pidfile = root / "child.pid"
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    class Resource(Step[str]):
        def build(self, run: Run) -> str:
            return "held"

        def teardown(self, value: str) -> None:
            value_holder.append("stop")

    @dataclass(eq=False)
    class Program(Step[None]):
        resource: Step[str]

        def build(self, run: Run) -> None:
            assert run[self.resource] == "held"
            self._cmd(sys.executable, "-c", script)

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                _thread.interrupt_main()
                return
            time.sleep(0.05)

    value_holder: list[str] = []
    resource = Resource()
    threading.Thread(target=interrupt_when_ready, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        execute([resource, Program(resource)], root=root, jobs=jobs)
    assert value_holder == ["stop"]
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child {pid} survived the interrupt")


# (spec) Teardown
def test_ctrl_c_stops_a_command_through_the_system_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    pidfile = root / "child.pid"
    calls: list[tuple[int, bool]] = []
    real_interrupt = processes.interrupt_process

    def recording(process: subprocess.Popen[bytes], *, session: bool) -> None:
        calls.append((process.pid, session))
        real_interrupt(process, session=session)

    monkeypatch.setattr(processes, "interrupt_process", recording)

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(sys.executable, "-c", _sleep_until_stopped(pidfile))

    threading.Thread(
        target=_interrupt_when_pidfile_appears, args=(pidfile,), daemon=True
    ).start()
    with pytest.raises(KeyboardInterrupt):
        execute([_named(Note(), "note")], root=root, jobs=1)
    assert calls == [(int(pidfile.read_text()), False)]


def _interrupt_when_pidfile_appears(pidfile: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pidfile.exists() and pidfile.read_text().strip():
            _thread.interrupt_main()
            return
        time.sleep(0.05)


def _sleep_until_stopped(pidfile: Path) -> str:
    return (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )


# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_names_the_running_step(
    tmp_path: Path, jobs: int, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    pidfile = root / "child.pid"

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            self._cmd(sys.executable, "-c", _sleep_until_stopped(pidfile))

    threading.Thread(target=_interrupt_when_pidfile_appears, args=(pidfile,), daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        execute([_named(Note(), "note")], root=root, jobs=jobs)
    assert "interrupted: stopping note" in capsys.readouterr().out.splitlines()


# (spec) Step hooks
# (spec) Teardown
def test_a_kind_that_only_calls_super_serve_keeps_the_lock_until_teardown(
    tmp_path: Path,
) -> None:
    def lock_free() -> bool:
        handle = lock_path(tmp_path.resolve()).open("a+b")
        try:
            if not lock_file(handle, blocking=False):
                return False
            unlock_file(handle)
            return True
        finally:
            handle.close()

    seen: list[bool] = []

    class Almost(Step[str]):
        def build(self, run: Run) -> str:
            return "up"

        def serve(self, run: Run, value: str) -> None:
            super().serve(run, value)

        def teardown(self, value: str) -> None:
            seen.append(lock_free())

    class Other(Step[str]):
        def build(self, run: Run) -> str:
            return "other"

    almost = _named(Almost(), "almost")
    other = _named(Other(), "other")
    execute([almost, other], root=tmp_path, targets=[almost, other])
    assert seen == [False]


# (spec) Step hooks
# (spec) Requested result
def test_a_kind_that_only_calls_super_serve_prints_the_total_after_serving(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Almost(Step[str]):
        def build(self, run: Run) -> str:
            return "up"

        def serve(self, run: Run, value: str) -> None:
            print("almost served", flush=True)
            super().serve(run, value)

    execute([_named(Almost(), "almost")], root=tmp_path)
    output = capsys.readouterr().out
    assert output.index("almost served") < output.index("finished in")


# (spec) Step hooks
# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_does_not_name_a_kind_that_only_calls_super_serve(
    tmp_path: Path, jobs: int, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    pidfile = root / "child.pid"

    class Almost(Step[str]):
        deferred = True

        def build(self, run: Run) -> str:
            return "up"

        def serve(self, run: Run, value: str) -> None:
            super().serve(run, value)

    @dataclass(eq=False)
    class Note(Step[None]):
        api: Almost

        def build(self, run: Run) -> None:
            assert run[self.api] == "up"
            self._cmd(sys.executable, "-c", _sleep_until_stopped(pidfile))

    api = _named(Almost(), "api")
    note = _named(Note(api), "note")
    threading.Thread(target=_interrupt_when_pidfile_appears, args=(pidfile,), daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        execute([api, note], root=root, jobs=jobs)
    assert "interrupted: stopping note" in capsys.readouterr().out.splitlines()


# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_names_a_live_service(
    tmp_path: Path, jobs: int, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    pidfile = root / "child.pid"

    class Api(Service):
        def build(self, run: Run) -> FakeDeployment:
            return FakeDeployment()

    @dataclass(eq=False)
    class Note(Step[None]):
        api: Api

        def build(self, run: Run) -> None:
            assert run[self.api] is not None
            self._cmd(sys.executable, "-c", _sleep_until_stopped(pidfile))

    api = _named(Api(), "api")
    note = _named(Note(api), "note")
    threading.Thread(target=_interrupt_when_pidfile_appears, args=(pidfile,), daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        execute([api, note], root=root, jobs=jobs)
    assert "interrupted: stopping note, api" in capsys.readouterr().out.splitlines()


# (spec) Teardown
@pytest.mark.parametrize("jobs", [1, 2])
def test_an_interrupt_with_nothing_running_says_interrupted(
    tmp_path: Path, jobs: int, capsys: pytest.CaptureFixture[str]
) -> None:
    class Note(Step[None]):
        def build(self, run: Run) -> None:
            return None

        def teardown(self, value: None) -> None:
            _thread.interrupt_main()

    with pytest.raises(KeyboardInterrupt):
        execute([_named(Note(), "note")], root=tmp_path, jobs=jobs)
    assert "interrupted" in capsys.readouterr().out.splitlines()
