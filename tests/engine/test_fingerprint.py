import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import Value, execute, isolate_repo, write_build

import roadie.engine.fingerprint as fingerprints
from roadie.build import Files, RoadieError, Step
from roadie.engine.fingerprint import fingerprint
from roadie.engine.graph import load
from roadie.engine.root import use_project_root
from roadie.engine.run import Run


# (spec) Dependencies and attributes
def test_plain_attribute_of_unsupported_type_is_an_error() -> None:
    @dataclass(eq=False)
    class Custom(Step[None]):
        value: object

        def build(self, run: Run) -> None:
            pass

    step = Custom({"bad": 1})
    step.name = "custom"
    with pytest.raises(RoadieError, match="custom attribute value must be"):
        fingerprint(step)


# (spec) Step fingerprints
# (spec) Files input
def test_step_fingerprint_covers_files_dependency_outputs_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    with use_project_root(root):
        bundle = root / "dist"
        bundle.mkdir()
        (bundle / "app.js").write_text("one")
        outside = root / "outside.txt"
        outside.write_text("one")
        (bundle / "link.js").symlink_to(outside)

        @dataclass(eq=False)
        class Bundle(Step[None]):
            files: Files
            source: Step[str]

            def build(self, run: Run) -> None:
                pass

        source = Value("built")
        step = Bundle(Files("dist"), source)
        outputs = {source: "a" * 64}
        original = fingerprint(step, outputs=outputs)
        (bundle / "app.js").write_text("two")
        edited = fingerprint(step, outputs=outputs)
        assert edited != original
        outside.write_text("two")
        followed = fingerprint(step, outputs=outputs)
        assert followed != edited
        assert fingerprint(step, outputs={source: "b" * 64}) != followed
        assert fingerprint(step, outputs=outputs, env={"PORT": "1"}) != fingerprint(step, outputs=outputs, env={})
        step.env_values = {"PORT": "9"}
        assert fingerprint(step, outputs=outputs) == fingerprint(step, outputs=outputs, env={"PORT": "9"})
        step.env_values = {"PORT": "10"}
        assert fingerprint(step, outputs=outputs) != fingerprint(step, outputs=outputs, env={"PORT": "9"})

        missing = Files("absent")
        before = missing.fingerprint(root)
        (root / "absent").write_text("created")
        assert missing.fingerprint(root) != before


# (spec) Step fingerprints
def test_fingerprint_does_not_depend_on_the_checkout_location(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass
from pathlib import Path

@dataclass(eq=False)
class Note(Step[None]):
    source: Path
    extras: tuple[Path, ...]
    def build(self, run):
        return None

here = Path(__file__).parent
item = Note((here / "src/main.py").resolve(), ((here / "src/lib.py").resolve(),))
"""

    def digest(name: str) -> str:
        root = tmp_path / name
        write_build(root, source)
        graph = load(root)
        with use_project_root(graph.root):
            return fingerprint(graph.result("item"))

    assert digest("first") == digest("second")

    root = tmp_path / "first"
    with use_project_root(root.resolve()):
        inside = fingerprint(load(root).result("item"))
        outside = load(root).result("item")
        outside.source = Path("/tmp/roadie-outside.py")
        moved = fingerprint(outside)
    assert inside != moved


# (spec) Step fingerprints
def test_code_hash_keys_sources_by_module_name(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[bytes] = []
    real = hashlib.sha256

    class Spy:
        def __init__(self, data: bytes = b"") -> None:
            captured.append(data)
            self._parts = [data]

        def update(self, data: bytes) -> None:
            captured.append(data)
            self._parts.append(data)

        def hexdigest(self) -> str:
            return real(b"".join(self._parts)).hexdigest()

    monkeypatch.setattr(fingerprints.hashlib, "sha256", Spy)

    class Note(Step[None]):
        def build(self, run: Run) -> None:
            return None

    fingerprint(Note())
    joined = b"".join(captured)
    assert b"roadie.engine.step\0" in joined
    step_file = Path(fingerprints.__file__).resolve().parent / "step.py"
    assert str(step_file).encode() not in joined


# (spec) Dependencies and attributes
def test_a_reserved_name_fails_when_the_step_is_fingerprinted() -> None:
    @dataclass(eq=False)
    class Named(Step[str]):
        name: str

        def build(self, run: Run) -> str:
            return self.name

    named = Named("given")
    with pytest.raises(RoadieError, match="given attribute name is reserved by Roadie"):
        fingerprint(named)

    @dataclass(eq=False)
    class Logged(Step[str]):
        report: str

        def build(self, run: Run) -> str:
            return self.report

    logged = Logged("out")
    logged.name = "logged"
    with pytest.raises(RoadieError, match="logged attribute report is reserved by Roadie"):
        fingerprint(logged)


# (spec) Dependencies and attributes
def test_a_reserved_name_fails_when_a_dry_run_shows_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_repo(monkeypatch, tmp_path)

    @dataclass(eq=False)
    class Logged(Step[str]):
        report: str

        def build(self, run: Run) -> str:
            return self.report

    logged = Logged("out")
    logged.name = logged.variable = "logged"
    with pytest.raises(RoadieError, match="logged attribute report is reserved by Roadie"):
        execute([logged], dry=True)


# (spec) Dependencies and attributes
def test_overriding_a_class_attribute_is_not_a_reserved_name() -> None:
    class Once(Step[str]):
        reusable = False

        def build(self, run: Run) -> str:
            return "v"

    fingerprint(Once())


# (spec) Step fingerprints
def test_editing_a_build_file_changes_the_fingerprint_of_its_class(tmp_path: Path) -> None:
    source = "class Note(Step[str]):\n    def build(self, run):\n        return 'v'\nitem = Note()\n"
    write_build(tmp_path, source)

    def digest() -> str:
        graph = load(tmp_path)
        with use_project_root(graph.root):
            return fingerprint(graph.result("item"))

    before = digest()
    write_build(tmp_path, source + "# edited\n")
    assert digest() != before


# (spec) Step fingerprints
def test_editing_a_build_file_leaves_an_imported_class_unchanged(tmp_path: Path) -> None:
    source = "item = Check('true')\n"
    write_build(tmp_path, source)

    def digest() -> str:
        graph = load(tmp_path)
        with use_project_root(graph.root):
            return fingerprint(graph.result("item"))

    before = digest()
    write_build(tmp_path, source + "# edited\n")
    assert digest() == before
