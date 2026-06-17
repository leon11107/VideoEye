"""Resolve bundled resource paths in both dev and PyInstaller-frozen runs.

In a normal checkout, paths are relative to the repo root. In a PyInstaller
build, data files are unpacked under sys._MEIPASS, so resolve against that.
"""

import os
import sys
from pathlib import Path


def app_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parents[1]   # repo root (VideoEye/)


def resource_path(rel: str) -> str:
    """Absolute path to a bundled resource given its repo-relative path."""
    return str(app_base() / rel)
