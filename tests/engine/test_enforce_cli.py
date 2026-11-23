"""The enforcement scenario, run through the roadie command."""

from pathlib import Path

from support import git_init, run_roadie, tracing_only, write_build


def _source(outside: Path, declare_undeclared: bool = False) -> str:
    declared = 'Files("declared.txt", "undeclared.txt")' if declare_undeclared else 'Files("declared.txt")'
    script_a = (
        "from pathlib import Path\n"
        "Path('declared.txt').read_text()\n"
        "Path('undeclared.txt').read_text()\n"
        "Path('ignored.txt').read_text()\n"
        f"Path({str(outside)!r}).read_text()\n"
        "Path('output.txt').write_text('x')\n"
        "Path('output.txt').read_text()\n"
        "Path('gen.txt').read_text()\n"
    )
    script_b = "from pathlib import Path\nPath('declared.txt').read_text()\n"
    return f"""\
import sys
from pathlib import Path

class Gen(File):
    def build(self, run: Run) -> Path:
        path = self.base_dir / "gen.txt"
        path.write_text("gen")
        return path

gen = Gen()
a = Check(sys.executable, "-c", {script_a!r}, inputs=({declared}, gen))
b = Check(sys.executable, "-c", {script_b!r}, inputs=(Files("declared.txt"),))
"""


# (spec) Enforcing declared inputs
@tracing_only
def test_the_enforcement_scenario_runs_through_the_roadie_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside")
    git_init(root)
    (root / "declared.txt").write_text("declared")
    (root / "undeclared.txt").write_text("undeclared")
    (root / "ignored.txt").write_text("ignored")
    (root / ".gitignore").write_text("ignored.txt\noutput.txt\n")
    write_build(root, _source(outside))

    enforced = run_roadie(root, "--enforce", "a", "b")
    assert enforced.returncode == 1, enforced.stderr
    assert "a reads files it does not declare: undeclared.txt" in enforced.stdout
    assert "build b = " in enforced.stdout

    plain = run_roadie(root, "a", "b")
    assert plain.returncode == 0, plain.stderr

    write_build(root, _source(outside, declare_undeclared=True))
    fixed = run_roadie(root, "--enforce", "a", "b")
    assert fixed.returncode == 0, fixed.stderr + fixed.stdout
    assert "reads files it does not declare" not in fixed.stdout

    again = run_roadie(root, "--enforce", "a", "b")
    assert again.returncode == 0, again.stderr + again.stdout
    assert "reused" not in again.stdout
    assert "build gen = " in again.stdout
    assert "build a = " in again.stdout
    assert "build b = " in again.stdout
