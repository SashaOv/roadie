"""UserTask asks at a terminal and keeps the questions to itself."""

import os
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import Value, execute, isolate_repo, plan_names, run_cli, write_build

import roadie
from roadie.build import RoadieError, Step, UserTask
from roadie.engine.fingerprint import fingerprint
from roadie.engine.run import Run


def _read_until(
    fd: int, pending: bytes, marker: bytes, start: int = 0, timeout: float = 5
) -> bytes:
    deadline = time.monotonic() + timeout
    while marker not in pending[start:]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(pending.decode(errors="replace"))
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise AssertionError(pending.decode(errors="replace"))
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        pending += chunk
    return pending


def _pty_run(directory: Path, answers: list[str]) -> tuple[int, str]:
    import pty

    master, slave = pty.openpty()
    root = Path(roadie.__file__).resolve().parent.parent
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from roadie.cli import main; main()",
            "--jobs",
            "1",
            "-f",
            str(directory),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=root,
    )
    os.close(slave)
    pending = b""
    cursor = 0
    try:
        for answer in answers:
            pending = _read_until(master, pending, b"? ", cursor)
            cursor = pending.index(b"? ", cursor) + len(b"? ")
            os.write(master, f"{answer}\n".encode())
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    pending += os.read(master, 4096)
                except OSError:
                    break
        process.wait(timeout=5)
    finally:
        os.close(master)
    return process.returncode, pending.decode(errors="replace")


# (spec) Step kinds
def test_user_task_takes_instructions_and_string_checks() -> None:
    with pytest.raises(RoadieError, match="UserTask takes at least one check"):
        UserTask("Look.")
    with pytest.raises(RoadieError, match="UserTask takes checks as strings: got int"):
        UserTask("Look.", 1)
    with pytest.raises(RoadieError, match="UserTask takes instructions as a string: got int"):
        UserTask(1, "ok")  # type: ignore[arg-type]
    with pytest.raises(RoadieError, match="UserTask inputs takes inputs or steps: got int"):
        UserTask("Look.", "ok", inputs=1)


# (spec) Step kinds
def test_a_step_in_user_task_inputs_is_a_dependency(tmp_path: Path) -> None:
    write_build(tmp_path, "item = Group()\nlook = UserTask('Look.', 'ok', inputs=item)\n")
    assert plan_names(tmp_path, "look") == ["item", "look"]
    source = Value("built")
    task = UserTask("Look.", "ok", inputs=source)
    original = fingerprint(task, outputs={source: "a" * 64})
    assert fingerprint(task, outputs={source: "b" * 64}) != original


# (spec) Step kinds
def test_a_user_task_fails_without_a_terminal(tmp_path: Path) -> None:
    write_build(tmp_path, "look = UserTask('Look at the app.', 'the app starts')\n")
    with pytest.raises(SystemExit, match="look needs the user at a terminal"):
        run_cli(["--jobs", "1", "-f", str(tmp_path)])


# (spec) Step kinds
def test_dry_run_does_not_ask(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(tmp_path, "look = UserTask('Look at the app.', 'the app starts')\n")
    run_cli(["--dry", "--jobs", "1", "-f", str(tmp_path)])
    output = capsys.readouterr().out
    assert "? [y/n]" not in output
    assert "look = UserTask(" in output


# (spec) Step kinds
def test_a_user_task_records_a_pass_and_reuses_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(
        tmp_path,
        "look = UserTask('Look at the app.', 'the app starts', 'the button works')\n",
    )
    status, output = _pty_run(tmp_path, ["y", "yes"])
    assert status == 0, output
    assert "Look at the app." in output
    assert "the app starts? [y/n]" in output
    assert "the button works? [y/n]" in output

    run_cli(["--jobs", "1", "-f", str(tmp_path)])
    reused = capsys.readouterr().out
    assert "reused look" in reused
    assert "? [y/n]" not in reused


# (spec) Step kinds
def test_a_failed_user_check_is_not_recorded(tmp_path: Path) -> None:
    write_build(tmp_path, "look = UserTask('Look.', 'the app starts', 'the button works')\n")
    status, output = _pty_run(tmp_path, ["y", "maybe", "n", "the button is missing"])
    assert status != 0, output
    assert "the button works: the button is missing" in output
    with pytest.raises(SystemExit, match="look needs the user at a terminal"):
        run_cli(["--jobs", "1", "-f", str(tmp_path)])


# (spec) Step kinds
def test_a_user_task_fails_at_end_of_input(tmp_path: Path) -> None:
    import pty

    write_build(tmp_path, "look = UserTask('Look.', 'the app starts')\n")
    master, slave = pty.openpty()
    root = Path(roadie.__file__).resolve().parent.parent
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from roadie.cli import main; main()",
            "--jobs",
            "1",
            "-f",
            str(tmp_path),
        ],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root,
    )
    os.close(slave)
    os.close(master)
    output, errors = process.communicate(timeout=10)
    text = (output + errors).decode()
    assert process.returncode != 0, text
    assert "look: no answer from the user" in text


# (spec) Step kinds
def test_a_user_step_asks_while_nothing_else_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pty

    monkeypatch.chdir(tmp_path)
    isolate_repo(monkeypatch, tmp_path)
    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r", buffering=1)
    monkeypatch.setattr(sys, "stdin", stdin)
    asking = threading.Event()
    slow_entered = threading.Event()
    release = threading.Event()

    class Ask(UserTask):
        def build(self, run: Run) -> object:
            asking.set()
            return super().build(run)

    @dataclass(eq=False)
    class Slow(Step[None]):
        def build(self, run: Run) -> None:
            slow_entered.set()
            assert release.wait(5)

    task = Ask("Look.", "ready")
    task.name = "look"
    task.variable = "look"
    task.build_variables = frozenset({"look"})
    slow = Slow()
    slow.name = "slow"
    slow.variable = "slow"

    def coordinate() -> None:
        if asking.wait(2):
            assert not slow_entered.is_set()
            os.write(master, b"y\n")
            assert slow_entered.wait(2)
            release.set()
        else:
            assert slow_entered.is_set()
            release.set()
            assert asking.wait(2)
            os.write(master, b"y\n")

    thread = threading.Thread(target=coordinate)
    thread.start()
    try:
        execute([task, slow], root=tmp_path, jobs=2)
        thread.join(5)
    finally:
        if thread.is_alive():
            release.set()
            os.write(master, b"y\n")
            thread.join(5)
        os.close(master)
        stdin.close()
    assert not thread.is_alive()
