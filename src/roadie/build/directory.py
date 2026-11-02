"""A step that builds a directory."""

from pathlib import Path

from roadie.build.path import content_digest, link_target, walk_files
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import digest
from roadie.engine.root import from_stored, to_stored
from roadie.engine.step import Step


def _files(directory: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in walk_files(directory):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            entries.append({"path": relative, "target": link_target(path)})
        elif path.is_file():
            entries.append({"path": relative, "sha256": content_digest(path)})
    entries.sort(key=lambda item: item["path"])
    return entries


# (spec) Step kinds
class Directory(Step[Path]):
    """A step that builds a directory; its value is the directory's path.

    The record keeps the path, relative to the project root when inside it,
    and every file under the directory: a regular file by its relative path
    and SHA-256, a symbolic link by its target text. Symbolic links are not
    followed, so a link to a directory is not descended and a retargeted
    link changes the record. Ignored files count; empty directories do not.
    The step is reused only while the directory still holds exactly that
    content. A file that cannot be read fails with `cannot read <path>:
    <reason>`. A build that returns a path that is not a directory fails
    with `<step> built <path>, which is not a directory`. The output
    fingerprint is a SHA-256 of the file record, so a rebuild with identical
    content leaves its consumers reusable.
    """

    def encode(self, value: Path) -> object:
        return {"path": to_stored(value), "files": self._listing(value)}

    def matches(self, recorded: object) -> bool:
        if not isinstance(recorded, dict):
            return False
        stored, files = recorded.get("path"), recorded.get("files")
        if not isinstance(stored, str) or not isinstance(files, list):
            return False
        path = from_stored(stored)
        if not path.is_dir():
            return False
        return _files(path) == files

    def decode(self, recorded: object) -> Path:
        assert isinstance(recorded, dict)
        return from_stored(str(recorded["path"]))

    def output_fingerprint(self, fingerprint: str, value: Path) -> str:
        return digest(self._listing(value))

    def _listing(self, value: Path) -> list[dict[str, str]]:
        if not value.is_dir():
            label = self.name or type(self).__name__
            raise RoadieError(f"{label} built {value}, which is not a directory")
        return _files(value)
