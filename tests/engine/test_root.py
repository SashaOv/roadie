from pathlib import Path

import pytest
from support import EMITTER, execute, git_init, write_build

from roadie.build import RoadieError
from roadie.engine.graph import load
from roadie.engine.root import project_root


def test_project_root_outside_load_and_execute_is_an_error() -> None:
    with pytest.raises(
        RoadieError,
        match="no project root: run steps through roadie's load and execute",
    ):
        project_root()


# (spec) Project root
# (spec) Records
def test_subdirectory_of_a_work_tree_records_under_the_top_level(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git_init(repo)
    write_build(repo / "services", EMITTER)
    graph = load(repo / "services")
    assert graph.root == repo.resolve()
    execute(graph.plan(), root=graph.root)
    assert (repo / "target" / "roadie" / "services" / "api.txt").is_file()
    assert not (repo / "services" / "target").exists()


# (spec) Project root
# (spec) Records
def test_build_outside_a_work_tree_records_under_its_own_directory(tmp_path: Path) -> None:
    write_build(tmp_path, EMITTER)
    graph = load(tmp_path)
    assert graph.root == tmp_path.resolve()
    execute(graph.plan(), root=graph.root)
    assert (tmp_path / "target" / "roadie" / "api.txt").read_text() == "v1"
    assert not (tmp_path / "services").exists()


# (spec) Project root
def test_included_file_shares_the_including_files_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    git_init(repo)
    write_build(other, EMITTER)
    write_build(repo / "app", 'other = include("../../other")\nresult = other.api\n')
    graph = load(repo / "app")
    assert graph.root == repo.resolve()
    execute(graph.plan("result"), root=graph.root)
    assert not (other / "target").exists()
    recorded = list((repo / "target" / "roadie" / "_outside").rglob("api.txt"))
    assert recorded
    assert recorded[0].read_text() == "v1"
