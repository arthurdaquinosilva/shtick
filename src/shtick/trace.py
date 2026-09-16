"""%trace: run code with `set -x` and a machine-readable PS4, then show what executed.

Each traced command is printed by the shell as

    ++<US>shtick<US>cell-3.sh<US>4<US>greet<US><US> echo 'hi a'

(US = \\x1f; bash repeats the leading `+` for nesting, zsh reports depth in its own field). The
splitter pulls those lines out of the output streams; everything else is passed through.
\\x01 would be a nicer marker but bash 3.2 uses it internally and drops it from PS4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

US = "\x1f"
MARK = f"{US}shtick{US}"

PS4 = {
    # `-` defaults: PS4 is expanded under the cell's own options, including set -u
    "bash": f"+{MARK}${{BASH_SOURCE[0]-}}{US}${{LINENO-}}{US}${{FUNCNAME[0]-}}{US}{US} ",
    "zsh": f"{MARK}%x{US}%I{US}%N{US}%e{US} ",
    "dash": f"+{MARK}{US}${{LINENO-}}{US}{US}{US} ",
    "sh": f"+{MARK}{US}${{LINENO-}}{US}{US}{US} ",
}

UNWRAP = "{ set +x; } 2>/dev/null; PS4=${__shtick_ps4-}; unset __shtick_ps4"

_LINE = re.compile(rf"(?P<plus>\+*){MARK}(?P<file>[^{US}]*){US}(?P<line>\d*){US}(?P<func>[^{US}]*){US}(?P<depth>\d*){US} ?(?P<cmd>.*)")
_DRIVER = re.compile(r"^(command printf [0-9a-f]{16}|'?command'? 'printf'|__shtick_status\b|set \+x$|\. .*cell-\d+\.sh$)")


@dataclass
class TraceLine:
    file: str
    line: int
    function: str
    depth: int
    command: str

    def location(self, run_number: int, origin: Callable[[int, int], str | None] | None = None) -> str:
        """`4` inside this cell, `[2]:4` in an earlier cell, `deploy.sh:9` for code that came from a
        script, `lib.sh:4` in a sourced file.

        Cell files are named after the engine's run number; origin(run_number, line) names where
        that code came from (None: unknown)."""
        m = re.fullmatch(r"cell-(\d+)\.sh", self.file)
        run = int(m[1]) if m else run_number if not self.file else None
        if run is not None:
            named = origin(run, self.line) if origin else None
            if named:
                return named
            if run == run_number:
                return str(self.line)
            return f"cell:{self.line}"
        return f"{self.file}:{self.line}"


def wrap(code: str, kind: str) -> str:
    """Turn tracing on in the cell's first line, so line numbers stay the same."""
    ps4 = PS4.get(kind, PS4["sh"])
    return f"__shtick_ps4=${{PS4-}}; PS4='{ps4}'; set -x; {code}"


def parse(text: str) -> TraceLine | None:
    m = _LINE.match(text)
    if not m:
        return None
    file = m["file"].rsplit("/", 1)[-1]
    depth = int(m["depth"]) if m["depth"] else max(1, len(m["plus"]))
    return TraceLine(file, int(m["line"] or 0), m["func"] if m["func"] != m["file"] else "", depth, m["cmd"])


def select(lines: list[TraceLine], cell_number: int) -> list[TraceLine]:
    """Drop the engine's own commands: the ones outside any cell file, and the driver line."""
    has_files = any(t.file for t in lines)
    kept = []
    for t in lines:
        if has_files and (t.file in ("", "zsh", "bash", "sh", "dash") or not t.file):
            continue
        if _DRIVER.match(t.command):
            continue
        kept.append(t)
    # zsh prints `x=` before tracing the command substitution in `x=$(…)`, then `x=value`
    return [t for i, t in enumerate(kept)
            if not (re.fullmatch(r"[A-Za-z_]\w*=", t.command) and any(u.command.startswith(t.command) and u.command != t.command for u in kept[i + 1:]))]


class TraceSplitter:
    """Feeds (stream, text) chunks; trace lines are collected, the rest goes to `forward`."""

    def __init__(self, forward: Callable[[str, str], None]):
        self.forward = forward
        self.lines: list[TraceLine] = []
        self._buf: dict[str, str] = {}

    def feed(self, stream: str, text: str) -> None:
        buf = self._buf.get(stream, "") + text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            self._line(stream, line, newline=True)
        # Unfinished text that can't be a trace line (a `read -p` prompt…) shouldn't wait.
        if buf and US not in buf and not buf.startswith("+"):
            self.forward(stream, buf)
            buf = ""
        self._buf[stream] = buf

    def _line(self, stream: str, line: str, newline: bool) -> None:
        if US not in line:
            self.forward(stream, line + ("\n" if newline else ""))
            return
        # zsh can put a traced command substitution in the middle of a line: split at markers.
        starts = [m.start() for m in re.finditer(rf"\+*{MARK}", line)]
        head = line[: starts[0]]
        if head:
            self.forward(stream, head + "\n")
        for i, start in enumerate(starts):
            piece = line[start: starts[i + 1] if i + 1 < len(starts) else len(line)]
            if (t := parse(piece)) is not None:
                self.lines.append(t)

    def flush(self) -> None:
        for stream, buf in self._buf.items():
            if buf:
                self._line(stream, buf, newline=False)
        self._buf = {}
