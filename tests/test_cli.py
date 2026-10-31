import subprocess
from pathlib import Path

import pytest
from support import EMITTER, isolate_repo, plan_names, run_cli, write_build

from roadie import cli


def _git_start_failure(directory: Path) -> str:
    try:
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return str(error)
    raise AssertionError("git started")


# (spec) Project root
# (spec) Command line
def test_missing_directory_and_missing_git_are_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = (tmp_path / "absent").resolve()
    message = f"build file does not exist: {missing}"
    with pytest.raises(SystemExit) as missing_run:
        cli.run(file=missing)
    assert str(missing_run.value) == message
    with pytest.raises(SystemExit) as missing_clean:
        cli.run(file=missing, clean=True)
    assert str(missing_clean.value) == message

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    git_message = f"cannot run git to find the project root: {_git_start_failure(project)}"
    with pytest.raises(SystemExit) as git_run:
        cli.run(file=project)
    assert str(git_run.value) == git_message
    with pytest.raises(SystemExit) as git_clean:
        cli.run(file=project, clean=True)
    assert str(git_clean.value) == git_message


# (spec) Dry run output
# (spec) Command line
def test_dry_run_marks_reuse_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    project = root / "project"
    write_build(project, EMITTER.replace("api = Emitter()", 'api = Emitter("v1")\n'))
    cli.run(file=project)
    capsys.readouterr()
    cli.run(file=project, dry_run=True)
    assert capsys.readouterr().out.splitlines() == ["api = Emitter(text='v1')  # reuse"]
    write_build(project, EMITTER.replace("api = Emitter()", 'api = Emitter("v2")\n'))
    cli.run(file=project, dry_run=True)
    assert capsys.readouterr().out.splitlines() == ["api = Emitter(text='v2')  # run"]


# (spec) Command line
@pytest.mark.parametrize(
    ("kind", "marker"),
    [("reusable = True", "# reuse"), ("reusable = False", "# run")],
)
def test_dry_run_marks_a_step_without_a_record_by_whether_it_reuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    marker: str,
) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    write_build(
        root,
        EMITTER
        + "\nclass After(Step[str]):\n"
        + "    recorded = False\n"
        + f"    {kind}\n"
        + "    def __init__(self, source):\n"
        + "        self.source = source\n"
        + "    def build(self, run):\n"
        + "        return 'done'\n"
        + "\nafter = After(api)\n",
    )
    cli.run(file=root)
    capsys.readouterr()
    cli.run(file=root, dry_run=True)
    assert capsys.readouterr().out.splitlines()[-1] == f"after = After(source=api)  {marker}"


# (spec) Settings
# (spec) Command line
def test_cli_base_dir_applies_to_included_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project/module", 'server = RustBinary(CargoPackage("module"))\n')
    write_build(tmp_path / "project", 'module = include("module")\napi = Group(module.server)\n')
    observed: list[Path] = []
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: observed.extend(step.base_dir for step in steps))
    run_cli(["--base_dir", "work", "-f", "project", "api"])
    assert observed == [tmp_path / "work", tmp_path / "work"]


# (spec) Environment files
# (spec) Command line
def test_cli_passes_env_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path, 'item = RustBinary(CargoPackage("server"))\n')
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: seen.update(kwargs))
    run_cli(["--env", "dev.env", "--env", "extra.env", "-f", str(tmp_path)])
    assert seen["env"] == [Path("dev.env"), Path("extra.env")] or seen["env"] == (
        Path("dev.env"),
        Path("extra.env"),
    )


