"""Path conventions that differ by platform."""

import sys


def executable_suffix() -> str:
    """Return `.exe` on Windows, otherwise the empty string."""
    return ".exe" if sys.platform == "win32" else ""
