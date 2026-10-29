from dataclasses import dataclass
from pathlib import Path

import pytest
from support import Value

from roadie.build import Files, RoadieError, Step
from roadie.engine.fingerprint import fingerprint
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
