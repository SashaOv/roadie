"""Cargo workspace metadata lives in Wright."""

import json
import subprocess
from pathlib import Path

import pytest
from forge.environment import repository_root

import wright.cargo as cargo

REPO_ROOT = repository_root()


def test_cargo_package_discovery_is_module_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages = [
        {
            "name": "jammer_core",
            "manifest_path": str(REPO_ROOT / "jammer/core/Cargo.toml"),
            "dependencies": [],
        },
        {
            "name": "niva_lang",
            "manifest_path": str(REPO_ROOT / "notation/lang/Cargo.toml"),
            "dependencies": [],
        },
        {
            "name": "niva_desktop",
            "manifest_path": str(REPO_ROOT / "desktop/src-tauri/Cargo.toml"),
            "dependencies": [{"name": "niva_lang"}],
        },
    ]
    monkeypatch.setattr(
        cargo.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"packages": packages}),
        ),
    )

    assert cargo.cargo_rust_packages() == (
        cargo.RustPackage("jammer_core", Path("jammer/core"), ()),
        cargo.RustPackage("niva_lang", Path("notation/lang"), ()),
        cargo.RustPackage(
            "niva_desktop",
            Path("desktop/src-tauri"),
            ("niva_lang",),
        ),
    )


def test_local_dependency_closure_includes_transitive_dependents() -> None:
    packages = (
        cargo.RustPackage("core", Path("jammer/core"), ()),
        cargo.RustPackage("app", Path("jammer/app"), ("core",)),
        cargo.RustPackage("other", Path("jammer/other"), ()),
    )

    assert cargo.select_rust_packages(
        packages, (Path("jammer/core/src/lib.rs"),)
    ) == ("core", "app")


def test_local_dependency_closure_ignores_unrelated_changes() -> None:
    packages = (
        cargo.RustPackage("core", Path("jammer/core"), ()),
        cargo.RustPackage("app", Path("jammer/app"), ("core",)),
    )

    assert cargo.select_rust_packages(packages, (Path("docs/dev.md"),)) == ()


def test_local_dependency_closure_covers_notation_and_desktop() -> None:
    packages = cargo.cargo_rust_packages()

    notation_selected = set(
        cargo.select_rust_packages(
            packages,
            (Path("notation/lang/src/lib.rs"),),
        )
    )
    desktop_selected = cargo.select_rust_packages(
        packages,
        (Path("desktop/src-tauri/src/lib.rs"),),
    )

    assert {"niva_lang", "niva_renderer", "niva_cli"} <= notation_selected
    assert desktop_selected == ("editorsapp",)
