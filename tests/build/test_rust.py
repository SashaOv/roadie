import json
import subprocess
from pathlib import Path

import pytest
from support import FIXTURES, execute, isolate_repo

from roadie.build import CargoPackage, RustBinary, Step
from roadie.build import rust as cargo
from roadie.engine.errors import StepFailed
from roadie.engine.inputs import run_scope


# (spec) Cargo workspace queries
def test_cargo_package_discovery_is_module_neutral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    packages = [
        {
            "name": "core",
            "manifest_path": str(root / "core/Cargo.toml"),
            "dependencies": [],
        },
        {
            "name": "lang",
            "manifest_path": str(root / "lang/Cargo.toml"),
            "dependencies": [],
        },
        {
            "name": "desktop",
            "manifest_path": str(root / "desktop/Cargo.toml"),
            "dependencies": [{"name": "lang"}],
        },
    ]

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == root
        return subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"workspace_root": str(root), "packages": packages}),
        )

    monkeypatch.setattr(cargo.subprocess, "run", fake_run)

    assert cargo.cargo_rust_packages(root) == cargo.CargoWorkspace(
        root,
        (
            cargo.RustPackage("core", Path("core"), ()),
            cargo.RustPackage("lang", Path("lang"), ()),
            cargo.RustPackage("desktop", Path("desktop"), ("lang",)),
        ),
    )


# (spec) Cargo workspace queries
def test_local_dependency_closure_includes_transitive_dependents() -> None:
    packages = (
        cargo.RustPackage("core", Path("services/core"), ()),
        cargo.RustPackage("app", Path("services/app"), ("core",)),
        cargo.RustPackage("other", Path("services/other"), ()),
    )

    assert cargo.select_rust_packages(
        packages, (Path("services/core/src/lib.rs"),)
    ) == ("core", "app")


# (spec) Cargo workspace queries
def test_local_dependency_closure_ignores_unrelated_changes() -> None:
    packages = (
        cargo.RustPackage("core", Path("services/core"), ()),
        cargo.RustPackage("app", Path("services/app"), ("core",)),
    )

    assert cargo.select_rust_packages(packages, (Path("docs/dev.md"),)) == ()


def _installed_toolchain() -> str | None:
    try:
        listed = subprocess.run(
            ["rustup", "toolchain", "list"], check=True, capture_output=True, text=True
        )
    except FileNotFoundError:
        return None
    names = listed.stdout.split()
    return names[0] if names else None


def _cargo_workspace(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    # Pin a toolchain as a real workspace does: build hosts may install only a
    # pinned toolchain and configure no rustup default.
    toolchain = _installed_toolchain()
    if toolchain is not None:
        (root / "rust-toolchain.toml").write_text(
            f'[toolchain]\nchannel = "{toolchain}"\n'
        )
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


# (spec) Cargo packages
def test_cargo_package_fingerprint_follows_its_dependency_closure(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
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


# (spec) Cargo packages
def test_cargo_package_fingerprint_reads_declared_cargo_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
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


# (spec) Cargo packages
def test_cargo_metadata_and_rustc_run_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    calls: list[str] = []
    real_run = subprocess.run

    def spy(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and command[0] in {"cargo", "rustc"}:
            calls.append(str(command[0]))
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cargo.subprocess, "run", spy)
    package = cargo.CargoPackage("core")
    with run_scope():
        package.fingerprint(root / "app")
        package.fingerprint(root / "core")

    assert calls.count("cargo") == 1
    assert calls.count("rustc") == 1

    # The next run sees a fresh workspace and toolchain.
    with run_scope():
        package.fingerprint(root / "app")

    assert calls.count("cargo") == 2
    assert calls.count("rustc") == 2


# (spec) Cargo packages
def test_cargo_package_in_a_subdirectory_finds_the_workspace_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _cargo_workspace(root)
    workspace = cargo.cargo_rust_packages(root / "app")
    assert workspace.root == root
    package = cargo.CargoPackage("app")
    fingerprint = package.fingerprint(root / "app")
    assert package.fingerprint(root / "core") == fingerprint
    assert package.fingerprint(root) == fingerprint


# (spec) Step kinds
@pytest.mark.parametrize(
    ("package", "fixture", "executable"),
    [
        (
            "api_server",
            "cargo-build-workspace.jsonl",
            "/tmp/roadie-cargo-ws/target/debug/api-server",
        ),
        (
            "sample",
            "cargo-build-sample.jsonl",
            "/tmp/roadie-cargo-sample/sample/target/debug/sample",
        ),
    ],
)
def test_rust_binary_picks_executable_from_captured_cargo_json(
    package: str, fixture: str, executable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, ...]] = []

    def command(
        self: Step[object], *args: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(
            args, 0, (FIXTURES / fixture).read_text(), ""
        )

    monkeypatch.setattr(Step, "_cmd", command)
    monkeypatch.setattr(
        "roadie.build.rust.CargoPackage.fingerprint",
        lambda self, base_dir: f"package:{self.name}",
    )
    source = Path(executable)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"binary")
    source.chmod(0o755)
    step = RustBinary(CargoPackage(package))
    copied = execute([step])
    assert copied != source
    assert copied.read_bytes() == b"binary"
    assert copied.stat().st_mode == source.stat().st_mode
    assert captured == [
        ("cargo", "build", "--locked", "-p", package, "--message-format=json")
    ]


# (spec) Step kinds
def test_rust_binary_rejects_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roadie.build.rust.CargoPackage.fingerprint",
        lambda self, base_dir: f"package:{self.name}",
    )
    monkeypatch.setattr(
        Step, "_cmd", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", "")
    )
    with pytest.raises(StepFailed, match="produced no executable"):
        execute([RustBinary(CargoPackage("missing"))])


# (spec) Step kinds
# (spec) Reusing a step
def test_rust_binary_is_rebuilt_when_its_recorded_copy_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    isolate_repo(monkeypatch, root)
    source = root / "compiler-output"
    source.write_bytes(b"v1")
    source.chmod(0o755)
    message = (
        '{"reason":"compiler-artifact","package_id":"path+file:///tmp/app#app@0.1.0",'
        f'"target":{{"kind":["bin"]}},"executable":"{source}"}}\n'
    )
    calls: list[tuple[str, ...]] = []

    def command(self: Step[object], *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, message, "")

    monkeypatch.setattr(Step, "_cmd", command)
    monkeypatch.setattr("roadie.build.rust.CargoPackage.fingerprint", lambda self, base_dir: "package:app")
    step = RustBinary(CargoPackage("app"))
    step.name = step.variable = "app"
    first = execute([step])
    assert first.read_bytes() == b"v1"
    execute([step])
    assert len(calls) == 1
    first.write_bytes(b"tampered")
    execute([step])
    assert len(calls) == 2
    assert first.read_bytes() == b"v1"