# (spec) Dry run output
# (spec) Dependencies and attributes
# (spec) Command line
def test_tuple_attributes_declare_dependencies_and_dry_run_prints_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("roadie.build.rust.CargoPackage.fingerprint", lambda self, base_dir: f"package:{self.name}")
    write_build(tmp_path, '''from dataclasses import dataclass
from roadie.engine.run import Run

@dataclass(eq=False)
class Combined(Step[None]):
    inputs: tuple[Step, ...]
    def build(self, run: Run) -> None:
        pass

first = RustBinary(CargoPackage("first"))
second = RustBinary(CargoPackage("second"))
combined = Combined((first, second))
''')
    assert plan_names(tmp_path) == ["first", "second", "combined"]
    run_cli(["--dry", "-f", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "combined = Combined(inputs=(first, second))  # run"


# (spec) Dry run output
# (spec) Command line
# (spec) Generated files
def test_cli_passes_locked_and_prints_keyword_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(eq=False, kw_only=True)\n"
        "class Note(File):\n"
        "    def build(self, run):\n"
        "        return self.output\n"
        "api = Note(output='api.yaml')\n",
    )
    run_cli(["--dry", "-f", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "api = Note(output='api.yaml')  # run"
    observed: list[tuple[bool, object]] = []
    monkeypatch.setattr(
        cli,
        "execute",
        lambda steps, *, root, locked, env=(), dry=False, jobs=1, targets=None: observed.append(
            (locked, env)
        ),
    )
    run_cli(["--locked", "-f", str(tmp_path)])
    assert observed == [(True, ())]


# (spec) Dry run output
# (spec) Command line
def test_dry_run_omits_attributes_whose_value_is_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "from dataclasses import dataclass\n"
        "from roadie.engine.run import Run\n\n"
        "@dataclass(eq=False)\n"
        "class Setting(Step[None]):\n"
        "    label: str | None\n"
        "    note: str | None = None\n"
        "    def build(self, run: Run) -> None:\n"
        "        pass\n\n"
        "item = Setting(None)\n",
    )
    run_cli(["--dry", "-f", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "item = Setting()  # run"


# (spec) Dry run output
def test_dry_run_prints_attributes_of_a_plain_class_as_keywords(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(
        tmp_path,
        "class Plain(Step[None]):\n"
        "    def __init__(self, label):\n"
        "        self.label = label\n"
        "    def build(self, run):\n"
        "        return None\n"
        "item = Plain('v')\n",
    )
    run_cli(["--dry", "-f", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "item = Plain(label='v')  # run"


# (spec) Dry run output
# (spec) Deferred steps
def test_dry_run_marks_a_deferred_step_not_started_unless_a_consumer_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(
        tmp_path,
        "from pathlib import Path\n"
        "class Up:\n"
        "    def stop(self):\n"
        "        Path(__file__).with_name('stopped.txt').write_text('stopped')\n"
        "class Server(Service):\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'server.txt').write_text('up')\n"
        "        return Up()\n"
        "class Job(Step[str]):\n"
        "    def __init__(self, server, label='a'):\n"
        "        self.server = server\n"
        "        self.label = label\n"
        "    def build(self, run):\n"
        "        return self.label\n"
        "server = Server()\n"
        "job = Job(server)\n",
    )
    cli.run(file=tmp_path)
    capsys.readouterr()
    (tmp_path / "server.txt").unlink()
    (tmp_path / "stopped.txt").unlink()
    cli.run(file=tmp_path, dry_run=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].endswith("# not started")
    assert lines[-1].endswith("# reuse")
    assert not (tmp_path / "server.txt").exists()
    assert not (tmp_path / "stopped.txt").exists()

    write_build(
        tmp_path,
        "from pathlib import Path\n"
        "class Up:\n"
        "    def stop(self):\n"
        "        Path(__file__).with_name('stopped.txt').write_text('stopped')\n"
        "class Server(Service):\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'server.txt').write_text('up')\n"
        "        return Up()\n"
        "class Job(Step[str]):\n"
        "    def __init__(self, server, label='b'):\n"
        "        self.server = server\n"
        "        self.label = label\n"
        "    def build(self, run):\n"
        "        return self.label\n"
        "server = Server()\n"
        "job = Job(server)\n",
    )
    cli.run(file=tmp_path, dry_run=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].endswith("# run")
    assert lines[-1].endswith("# run")
    assert not (tmp_path / "server.txt").exists()
    assert not (tmp_path / "stopped.txt").exists()


# (spec) Command line
def test_help_names_the_build_file_and_base_dir(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit:
        cli.app(["--help"])
    assert exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--file" in help_text
    assert "-f " in help_text or "-f," in help_text or " -f" in help_text
    assert "--base-dir" in help_text
    assert "--base_dir" not in help_text


# (spec) Command line
def test_a_directory_word_hints_to_pass_the_build_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path, "item = Check('true')\n")
    with pytest.raises(SystemExit, match=r"has no result named '\.'; to use the build file in \., pass -f \."):
        run_cli([".", "item", "--dry"])


# (spec) Command line
def test_several_results_run_once_over_their_shared_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    def build(self, run):\n"
        "        return 'n'\n"
        "class Job(Step[str]):\n"
        "    def __init__(self, source):\n"
        "        self.source = source\n"
        "    def build(self, run):\n"
        "        return 'j'\n"
        "shared = Note()\n"
        "one = Job(shared)\n"
        "two = Job(shared)\n",
    )
    run_cli(["-f", str(tmp_path / "build.py"), "one", "two", "--dry"])
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(" = ")[0] for line in lines] == ["shared", "one", "two"]
    assert all(line.endswith("# run") for line in lines)


# (spec) Command line
def test_two_services_cannot_both_be_requested(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "class Up:\n"
        "    def stop(self):\n"
        "        pass\n"
        "class Api(Service):\n"
        "    def build(self, run):\n"
        "        return Up()\n"
        "class Web(Service):\n"
        "    def build(self, run):\n"
        "        return Up()\n"
        "api = Api()\n"
        "web = Web()\n",
    )
    with pytest.raises(
        SystemExit,
        match="api and web can each be requested only once per run; request one of them",
    ):
        run_cli(["-f", str(tmp_path), "api", "web", "--dry"])


# (spec) Command line
def test_a_missing_build_file_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(SystemExit, match=f"build file does not exist: {missing}"):
        run_cli(["-f", str(missing), "--dry"])
