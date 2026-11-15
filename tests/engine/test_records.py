import errno
from pathlib import Path

import pytest

from roadie.engine.inputs import run_scope
from roadie.engine.records import load_record_at


# (spec) Records
@pytest.mark.parametrize(
    ("content", "reason"),
    [("not json", "invalid JSON"), ("{}", "not a Roadie record"),
     ('{"version":2,"entries":{"bad":{}}}', "not a Roadie record"),
     (None, "Permission denied")],
)
def test_damaged_record_warning_is_once_per_run(
    content: str | None, reason: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "record.json"
    path.write_text(content or "{}")
    if content is None:
        real_read = Path.read_text

        def read(self: Path, *args, **kwargs) -> str:
            if self == path:
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
    for _ in range(2):
        with run_scope():
            assert load_record_at(path) == {}
            assert load_record_at(path) == {}
        assert capsys.readouterr().out.splitlines() == [
            f"ignoring unreadable record {path}: {reason}"
        ]
