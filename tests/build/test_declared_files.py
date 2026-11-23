"""Verify each input names the files its fingerprint reads."""

import json
import subprocess
from pathlib import Path

from support import git_init

from roadie.build import CargoPackage, Files, NodeEnv, PnpmPackage
from roadie.engine.inputs import Input, digest, run_scope
from roadie.system import prepare_command


def _resolved(paths: set[Path]) -> set[Path]:
    return {path.resolve() for path in paths}


class _Bare(Input):
    def fingerprint(self, base_dir: Path) -> str:
        return digest("bare")


# (spec) Input fingerprints
def test_a_default_input_declares_no_files(tmp_path: Path) -> None:
    assert _Bare().declared_files(tmp_path) == set()


# (spec) Input fingerprints
# (spec) Files input
def test_files_declares_named_files_directories_and_a_missing_path(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "b.txt").write_text("b")
    (tmp_path / "declared.txt").write_text("d")
    files = Files("src", "declared.txt", "missing.txt")
    assert _resolved(files.declared_files(tmp_path)) == _resolved(
        {source / "a.txt", source / "b.txt", tmp_path / "declared.txt", tmp_path / "missing.txt"}
    )
    # Each declared file changes the fingerprint; an undeclared one does not.
    baseline = files.fingerprint(tmp_path)
    (source / "a.txt").write_text("a2")
    assert files.fingerprint(tmp_path) != baseline


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pnpm_workspace(root: Path) -> Path:
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    _write_json(root / "package.json", {"private": True})
    _write_json(root / "packages/core/package.json", {"name": "core", "version": "1.0.0"})
    _write_json(
        root / "packages/app/package.json",
        {"name": "app", "version": "1.0.0", "dependencies": {"core": "workspace:*"}},
    )
    subprocess.run(
        prepare_command(["pnpm", "install", "--offline"]),
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


# (spec) Input fingerprints
# (spec) Node environment
def test_node_env_declares_manifests_and_root_files(tmp_path: Path) -> None:
    root = _pnpm_workspace(tmp_path)
    with run_scope():
        declared = NodeEnv().declared_files(root)
    assert root / "packages/app/package.json" in _resolved(declared)
    assert root / "packages/core/package.json" in _resolved(declared)
    assert root / "pnpm-workspace.yaml" in _resolved(declared)


def _git_pnpm_workspace(root: Path) -> Path:
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    _write_json(root / "package.json", {"private": True})
    needs = {"app": "core", "core": "base", "base": None, "other": None}
    for name, needed in needs.items():
        manifest: dict[str, object] = {"name": name, "version": "1.0.0"}
        if needed is not None:
            manifest["dependencies"] = {needed: "workspace:*"}
        _write_json(root / f"packages/{name}/package.json", manifest)
        (root / f"packages/{name}/index.js").write_text(f"// {name}\n")
    git_init(root)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    return root


# (spec) Input fingerprints
# (spec) Pnpm packages
def test_pnpm_package_declares_its_dependency_closure(tmp_path: Path) -> None:
    root = _git_pnpm_workspace(tmp_path)
    with run_scope():
        declared = _resolved(PnpmPackage("app").declared_files(root))
    assert root / "packages/app/index.js" in declared
    assert root / "packages/core/index.js" in declared
    assert root / "packages/base/index.js" in declared
    assert root / "packages/other/index.js" not in declared
    assert root / "packages/app/package.json" in declared


def _cargo_workspace(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / "Cargo.toml").write_text(
        '[workspace]\nresolver = "2"\nmembers = ["core", "app", "other"]\n'
    )
    members = {"core": "", "app": 'core = { path = "../core" }\n', "other": ""}
    for name, deps in members.items():
        package = root / name
        (package / "src").mkdir(parents=True)
        (package / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n{deps}'
        )
        (package / "src" / "lib.rs").write_text(f"// {name}\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)


# (spec) Input fingerprints
# (spec) Cargo packages
def test_cargo_package_declares_its_dependency_closure(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    with run_scope():
        declared = _resolved(CargoPackage("app").declared_files(root))
    assert root / "app/src/lib.rs" in declared
    assert root / "app/Cargo.toml" in declared
    assert root / "core/src/lib.rs" in declared
    assert root / "other/src/lib.rs" not in declared
    assert root / "Cargo.toml" in declared
