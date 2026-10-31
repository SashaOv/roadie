import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from support import Stopped, execute, isolate_repo, write_build

from roadie import cli
from roadie.build import Service, Step
from roadie.engine.errors import StepFailed
from roadie.engine.run import Run

_HOLDER = """
import sys
from pathlib import Path
from roadie.engine.lock import acquire_run_lock
held = acquire_run_lock(Path(sys.argv[1]))
print("held", flush=True)
sys.stdin.readline()
held.release()
"""

_WAITER = """
import sys
from pathlib import Path
from roadie.engine.lock import acquire_run_lock
held = acquire_run_lock(Path(sys.argv[1]))
print("acquired", flush=True)
held.release()
"""

_EMPTY_HOLDER = """
import fcntl
import os
import sys
from pathlib import Path
path = Path(sys.argv[1]) / "target" / "roadie" / "run.lock"
path.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
fcntl.flock(fd, fcntl.LOCK_UN)
os.close(fd)
"""


def _process(code: str, root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    return process.stdout.readline().rstrip("\n")


def _note() -> Step[str]:
    class Note(Step[str]):
        def build(self, run: Run) -> str:
            return "v"

    note = Note()
    note.name = note.variable = "note"
    return note


# (spec) One run at a time
def test_a_run_takes_the_lock_and_releases_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    seen: list[bool] = []

    class Note(Step[str]):
        def build(self, run: Run) -> str:
            seen.append(lock.is_file())
            return "v"

    note = Note()
    note.name = note.variable = "note"
    execute([note])
    assert seen == [True]
    assert lock.is_file()
    assert execute([_note()]) == "v"


# (spec) One run at a time
def test_a_run_waits_for_the_holder_and_proceeds_when_it_releases(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = root / "target" / "roadie" / "run.lock"
    holder = _process(_HOLDER, root)
    waiter = None
    try:
        assert holder.stdin is not None
        assert _line(holder) == "held"
        waiter = _process(_WAITER, root)
        waiting = _line(waiter)
        assert waiting.startswith(f"waiting for another roadie run: {lock} (pid {holder.pid}, started ")
        assert waiting.endswith(")")
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert _line(waiter) == "acquired"
        assert waiter.wait() == 0
        assert lock.is_file()
    finally:
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
        if holder.poll() is None:
            holder.kill()


# (spec) One run at a time
def test_a_run_waits_without_a_pid_when_the_lock_file_cannot_be_read(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = root / "target" / "roadie" / "run.lock"
    holder = _process(_EMPTY_HOLDER, root)
    waiter = None
    try:
        assert holder.stdin is not None
        assert _line(holder) == "held"
        waiter = _process(_WAITER, root)
        assert _line(waiter) == f"waiting for another roadie run: {lock}"
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert _line(waiter) == "acquired"
    finally:
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
        if holder.poll() is None:
            holder.kill()


# (spec) One run at a time
def test_a_run_proceeds_after_the_holder_is_killed(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    holder = _process(_HOLDER, root)
    waiter = None
    try:
        assert _line(holder) == "held"
        waiter = _process(_WAITER, root)
        assert _line(waiter).startswith("waiting for another roadie run:")
        holder.kill()
        holder.wait()
        assert _line(waiter) == "acquired"
    finally:
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
        if holder.poll() is None:
            holder.kill()


# (spec) One run at a time
def test_a_failed_run_releases_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)

    lock = root / "target" / "roadie" / "run.lock"
    seen: list[bool] = []

    class Boom(Step[None]):
        def build(self, run: Run) -> None:
            seen.append(lock.is_file())
            raise RuntimeError("nope")

    boom = Boom()
    boom.name = "boom"
    with pytest.raises(StepFailed, match="nope"):
        execute([boom])
    assert seen == [True]
    assert lock.is_file()
    assert execute([_note()]) == "v"


# (spec) One run at a time
# (spec) Requested result
def test_the_lock_is_released_before_the_result_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    lock = root / "target" / "roadie" / "run.lock"
    seen: list[tuple[str, bool]] = []

    class Server(Service):
        def build(self, run: Run) -> Stopped:
            seen.append(("build", lock.is_file()))
            return Stopped()

        def serve(self, value: Stopped) -> None:
            probe = _process(_WAITER, root)
            try:
                seen.append(("serve", _line(probe) == "acquired"))
            finally:
                if probe.poll() is None:
                    probe.kill()

    execute([Server()])
    assert seen == [("build", True), ("serve", True)]
    assert lock.is_file()


# (spec) One run at a time
# (spec) Command line
def test_dry_run_takes_the_lock_like_a_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    write_build(root, "class Note(Step[str]):\n    def build(self, run):\n        return 'v'\nitem = Note()\n")
    lock = root / "target" / "roadie" / "run.lock"
    cli.run(root, dry_run=True)
    assert lock.is_file()
    assert "# run" in capsys.readouterr().out


# (spec) One run at a time
# (spec) Command line
def test_a_dry_run_waits_for_the_holder(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    write_build(root, "class Note(Step[str]):\n    def build(self, run):\n        return 'v'\nitem = Note()\n")
    holder = _process(_HOLDER, root)
    dry = None
    try:
        assert holder.stdin is not None
        assert _line(holder) == "held"
        dry = _process(
            "import sys\nfrom pathlib import Path\nfrom roadie import cli\n"
            "cli.run(Path(sys.argv[1]), dry_run=True)\nprint('dry-done', flush=True)\n",
            root,
        )
        assert _line(dry).startswith("waiting for another roadie run:")
        holder.stdin.write("\n")
        holder.stdin.flush()
        lines = []
        while (line := _line(dry)) != "":
            lines.append(line)
        assert any("# run" in line for line in lines)
        assert lines[-1] == "dry-done"
        assert dry.wait() == 0
    finally:
        if dry is not None and dry.poll() is None:
            dry.kill()
        if holder.poll() is None:
            holder.kill()


# (spec) Command line
def test_clean_deletes_roadie_state_without_building(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path.resolve()
    (root / "build.py").write_text("raise SystemExit('loaded')\n")
    state = root / "target" / "roadie"
    state.mkdir(parents=True)
    (state / "build.record.json").write_text("{}")
    (state / "note.txt").write_text("artifact")
    cli.run(root, clean=True, dry_run=True)
    assert sorted(path.name for path in state.iterdir()) == ["run.lock"]
    assert capsys.readouterr().out == ""
    (state / "note.txt").write_text("again")
    cli.run(root, clean=True)
    assert sorted(path.name for path in state.iterdir()) == ["run.lock"]


# (spec) Command line
# (spec) One run at a time
def test_clean_waits_for_the_holder_before_deleting(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    state = root / "target" / "roadie"
    holder = _process(_HOLDER, root)
    cleaner = None
    try:
        assert holder.stdin is not None
        assert _line(holder) == "held"
        (state / "note.txt").write_text("keep")
        cleaner = _process(
            "import sys\nfrom pathlib import Path\nfrom roadie import cli\n"
            "cli.run(Path(sys.argv[1]), clean=True)\nprint('cleaned', flush=True)\n",
            root,
        )
        assert _line(cleaner).startswith("waiting for another roadie run:")
        assert (state / "note.txt").read_text() == "keep"
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert _line(cleaner) == "cleaned"
        assert cleaner.wait() == 0
        assert not (state / "note.txt").exists()
        assert (state / "run.lock").is_file()
    finally:
        if cleaner is not None and cleaner.poll() is None:
            cleaner.kill()
        if holder.poll() is None:
            holder.kill()


# (spec) One run at a time
def test_a_step_that_runs_roadie_in_the_same_project_fails(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = root / "target" / "roadie" / "run.lock"
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from roadie import cli\n"
        "cli.run(Path(sys.argv[1]))\n"
    )
    write_build(
        root,
        "import sys\n"
        "\n"
        "class Again(Step[None]):\n"
        "    def build(self, run):\n"
        f"        self._cmd(sys.executable, '-c', {script!r}, {str(root)!r})\n"
        "\n"
        "again = Again()\n",
    )
    message = f"{lock} is held by the roadie run that started this one (pid {os.getpid()})"
    with pytest.raises(SystemExit, match=re.escape(message)):
        cli.run(root)


# (spec) One run at a time
# (spec) Step helpers
def test_a_step_program_sees_this_run_pid_over_an_environment_file(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    env_file = root / "pid.env"
    env_file.write_text("ROADIE_RUN_PID=1\n")

    class Again(Step[str]):
        def build(self, run: Run) -> str:
            completed = self._cmd(
                sys.executable,
                "-c",
                "import os; print(os.environ['ROADIE_RUN_PID'])",
                environment={"ROADIE_RUN_PID": "2"},
                capture_output=True,
            )
            return completed.stdout.strip()

    again = Again()
    again.name = "again"
    assert execute([again], root=root, env=(env_file,)) == str(os.getpid())


# (spec) One run at a time
def test_a_run_started_by_an_unrelated_process_still_waits(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock = root / "target" / "roadie" / "run.lock"
    holder = _process(_HOLDER, root)
    waiter = None
    try:
        assert holder.stdin is not None
        assert _line(holder) == "held"
        environment = os.environ.copy()
        environment["ROADIE_RUN_PID"] = str(os.getpid())
        waiter = subprocess.Popen(
            [sys.executable, "-c", _WAITER, str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        waiting = _line(waiter)
        assert waiting.startswith(
            f"waiting for another roadie run: {lock} (pid {holder.pid}, started "
        )
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert _line(waiter) == "acquired"
    finally:
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
        if holder.poll() is None:
            holder.kill()
