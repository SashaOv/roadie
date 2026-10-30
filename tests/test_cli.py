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
    message = f"directory does not exist: {missing}"
    with pytest.raises(SystemExit) as missing_run:
        cli.run(missing)
    assert str(missing_run.value) == message
    with pytest.raises(SystemExit) as missing_clean:
        cli.run(missing, clean=True)
    assert str(missing_clean.value) == message

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    git_message = f"cannot run git to find the project root: {_git_start_failure(project)}"
    with pytest.raises(SystemExit) as git_run:
        cli.run(project)
    assert str(git_run.value) == git_message
    with pytest.raises(SystemExit) as git_clean:
        cli.run(project, clean=True)
    assert str(git_clean.value) == git_message


# (spec) Dry run output
# (spec) Command line
def test_dry_run_marks_reuse_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    project = root / "project"
    write_build(project, EMITTER.replace("api = Emitter()", 'api = Emitter("v1")\n'))
    cli.run(project)
    capsys.readouterr()
    cli.run(project, dry_run=True)
    assert capsys.readouterr().out.splitlines() == ["api = Emitter(text='v1')  # reuse"]
    write_build(project, EMITTER.replace("api = Emitter()", 'api = Emitter("v2")\n'))
    cli.run(project, dry_run=True)
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
    cli.run(root)
    capsys.readouterr()
    cli.run(root, dry_run=True)
    assert capsys.readouterr().out.splitlines()[-1] == f"after = After(source=api)  {marker}"


# (spec) Settings
# (spec) Command line
def test_cli_base_dir_applies_to_included_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path / "project/module", 'server = RustBinary(CargoPackage("module"))\n')
    write_build(tmp_path / "project", 'module = include("module")\napi = Group(module.server)\n')
    observed: list[Path] = []
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: observed.extend(step.base_dir for step in steps))
    run_cli(["--base_dir", "work", "project", "api"])
    assert observed == [tmp_path / "work", tmp_path / "work"]


# (spec) Environment files
# (spec) Command line
def test_cli_passes_env_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_build(tmp_path, 'item = RustBinary(CargoPackage("server"))\n')
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "execute", lambda steps, **kwargs: seen.update(kwargs))
    run_cli(["--env", "dev.env", "--env", "extra.env", str(tmp_path)])
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
    run_cli(["--dry", str(tmp_path)])
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
    observed: list[tuple[bool, object]] = []
    monkeypatch.setattr(
        cli,
        "execute",
        lambda steps, *, root, locked, env=(): observed.append((locked, env)),
    )
    run_cli(["--locked", str(tmp_path)])
    assert observed == [(True, ())]
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "api = Note(output='api.yaml')  # run"


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
    run_cli(["--dry", str(tmp_path)])
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
    run_cli(["--dry", str(tmp_path)])
    assert capsys.readouterr().out.splitlines()[-1] == "item = Plain(label='v')  # run"
