"""A step that builds a directory."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from roadie.build.generated import same_as_git_stores, tracked, tracked_files
from roadie.build.path import content_digest, link_target, walk_files
from roadie.engine.errors import RoadieError
from roadie.engine.inputs import digest
from roadie.engine.root import from_stored, to_stored
from roadie.engine.step import Step

if TYPE_CHECKING:
    from roadie.engine.run import Run


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


def _matches(destination: Path, built: Path) -> bool:
    if built.is_symlink():
        return destination.is_symlink() and os.readlink(destination) == os.readlink(built)
    if destination.is_symlink() or not destination.is_file():
        return False
    if destination.read_bytes() == built.read_bytes():
        return True
    return tracked(destination) and same_as_git_stores(destination, built)


def _write(destination: Path, built: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    if built.is_symlink():
        if destination.exists():
            destination.unlink()
        destination.symlink_to(os.readlink(built), target_is_directory=built.is_dir())
        return
    destination.write_bytes(built.read_bytes())


# (spec) Step kinds
# (spec) Directory step
@dataclass(eq=False, kw_only=True)
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

    With `output`, the built directory is published to `output` resolved
    from the base directory. Each file is compared as a generated file is.
    A tracked file the build no longer produces is removed; an untracked
    file in the destination is kept. Under `--locked`, a tracked file that
    would change fails with `generated directory is stale: <destination>`
    and one line per changed path, and nothing is written.
    """

    output: str | Path | None = None

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

    def publish(self, run: "Run", value: Path) -> Path:
        if self.output is None:
            return value
        destination = self.resolve(self.output)
        if destination.exists() and not destination.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        built = [
            (path.relative_to(value).as_posix(), path)
            for path in walk_files(value)
            if path.is_symlink() or path.is_file()
        ]
        built_names = {name for name, _source in built}
        stale: list[Path] = []
        writes: list[tuple[Path, Path]] = []
        removals: list[Path] = []
        for name, source in built:
            target = destination / name
            if _matches(target, source):
                continue
            if run.locked and tracked(target):
                stale.append(target)
            else:
                writes.append((target, source))
        for path in tracked_files(destination):
            name = path.relative_to(destination).as_posix()
            if name in built_names:
                continue
            if run.locked:
                stale.append(path)
            else:
                removals.append(path)
        if stale:
            for path in sorted(stale, key=lambda item: item.as_posix()):
                print(path, flush=True)
            raise RoadieError(f"generated directory is stale: {destination}")
        for target, source in writes:
            _write(target, source)
            print(f"updated {target}", flush=True)
        for path in removals:
            path.unlink()
        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
        return destination

    def _listing(self, value: Path) -> list[dict[str, str]]:
        if not value.is_dir():
            label = self.name or type(self).__name__
            raise RoadieError(f"{label} built {value}, which is not a directory")
        return _files(value)
