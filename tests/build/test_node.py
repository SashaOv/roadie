import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from support import git_init

from roadie.build import NodeEnv, PnpmPackage, node
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import run_scope
from roadie.system import prepare_command

_REAL_RUN = subprocess.run


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _workspace(root: Path) -> Path:
    """A pnpm workspace in which `app` depends on `core`, installed offline."""
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    _write_json(root / "package.json", {"private": True})
    _write_json(root / "packages/core/package.json", {"name": "core", "version": "1.0.0"})
    _write_json(
        root / "packages/app/package.json",
        {"name": "app", "version": "1.0.0", "dependencies": {"core": "workspace:*"}},
    )
    _REAL_RUN(
        prepare_command(["pnpm", "install", "--offline"]),
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def _fingerprint(base_dir: Path) -> str:
    with run_scope():
        return NodeEnv().fingerprint(base_dir)


def _command_text(args: object) -> str:
    return args if isinstance(args, str) else " ".join(args)  # type: ignore[arg-type]


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    replace: Callable[[str, subprocess.CompletedProcess[str]], subprocess.CompletedProcess[str]],
) -> list[str]:
    """Run the real program, let `replace` change its result, and log each command."""
    commands: list[str] = []

    def run(args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        text = _command_text(args)
        commands.append(text)
        return replace(text, _REAL_RUN(args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(node.subprocess, "run", run)
    return commands


# (spec) Node environment
def test_node_env_covers_every_workspace_project_manifest(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    baseline = _fingerprint(root)
    assert _fingerprint(root / "packages/app") == baseline

    _write_json(root / "packages/core/package.json", {"name": "core", "version": "1.0.1"})
    assert _fingerprint(root) != baseline


# (spec) Node environment
def test_a_single_package_counts_its_own_manifest_and_not_a_nested_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(tmp_path / "package.json", {"name": "solo", "version": "1.0.0"})
    _write_json(tmp_path / "tools/package.json", {"name": "tools", "version": "1.0.0"})
    commands = _patch_run(monkeypatch, lambda _text, result: result)
    baseline = _fingerprint(tmp_path)

    _write_json(tmp_path / "tools/package.json", {"name": "tools", "version": "2.0.0"})
    assert _fingerprint(tmp_path) == baseline
    _write_json(tmp_path / "package.json", {"name": "solo", "version": "2.0.0"})
    assert _fingerprint(tmp_path) != baseline
    assert not [command for command in commands if "--depth" in command]


# (spec) Node environment
@pytest.mark.parametrize(
    "relative",
    ["pnpm-lock.yaml", "pnpm-workspace.yaml", ".npmrc", "node_modules/.pnpm/lock.yaml"],
)
def test_node_env_follows_each_workspace_root_file(tmp_path: Path, relative: str) -> None:
    root = _workspace(tmp_path)
    baseline = _fingerprint(root)
    path = root / relative
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + "# changed\n")
    assert _fingerprint(root) != baseline


# (spec) Node environment
@pytest.mark.parametrize(
    ("name", "value"),
    [("NODE_ENV", "production"), ("NODE_OPTIONS", "--max-old-space-size=4096")],
)
def test_node_env_follows_its_environment_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.delenv(name, raising=False)
    unset = _fingerprint(root)
    monkeypatch.setenv(name, "")
    empty = _fingerprint(root)
    monkeypatch.setenv(name, value)
    assert len({unset, empty, _fingerprint(root)}) == 3


# (spec) Node environment
def test_node_and_pnpm_run_once_per_workspace_in_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    commands = _patch_run(monkeypatch, lambda _text, result: result)
    with run_scope():
        NodeEnv().fingerprint(root)
        NodeEnv().fingerprint(root / "packages/app")
    assert len(commands) == 3
    assert len([command for command in commands if "--depth" in command]) == 1
    _fingerprint(root)
    assert len(commands) == 6


# (spec) Node environment
def test_node_env_without_a_package_json_fails(tmp_path: Path) -> None:
    with pytest.raises(RoadieError) as caught:
        _fingerprint(tmp_path)
    assert str(caught.value) == f"no package.json at or above {tmp_path}"


# (spec) Node environment
def test_a_different_node_version_changes_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    baseline = _fingerprint(root)

    def other_node(_text: str, result: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
        if result.stdout.startswith("v"):
            result.stdout = "v0.0.1\n"
        return result

    _patch_run(monkeypatch, other_node)
    assert _fingerprint(root) != baseline


# (spec) Node environment
def test_a_program_that_cannot_start_fails_with_the_cmd_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)

    def missing(args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "node")

    monkeypatch.setattr(node.subprocess, "run", missing)
    with pytest.raises(RoadieError) as caught:
        _fingerprint(root)
    assert str(caught.value) == "cannot run node: [Errno 2] No such file or directory"


# (spec) Node environment
def test_a_failing_pnpm_ls_fails_with_its_standard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)

    def failing_ls(text: str, result: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
        if "--depth" in text:
            return subprocess.CompletedProcess(result.args, 1, "", "ERR_PNPM_BROKEN boom\n")
        return result

    _patch_run(monkeypatch, failing_ls)
    with pytest.raises(RoadieError) as caught:
        _fingerprint(root)
    assert str(caught.value) == "pnpm ls failed: ERR_PNPM_BROKEN boom"


def _git_workspace(root: Path, field: str) -> Path:
    """A git pnpm workspace: `app` needs `core` needs `base` through `field`; `other` stands alone."""
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    _write_json(root / "package.json", {"private": True})
    needs = {"app": "core", "core": "base", "base": None, "other": None}
    for name, needed in needs.items():
        manifest: dict[str, object] = {"name": name, "version": "1.0.0"}
        if needed is not None:
            manifest[field] = {needed: "workspace:*"}
        _write_json(root / f"packages/{name}/package.json", manifest)
        (root / f"packages/{name}/index.js").write_text(f"// {name}\n")
    git_init(root)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return root


def _package_fingerprint(name: str, base_dir: Path) -> str:
    with run_scope():
        return PnpmPackage(name).fingerprint(base_dir)


# (spec) Pnpm packages
@pytest.mark.parametrize(
    "field",
    ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"],
)
def test_pnpm_package_follows_its_dependency_closure(tmp_path: Path, field: str) -> None:
    root = _git_workspace(tmp_path, field)
    app = _package_fingerprint("app", root)
    core = _package_fingerprint("core", root)

    (root / "packages/other/index.js").write_text("// other changed\n")
    assert _package_fingerprint("app", root) == app
    (root / "packages/app/index.js").write_text("// app changed\n")
    assert _package_fingerprint("core", root) == core
    assert _package_fingerprint("app", root) != app

    app = _package_fingerprint("app", root)
    (root / "packages/base/index.js").write_text("// base changed\n")
    assert _package_fingerprint("app", root) != app
    assert _package_fingerprint("core", root) != core


# (spec) Pnpm packages
def test_pnpm_package_follows_the_node_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _git_workspace(tmp_path, "dependencies")
    monkeypatch.delenv("NODE_ENV", raising=False)
    baseline = _package_fingerprint("other", root)
    monkeypatch.setenv("NODE_ENV", "production")
    assert _package_fingerprint("other", root) != baseline


# (spec) Pnpm packages
def test_pnpm_package_counts_untracked_files_and_not_ignored_ones(tmp_path: Path) -> None:
    root = _git_workspace(tmp_path, "dependencies")
    baseline = _package_fingerprint("core", root)
    (root / "packages/base/extra.js").write_text("// untracked\n")
    with_untracked = _package_fingerprint("core", root)
    assert with_untracked != baseline

    (root / ".gitignore").write_text("*.ignored\n")
    (root / "packages/base/skip.ignored").write_text("// ignored\n")
    ignored_base = _package_fingerprint("core", root)
    (root / "packages/base/skip.ignored").write_text("// ignored, changed\n")
    assert _package_fingerprint("core", root) == ignored_base


# (spec) Pnpm packages
def test_a_pnpm_package_missing_from_the_workspace_fails(tmp_path: Path) -> None:
    root = _git_workspace(tmp_path, "dependencies")
    with pytest.raises(RoadieError) as caught:
        _package_fingerprint("missing", root)
    assert str(caught.value) == "pnpm package 'missing' is not in the workspace"
