"""Trace a program with strace and read the files it opened."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from roadie.system.command import cannot_run_message

_OPEN = re.compile(r"\b(?:openat2|openat|open)\s*\(")
_DESCRIPTOR = re.compile(r"=\s*\d+<(.+)>\s*$")
_FLAG = re.compile(r"\b(O_RDONLY|O_WRONLY|O_RDWR)\b")
_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
_HEX = re.compile(r"[0-9a-fA-F]{2}")


def tracing_error() -> str | None:
    """Return the `--enforce` refusal when tracing is unavailable, else None."""
    if not sys.platform.startswith("linux"):
        return "--enforce needs Linux"
    try:
        completed = subprocess.run(
            ["strace", "-V"], capture_output=True, text=True, check=False
        )
    except OSError as error:
        return f"--enforce needs strace: {cannot_run_message('strace', error)}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        reason = detail.splitlines()[0] if detail else f"exited with status {completed.returncode}"
        return f"--enforce needs strace: cannot run strace: {reason}"
    return None


def traced_command(arguments: Sequence[str], log: Path) -> list[str]:
    """Return the strace command line that runs `arguments` (decision 4)."""
    return [
        "strace",
        "-f",
        "-qq",
        "-xx",
        "-y",
        "--seccomp-bpf",
        "--kill-on-exit",
        "-e",
        "trace=open,openat,openat2",
        "-e",
        "status=successful",
        "-o",
        str(log),
        *arguments,
    ]


def parse_trace(text: str) -> tuple[set[Path], set[Path]]:
    """Return the paths a trace read and the paths it wrote.

    A successful open with `O_RDONLY` reads, with `O_WRONLY` writes, and
    with `O_RDWR` does both. The descriptor path strace prints after the
    returned descriptor is the resolved path; it wins over the argument.
    Lines that are not a successful open are ignored.
    """
    read: set[Path] = set()
    written: set[Path] = set()
    for line in text.splitlines():
        if _OPEN.search(line) is None:
            continue
        # Require a successful return with a descriptor.
        equals = line.rfind(" = ")
        if equals < 0:
            continue
        tail = line[equals + 3 :].strip()
        if tail.startswith("-"):
            continue
        flag = _FLAG.search(line)
        if flag is None:
            continue
        path = _descriptor_path(line)
        if path is None:
            path = _argument_path(line)
        if path is None:
            continue
        kind = flag.group(1)
        if kind in ("O_RDONLY", "O_RDWR"):
            read.add(path)
        if kind in ("O_WRONLY", "O_RDWR"):
            written.add(path)
    return read, written


def _descriptor_path(line: str) -> Path | None:
    match = _DESCRIPTOR.search(line.strip())
    if match is None:
        return None
    raw = match.group(1).removesuffix(" (deleted)")
    if not raw or raw.startswith(("pipe:", "socket:", "anon_inode:")):
        return None
    return Path(_decode(raw))


def _argument_path(line: str) -> Path | None:
    match = _QUOTED.search(line)
    if match is None:
        return None
    return Path(_decode(match.group(1)))


def _decode(raw: str) -> str:
    if "\\" not in raw:
        return raw
    data = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            following = raw[index + 1]
            if following == "x" and index + 3 < len(raw) + 1:
                hexpart = raw[index + 2 : index + 4]
                if len(hexpart) == 2 and _HEX.fullmatch(hexpart):
                    data.append(int(hexpart, 16))
                    index += 4
                    continue
            if following in "01234567":
                end = index + 1
                while end < len(raw) and end < index + 4 and raw[end] in "01234567":
                    end += 1
                data.append(int(raw[index + 1 : end], 8))
                index = end
                continue
            if following == "n":
                data.append(0x0A)
                index += 2
                continue
            if following == "t":
                data.append(0x09)
                index += 2
                continue
            if following == "r":
                data.append(0x0D)
                index += 2
                continue
            # Escaped char (e.g. \\, \") is itself.
            data.extend(following.encode("utf-8", "surrogateescape"))
            index += 2
            continue
        data.extend(char.encode("utf-8", "surrogateescape"))
        index += 1
    return data.decode("utf-8", "surrogateescape")
