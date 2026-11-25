from pathlib import Path

import pytest
from support import execute, run_cli, write_build

from roadie.build import Attribute, Param, RoadieError, Step
from roadie.engine.errors import StepFailed
from roadie.engine.parameters import ParameterValues
from roadie.engine.run import Run


# (spec) Parameters
def test_param_rejects_a_name_that_is_not_an_identifier() -> None:
    with pytest.raises(RoadieError, match=r"Param takes an identifier: got 'bad-name'"):
        Param("bad-name")
    with pytest.raises(RoadieError, match=r"Param takes an identifier: got 1"):
        Param(1)  # type: ignore[arg-type]


# (spec) Parameters
def test_param_default_is_a_string_or_none() -> None:
    assert repr(Param("mode")) == "Param('mode')"
    assert repr(Param("mode", default=None)) == "Param('mode', default=None)"
    assert repr(Param("mode", default="prod")) == "Param('mode', default='prod')"
    with pytest.raises(RoadieError, match="Param default takes a string or None: got int"):
        Param("mode", default=1)  # type: ignore[arg-type]


# (spec) Parameters
@pytest.mark.parametrize("fail_on", [None, "api", "job"])
def test_a_parameter_reaches_a_deferred_and_an_immediate_step(
    fail_on: str | None, tmp_path: Path
) -> None:
    seen: list[tuple[str, str | None]] = []

    class Api(Step[str]):
        deferred = True

        mode: Attribute[Param]

        def __init__(self) -> None:
            self.mode = Param("mode", default="dep")

        def build(self, run: Run) -> str:
            seen.append(("api", self.mode.value))
            if fail_on == "api":
                raise RuntimeError("api broke")
            return self.mode.value or ""

    class Job(Step[str]):
        api: Attribute[Api]
        mode: Attribute[Param]

        def __init__(self, api: Api) -> None:
            self.api = api
            self.mode = Param("mode", default="job")

        def build(self, run: Run) -> str:
            seen.append(("job", self.mode.value))
            if fail_on == "job":
                raise RuntimeError("job broke")
            return run[self.api]

    api = Api()
    job = Job(api)
    values = ParameterValues({"mode": "given"}, {})
    if fail_on is None:
        assert execute([api, job], root=tmp_path, parameters=values) == "given"
    else:
        with pytest.raises(StepFailed, match=f"{fail_on}.*broke"):
            execute([api, job], root=tmp_path, parameters=values)
    assert ("api", "given") in seen
    if fail_on != "api":
        assert ("job", "given") in seen
    with pytest.raises(RoadieError, match="has no value outside a run"):
        _ = api.mode.value


# (spec) Parameters
def test_a_parameter_has_no_value_outside_a_run() -> None:
    with pytest.raises(RoadieError, match="parameter mode has no value outside a run"):
        _ = Param("mode", default=None).value


# (spec) Parameters
def test_an_unset_parameter_differs_from_an_empty_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self):\n"
        "        self.mode = Param('mode', default=None)\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'ran').write_text(repr(self.mode.value))\n"
        "        return repr(self.mode.value)\n"
        "item = Note()\n",
    )
    run_cli(["-f", str(tmp_path), "--jobs", "1"])
    assert (tmp_path / "ran").read_text() == "None"
    capsys.readouterr()
    run_cli(["-f", str(tmp_path), "--jobs", "1"])
    assert "reused item" in capsys.readouterr().out
    run_cli(["mode=", "-f", str(tmp_path), "--jobs", "1"])
    output = capsys.readouterr().out
    assert "reused item" not in output
    assert (tmp_path / "ran").read_text() == "''"


# (spec) Parameters
# (spec) Command line
def test_a_parameter_after_a_result_does_not_reach_its_dependencies(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "class Source(Step[str]):\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self):\n"
        "        self.mode = Param('mode', default='dep')\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'dep').write_text(self.mode.value or '')\n"
        "        return self.mode.value or ''\n"
        "class Job(Step[str]):\n"
        "    source: Attribute[Step[str]]\n"
        "    label: Attribute[str]\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self, source, label):\n"
        "        self.source = source\n"
        "        self.label = label\n"
        "        self.mode = Param('mode', default='job')\n"
        "    def build(self, run):\n"
        "        (self.base_dir / self.label).write_text(self.mode.value or '')\n"
        "        return self.mode.value or ''\n"
        "shared = Source()\n"
        "job = Job(shared, 'job')\n"
        "other = Job(shared, 'other')\n",
    )
    run_cli(["-f", str(tmp_path), "job", "mode=own", "--jobs", "1"])
    assert (tmp_path / "dep").read_text() == "dep"
    assert (tmp_path / "job").read_text() == "own"
    run_cli(["mode=wide", "-f", str(tmp_path), "job", "other", "--jobs", "1"])
    assert (tmp_path / "dep").read_text() == "wide"
    assert (tmp_path / "job").read_text() == "wide"
    assert (tmp_path / "other").read_text() == "wide"


# (spec) Command line
def test_parameter_words_fail_before_a_step_builds(tmp_path: Path) -> None:
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self):\n"
        "        self.mode = Param('mode', default=None)\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'ran').write_text('yes')\n"
        "        return 'n'\n"
        "item = Note()\n",
    )
    write_build(
        tmp_path / "plain",
        "class Note(Step[str]):\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'ran').write_text('yes')\n"
        "        return 'n'\n"
        "item = Note()\n",
    )
    with pytest.raises(SystemExit, match="parameter name must be an identifier: 1mode=x"):
        run_cli(["1mode=x", "-f", str(tmp_path), "--dry"])
    with pytest.raises(SystemExit, match="parameter mode is given twice"):
        run_cli(["mode=a", "mode=b", "-f", str(tmp_path), "--dry"])
    with pytest.raises(SystemExit, match="parameter mode is given twice"):
        run_cli(["-f", str(tmp_path), "item", "mode=a", "mode=b", "--dry"])
    with pytest.raises(SystemExit, match=r"unknown parameter extra; the plan takes: mode"):
        run_cli(["extra=1", "-f", str(tmp_path)])
    assert not (tmp_path / "ran").exists()
    with pytest.raises(SystemExit, match=r"unknown parameter extra; the plan takes no parameters"):
        run_cli(["extra=1", "-f", str(tmp_path / "plain"), "--dry"])
    with pytest.raises(SystemExit, match=r"item takes no parameter extra; it takes: mode"):
        run_cli(["-f", str(tmp_path), "item", "extra=1", "--dry"])
    with pytest.raises(SystemExit, match="item takes no parameters"):
        run_cli(["-f", str(tmp_path / "plain"), "item", "extra=1", "--dry"])
    assert not (tmp_path / "plain" / "ran").exists()
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self):\n"
        "        self.mode = Param('mode')\n"
        "    def build(self, run):\n"
        "        (self.base_dir / 'ran').write_text('yes')\n"
        "        return 'n'\n"
        "item = Note()\n",
    )
    with pytest.raises(SystemExit, match="item needs parameter mode"):
        run_cli(["-f", str(tmp_path), "--dry"])
    assert not (tmp_path / "ran").exists()


# (spec) Parameters
# (spec) Dry run output
def test_dry_run_shows_a_param(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_build(
        tmp_path,
        "class Note(Step[str]):\n"
        "    mode: Attribute[Param]\n"
        "    def __init__(self):\n"
        "        self.mode = Param('mode', default=None)\n"
        "    def build(self, run):\n"
        "        return 'n'\n"
        "item = Note()\n",
    )
    run_cli(["-f", str(tmp_path), "--dry"])
    assert "mode=Param('mode', default=None)" in capsys.readouterr().out
