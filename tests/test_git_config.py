import os
import subprocess
from pathlib import Path


# (spec) Command line
def test_git_lookups_ignore_the_machine_config() -> None:
    found = subprocess.run(
        ["git", "config", "--show-origin", "--get", "init.defaultBranch"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode != 0, found.stdout
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    global_config = Path(os.environ["GIT_CONFIG_GLOBAL"])
    assert global_config.is_file()
    assert global_config.read_text() == ""
