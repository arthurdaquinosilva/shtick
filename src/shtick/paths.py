"""Showing paths: ~ for home, shortened from the left when space is short."""

from __future__ import annotations

import os


def short_path(path: str | os.PathLike) -> str:
    path = str(path)
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    return path


def fit_path(path: str, width: int) -> str:
    """Shorten a path from the left so it fits: …/project/src."""
    if len(path) <= width or width < 8:
        return path
    return "…" + path[len(path) - width + 1:]
