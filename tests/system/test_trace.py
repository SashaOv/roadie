"""Verify the strace capability, command line, and log parser."""

import subprocess
import sys
from pathlib import Path

import pytest

from roadie.system.trace import parse_trace, traced_command, tracing_error


def _hex(text: str) -> str:
    return "".join(f"\\x{byte:02x}" for byte in text.encode())


# (spec) Enforcing declared inputs
def test_traced_command_matches_the_decision() -> None:
    log = Path("trace.log")
    assert traced_command(["python3", "-c", "pass"], log) == [
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
        "trace.log",
        "python3",
        "-c",
        "pass",
    ]


# (spec) Enforcing declared inputs
def test_parse_trace_reads_hex_paths_and_flags() -> None:
    spaced = "/w/p/a b.txt"
    greater = "/w/p/a>b.txt"
    accented = "/w/p/café.txt"
    lines = [
        f'111 openat(AT_FDCWD, "{_hex(spaced)}", O_RDONLY|O_CLOEXEC) = 3<{_hex(spaced)}>',
        f'111 openat(AT_FDCWD, "{_hex(greater)}", O_RDWR) = 4<{_hex(greater)}>',
        f'111 open("{_hex(accented)}", O_WRONLY|O_CREAT) = 5<{_hex(accented)}>',
        f'222 openat(AT_FDCWD, "{_hex("/w/p/other.txt")}", O_RDONLY) = 3<{_hex("/w/p/other.txt")}>',
        '111 read(3, "data", 4) = 4',
        f'111 openat(AT_FDCWD, "{_hex("/w/p/failed.txt")}", O_RDONLY) = -1 ENOENT (No such file or directory)',
    ]
    read, written = parse_trace("\n".join(lines))
    assert read == {Path(spaced), Path(greater), Path("/w/p/other.txt")}
    assert written == {Path(greater), Path(accented)}


# (spec) Enforcing declared inputs
def test_parse_trace_prefers_the_resolved_descriptor_path() -> None:
    lines = [
        f'111 openat(AT_FDCWD, "{_hex("rel.txt")}", O_RDONLY) = 3<{_hex("/abs/rel.txt")}>',
        f'111 openat2(AT_FDCWD, "{_hex("/w/p/two.txt")}", {{flags=O_RDONLY|O_CLOEXEC, mode=0}}, 24) = 4<{_hex("/w/p/two.txt")}>',
    ]
    read, written = parse_trace("\n".join(lines))
    assert read == {Path("/abs/rel.txt"), Path("/w/p/two.txt")}
    assert written == set()


# (spec) Enforcing declared inputs
def test_parse_trace_ignores_non_opens_and_unfinished() -> None:
    text = "\n".join(
        [
            '111 stat("/w/p/a.txt", 0x7f) = 0',
            f'111 openat(AT_FDCWD, "{_hex("/w/p/a.txt")}", O_RDONLY <unfinished ...>',
            "111 +++ exited with 0 +++",
        ]
    )
    assert parse_trace(text) == (set(), set())


# (spec) Enforcing declared inputs
def test_tracing_error_on_a_non_linux_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert tracing_error() == "--enforce needs Linux"


# (spec) Enforcing declared inputs
def test_tracing_error_when_strace_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "strace")

    monkeypatch.setattr(subprocess, "run", fail)
    assert tracing_error() == (
        "--enforce needs strace: cannot run strace: [Errno 2] No such file or directory"
    )


# (spec) Enforcing declared inputs
def test_tracing_error_when_strace_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["strace", "-V"], 1, "", "strace: bad option"
        ),
    )
    assert tracing_error() == "--enforce needs strace: cannot run strace: strace: bad option"


# (spec) Enforcing declared inputs
def test_tracing_error_is_none_when_strace_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(["strace", "-V"], 0, "strace 6.8", ""),
    )
    assert tracing_error() is None
