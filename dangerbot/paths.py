"""Stable writable storage for packaged desktop builds."""
import os
from pathlib import Path
import sys


def application_directory():
    if not getattr(sys, "frozen", False):
        return Path.cwd()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "DangerBotanger"
