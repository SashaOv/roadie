"""The pnpm scenario, run through the roadie command."""

import json
import os
import subprocess
import sys
from pathlib import Path

from support import git_init, run_roadie, write_build

from roadie.system import prepare_command

_PACKAGES = {"app": {"core": "workspace:*"}, "core": {}, "other": {}}
_CHECKS = ("check_app", "check_core", "check_other")


def _workspace(root: Path) -> None:
    """A git project: `app` depends on `core`, `other` stands alone, installed offline."""
    git_init(root)
    (root / ".gitignore").write_text("node_modules/\n")
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    (root / "package.json").write_text(json.dumps({"private": True}))
    for name, dependencies in _PACKAGES.items():
        package = root / "packages" / name
        package.mkdir(parents=True)
        manifest = {"name": name, "version": "1.0.0", "dependencies": dependencies}
        (package / "package.json").write_text(json.dumps(manifest))
        (package / "index.js").write_text(f"// {name}\n")
    subprocess.run(
        prepare_command(["pnpm", "install", "--offline"]),
        cwd=root,
        check=True,
        capture_output=True,
    )
    assert (root / "pnpm-lock.yaml").is_file()
    checks = "\n".join(
        f"check_{name} = Check({sys.executable!r}, '-c', '', inputs=PnpmPackage({name!r}))"
        for name in _PACKAGES
    )
    write_build(root, checks + "\n")


def _outcomes(root: Path, report: Path, env: dict[str, str]) -> dict[str, str]:
    """Run every check with a report, and return each step's outcome in that run."""
    completed = run_roadie(root, "--report", str(report), *_CHECKS, env=env)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = [json.loads(line) for line in report.read_text().splitlines()]
    last_start = max(i for i, event in enumerate(events) if event["event"] == "run-start")
    return {
        event["step"]: event["outcome"]
        for event in events[last_start:]
        if event["event"] == "step-end"
    }


# (spec) Pnpm packages
def test_the_pnpm_scenario_runs_through_the_roadie_command(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    report = root / "report.json"
    _workspace(root)
    env = {key: value for key, value in os.environ.items() if key != "NODE_ENV"}
    every = dict.fromkeys(_CHECKS, "built")

    assert _outcomes(root, report, env) == every
    assert _outcomes(root, report, env) == dict.fromkeys(_CHECKS, "reused")

    (root / "packages/core/index.js").write_text("// core changed\n")
    assert _outcomes(root, report, env) == {
        "check_app": "built",
        "check_core": "built",
        "check_other": "reused",
    }

    lockfile = root / "pnpm-lock.yaml"
    lockfile.write_text(lockfile.read_text() + "# changed\n")
    assert _outcomes(root, report, env) == every

    assert _outcomes(root, report, {**env, "NODE_ENV": "production"}) == every
