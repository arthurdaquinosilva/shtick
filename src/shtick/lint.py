"""shellcheck integration: a background linter for the input bar and full reports for %lint."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2, "style": 3}
DIALECTS = {"bash": "bash", "sh": "sh", "dash": "dash", "ksh": "ksh"}


@dataclass(frozen=True)
class Finding:
    code: int
    severity: str
    line: int
    column: int
    end_column: int
    message: str

    @property
    def label(self) -> str:
        return f"SC{self.code}"

    @property
    def url(self) -> str:
        return f"https://www.shellcheck.net/wiki/SC{self.code}"


@lru_cache(maxsize=1)
def shellcheck_path() -> str | None:
    return shutil.which("shellcheck")


def dialect(kind: str) -> str | None:
    """shellcheck's -s value for a shell kind; None for shells it can't check (zsh)."""
    return DIALECTS.get(kind)


def check(code: str, kind: str, exclude: list[str] | tuple[str, ...] = ()) -> list[Finding]:
    """Findings sorted most severe first. Empty when shellcheck is missing or can't check this shell."""
    exe = shellcheck_path()
    shell = dialect(kind)
    if exe is None or shell is None or not code.strip():
        return []
    argv = [exe, "-s", shell, "-f", "json1"]
    if exclude:
        argv += ["-e", ",".join(e.upper().removeprefix("SC") for e in exclude)]
    try:
        proc = subprocess.run([*argv, "-"], input=code, capture_output=True, text=True, timeout=10)
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    findings = [
        Finding(c["code"], c["level"], c["line"], c["column"], c.get("endColumn", c["column"]), c["message"])
        for c in data.get("comments", [])
    ]
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.line, f.column))


class BackgroundLinter:
    """Lints the input as it changes, off the UI thread, keeping only the newest request."""

    def __init__(self, on_result: Callable[[], None], delay: float = 0.25):
        self.on_result = on_result
        self.delay = delay
        self.text = ""
        self.findings: list[Finding] = []
        self._requested: tuple[str, str, tuple[str, ...]] | None = None
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None

    def request(self, text: str, kind: str, exclude: list[str]) -> None:
        if shellcheck_path() is None:
            return
        with self._cond:
            self._requested = (text, kind, tuple(exclude))
            self._cond.notify()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="shtick-lint", daemon=True)
            self._thread.start()

    def current(self, text: str) -> Finding | None:
        """The top finding, but only if it's for exactly this text (never stale)."""
        if text != self.text or not self.findings:
            return None
        return self.findings[0]

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._requested is None:
                    self._cond.wait()
                # debounce: wait until typing pauses
                while True:
                    req = self._requested
                    self._cond.wait(self.delay)
                    if self._requested == req:
                        break
                self._requested = None
            text, kind, exclude = req  # type: ignore[misc]
            findings = check(text, kind, exclude) if text.strip() and not text.lstrip().startswith("%") else []
            self.text, self.findings = text, findings
            self.on_result()
