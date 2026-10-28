"""Cargo workspace metadata lives in Roadie."""

import json
import subprocess
from pathlib import Path

import pytest
from forge.environment import repository_root
from roadie import cargo

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


def _cargo_workspace(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    (root / "Cargo.toml").write_text(
        '[workspace]\nresolver = "2"\nmembers = ["core", "app", "other"]\n'
    )
    members = {
        "core": "",
        "app": 'core = { path = "../core" }\n',
        "other": "",
    }
    for name, deps in members.items():
        package = root / name
        (package / "src").mkdir(parents=True)
        (package / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n'
            f"[dependencies]\n{deps}"
        )
        (package / "src" / "lib.rs").write_text(f"// {name}\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)


def test_cargo_package_fingerprint_follows_its_dependency_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    monkeypatch.setattr(cargo, "repository_root", lambda: root)
    package = cargo.CargoPackage("app")
    core = cargo.CargoPackage("core")
    original = package.fingerprint(root)
    core_original = core.fingerprint(root)

    (root / "other" / "src" / "lib.rs").write_text("// other changed\n")
    assert package.fingerprint(root) == original
    (root / "app" / "src" / "lib.rs").write_text("// app changed\n")
    assert core.fingerprint(root) == core_original
    (root / "app" / "src" / "lib.rs").write_text("// app\n")
    assert package.fingerprint(root) == original

    (root / "core" / "src" / "lib.rs").write_text("// core changed\n")
    assert package.fingerprint(root) != original
    with_core = core.fingerprint(root)
    assert with_core != core_original

    (root / "core" / "src" / "extra.rs").write_text("// untracked\n")
    with_untracked = core.fingerprint(root)
    assert with_untracked != with_core

    (root / ".gitignore").write_text("*.ignored\n")
    ignored_base = core.fingerprint(root)
    (root / "core" / "src" / "skip.ignored").write_text("// ignored\n")
    assert core.fingerprint(root) == ignored_base

    lock = root / "Cargo.lock"
    lock.write_text(lock.read_text() + "# marker\n" if lock.exists() else "# marker\n")
    assert core.fingerprint(root) != ignored_base


def test_cargo_package_fingerprint_reads_declared_cargo_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    monkeypatch.setattr(cargo, "repository_root", lambda: root)
    package = cargo.CargoPackage("core")
    monkeypatch.delenv("RUSTFLAGS", raising=False)
    monkeypatch.delenv("CARGO_PROFILE_DEV_DEBUG", raising=False)
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    baseline = package.fingerprint(root)

    monkeypatch.setenv("RUSTFLAGS", "-Dwarnings")
    assert package.fingerprint(root) != baseline
    monkeypatch.delenv("RUSTFLAGS")
    assert package.fingerprint(root) == baseline

    monkeypatch.setenv("CARGO_PROFILE_DEV_DEBUG", "line-tables-only")
    assert package.fingerprint(root) != baseline
    monkeypatch.delenv("CARGO_PROFILE_DEV_DEBUG")
    assert package.fingerprint(root) == baseline

    monkeypatch.setenv("CARGO_TARGET_DIR", str(root / "custom-target"))
    assert package.fingerprint(root) == baseline


def test_cargo_metadata_and_rustc_run_once_per_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    monkeypatch.setattr(cargo, "repository_root", lambda: root)
    calls: list[str] = []
    real_run = subprocess.run

    def spy(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and command[0] in {"cargo", "rustc"}:
            calls.append(str(command[0]))
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cargo.subprocess, "run", spy)
    package = cargo.CargoPackage("core")
    with cargo.cargo_snapshot():
        package.fingerprint(root)
        package.fingerprint(root)

    assert calls.count("cargo") == 1
    assert calls.count("rustc") == 1
