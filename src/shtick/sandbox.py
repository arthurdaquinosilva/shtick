"""%sandbox: run cells in a throwaway directory and report what they changed on disk.

This is a working-directory sandbox, not a security boundary: code that uses absolute paths,
`cd ..` or `$HOME` still reaches the real filesystem. The footer warns when a cell leaves it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 20000
HASH_LIMIT = 1 << 20  # hash files up to 1 MiB; bigger ones compare by size and mtime

Entry = tuple[str, int, int, str]  # (kind, size, mtime_ns, digest)


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


class Sandbox:
    def __init__(self, source: str, copy: bool = False):
        self.source = os.path.abspath(source)  # where the session was before
        self.copied = copy
        self.root = Path(tempfile.mkdtemp(prefix="shtick-sandbox-")).resolve()
        if copy:
            shutil.copytree(self.source, self.root, symlinks=True, dirs_exist_ok=True)

    def contains(self, path: str | None) -> bool:
        if not path:
            return True
        real = os.path.realpath(path)
        return real == str(self.root) or real.startswith(str(self.root) + os.sep)

    def snapshot(self) -> dict[str, Entry]:
        entries: dict[str, Entry] = {}
        root = str(self.root)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    entries[rel + "/"] = ("dir", 0, 0, "")
                elif stat.S_ISLNK(st.st_mode):
                    entries[rel] = ("link", 0, 0, os.readlink(full))
                else:
                    entries[rel] = ("file", st.st_size, st.st_mtime_ns, _digest(full, st.st_size))
                if len(entries) > MAX_FILES:
                    return entries
        return entries

    def diff(self, before: dict[str, Entry], after: dict[str, Entry]) -> Changes:
        changes = Changes(truncated=len(after) > MAX_FILES or len(before) > MAX_FILES)
        for path in sorted(after.keys() - before.keys()):
            changes.added.append(path)
        for path in sorted(before.keys() - after.keys()):
            changes.deleted.append(path)
        for path in sorted(before.keys() & after.keys()):
            old, new = before[path], after[path]
            if old[0] == "dir" and new[0] == "dir":
                continue
            if old[0] != new[0] or old[1] != new[1] or old[3] != new[3] or (not old[3] and old[2] != new[2]):
                changes.modified.append(path)
        # A new directory's contents are implied by the directory itself.
        changes.added = _collapse(changes.added)
        changes.deleted = _collapse(changes.deleted)
        return changes

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _digest(path: str, size: int) -> str:
    if size > HASH_LIMIT:
        return ""
    try:
        with open(path, "rb") as f:
            return hashlib.blake2b(f.read(), digest_size=16).hexdigest()
    except OSError:
        return ""


def _collapse(paths: list[str]) -> list[str]:
    dirs = [p for p in paths if p.endswith("/")]
    return [p for p in paths if not any(p != d and p.startswith(d) for d in dirs)]
