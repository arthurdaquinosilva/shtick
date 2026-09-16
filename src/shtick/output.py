"""Cell output on a left rail (│ for stdout, a red │ for stderr), plus a live 'running' spinner."""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, TextIO

from prompt_toolkit.utils import get_cwidth

_SPLIT = re.compile(r"(\r\n|\n|\r)")
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def format_duration(seconds: float) -> str:
    if seconds < 1e-3:
        return f"{seconds * 1e6:.0f}µs"
    if seconds < 1:
        return f"{seconds * 1e3:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.0f}s"


class RailState:
    """Shared between stdout and stderr so both agree on where the cursor is."""

    def __init__(self, prefixes: dict[str, str], text_styles: dict[str, tuple[str, str]] | None = None):
        self.prefixes = prefixes  # stream → ANSI rail
        self.text_styles = text_styles or {}  # stream → (ANSI start, ANSI reset) around the text
        self.at_line_start = True
        self.stream: str | None = None  # the stream that owns the current (unfinished) line
        self.wrote = False
        self.lock = threading.RLock()


class Spinner:
    def __init__(self, real: TextIO, state: RailState, render: Callable[[str, float], str], delay: float = 0.25):
        self.real = real
        self.state = state
        self.render = render  # (frame, elapsed) -> ansi str
        self.delay = delay
        self.shown = False
        self.enabled = real.isatty()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started = time.perf_counter()

    def start(self) -> None:
        if not self.enabled:
            return
        self.started = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="shtick-spinner", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(0.08):
            elapsed = time.perf_counter() - self.started
            if elapsed < self.delay:
                continue
            with self.state.lock:
                if self._stop.is_set() or not self.state.at_line_start:
                    continue
                self.real.write("\r\x1b[2K" + self.render(FRAMES[i % len(FRAMES)], elapsed))
                self.real.flush()
                self.shown = True
            i += 1

    def clear(self) -> None:
        """Erase the spinner line. Caller holds state.lock."""
        if self.shown:
            self.real.write("\r\x1b[2K")
            self.shown = False

    def stop(self) -> None:
        self._stop.set()
        with self.state.lock:
            self.clear()
            self.real.flush()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)


class Rail:
    """Writes stream text from a running cell with a rail in front of every line."""

    def __init__(self, real: TextIO, state: RailState, spinner: Spinner | None = None, spacer: bool = True):
        self.real = real
        self.state = state
        self.spinner = spinner
        self.spacer = spacer

    def write(self, stream: str, s: str) -> None:
        if not s:
            return
        st = self.state
        prefix = st.prefixes.get(stream, "")
        start, reset = st.text_styles.get(stream, ("", ""))
        with st.lock:
            if self.spinner:
                self.spinner.clear()
            out: list[str] = []
            if self.spacer and not st.wrote:
                out.append(st.prefixes["out"].rstrip() + "\n")  # breathing room below the echoed code
            if not st.at_line_start and st.stream != stream:
                out.append("\n")  # the other stream had an unfinished line: don't glue onto it
                st.at_line_start = True
            for piece in _SPLIT.split(s):
                if not piece:
                    continue
                if piece in ("\n", "\r\n"):
                    if st.at_line_start:
                        out.append(prefix.rstrip())
                    out.append("\n")
                    st.at_line_start = True
                elif piece == "\r":
                    out.append("\r")
                    st.at_line_start = True
                else:
                    if st.at_line_start:
                        out.append(prefix)
                        st.at_line_start = False
                    out.append(start + piece + reset if start else piece)
            st.stream = stream
            self.real.write("".join(out))
            self.real.flush()
            st.wrote = True

    def finish(self) -> None:
        st = self.state
        with st.lock:
            if self.spinner:
                self.spinner.stop()
            if not st.at_line_start:
                self.real.write("\n")
                st.at_line_start = True
            if st.wrote and self.spacer:
                self.real.write(st.prefixes["out"].rstrip() + "\n")  # breathing room above the footer
            self.real.flush()


class LineInput:
    """Line editing for a running cell's stdin, drawn on the rail.

    The terminal is in non-canonical mode while a cell runs, so keys arrive one by one: printable
    text is echoed (after an input rail, or after a prompt the cell printed), Backspace and Ctrl+U
    edit, Enter sends the line, and Ctrl+D ends the cell's stdin (or sends a partial line, like a
    terminal does)."""

    def __init__(self, rail: Rail, prefix: str, style: tuple[str, str]):
        self.rail = rail
        self.prefix = prefix  # ANSI rail for lines that start with input
        self.start, self.reset = style
        self.line = ""
        self._pending = b""

    def _erase(self, text: str) -> None:
        w = sum(get_cwidth(c) for c in text)
        if w:
            self.rail.real.write("\b" * w + " " * w + "\b" * w)

    def feed(self, data: bytes) -> tuple[bytes, bool]:
        st = self.rail.state
        out = self.rail.real
        forward = b""
        eof = False
        data, self._pending = self._pending + data, b""
        try:
            text = data.decode()
        except UnicodeDecodeError as e:
            if e.start >= len(data) - 3:  # a multi-byte character split across reads
                self._pending = data[e.start:]
            text = data[: e.start].decode() if e.start >= len(data) - 3 else data.decode(errors="replace")
        with st.lock:
            if self.rail.spinner:
                self.rail.spinner.stop()
                self.rail.spinner.enabled = False  # the cell is interactive: no spinner over the input
            if self.rail.spacer and not st.wrote:
                out.write(st.prefixes["out"].rstrip() + "\n")
                st.wrote = True
            for ch in text:
                if ch in "\r\n":
                    forward += (self.line + "\n").encode()
                    if st.at_line_start:
                        out.write(self.prefix.rstrip())
                    out.write("\n")
                    self.line = ""
                    st.at_line_start = True
                elif ch == "\x04":  # Ctrl+D
                    if self.line:
                        forward += self.line.encode()
                        self.line = ""
                    else:
                        eof = True
                elif ch in ("\x7f", "\x08"):
                    if self.line:
                        self._erase(self.line[-1])
                        self.line = self.line[:-1]
                elif ch == "\x15":  # Ctrl+U
                    self._erase(self.line)
                    self.line = ""
                elif ch == "\t" or ch >= " " and ch != "\x7f":
                    if st.at_line_start:
                        out.write(self.prefix)
                        st.at_line_start = False
                    st.stream = "in"
                    self.line += ch
                    out.write(self.start + ch + self.reset)
                # other control keys and escape sequences aren't text input
            out.flush()
        return forward, eof
