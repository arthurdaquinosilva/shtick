"""The controller: runs cells in the session engine and draws them as blocks."""

from __future__ import annotations

import difflib
import os
import re
import signal
import sys
import termios
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from rich.console import Console, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from shtick import trace as tracing
from shtick.config import Profile, Settings
from shtick.engine import CellResult, Session, ShellNotFound
from shtick.history import HistoryManager
from shtick.magics import MAGICS, MagicError
from shtick.paths import fit_path, short_path
from shtick.output import LineInput, Rail, RailState, Spinner, format_duration
from shtick.syntax import MAGIC_RE, is_unfinished
from shtick.theme import PALETTES, Theme, get_theme

if TYPE_CHECKING:
    from shtick.sandbox import Changes, Sandbox
    from shtick.scripts import Script
    from shtick.testing import Expectation


BODY_COMMANDS = {"trace", "compare"}  # commands that take code on the following lines


@contextmanager
def keys_one_by_one(fd: int, enabled: bool = True) -> Iterator[None]:
    """Non-canonical, no-echo terminal input (Ctrl+C still signals) while a cell runs."""
    if not enabled:
        yield
        return
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        yield
        return
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ICANON | termios.ECHO)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@dataclass
class Cell:
    number: int
    code: str
    result: CellResult
    label: str = ""  # e.g. "deploy.sh:4-7" for a script step
    expectations: list[Expectation] = field(default_factory=list)
    changes: Changes | None = None
    trace: list[tracing.TraceLine] = field(default_factory=list)
    sandbox: str = "off"  # off · empty · copy — where the cell ran
    stdin: str = ""  # what was typed into the cell while it ran

    @property
    def ok(self) -> bool:
        return self.result.exit == 0


class Shell:
    def __init__(self, settings: Settings | None = None, profile: Profile | None = None, cwd: str | None = None):
        self.settings = settings or Settings()
        self.profile = profile
        self.theme: Theme = get_theme(self.settings.theme)
        self.ui = self._make_console()
        self.history = HistoryManager(profile.history_db if profile else None)
        self.start_dir = os.path.abspath(cwd or os.getcwd())
        self.session = self._new_session(self.settings.shell, self.start_dir)
        self.count = 0
        self.cells: list[Cell] = []
        self.origins: dict[int, tuple[int, str, int]] = {}  # engine run number → (cell number, script name, first line)
        self.next_input = ""
        self.last_duration: float | None = None
        self.last_status: int | None = None
        self.flash: str | None = None  # a one-off message for the mode line
        self.sandbox: Sandbox | None = None
        self.script: Script | None = None
        self.step_pending: int | None = None
        self.test_start = 0  # cells before this index aren't part of %test
        self.exit_requested = False
        self.current_command = ""
        self._magic_depth = 0
        self._magic_cells = 0
        self.interactive = sys.__stdout__.isatty() and sys.__stdin__.isatty()
        self.vars_baseline = None
        self.env_baseline: dict[str, str] | None = None
        self._run_startup()

    # ── consoles, settings, sessions ──────────────────────────────────────

    def _make_console(self) -> Console:
        return Console(file=sys.__stdout__, theme=self.theme.rich_theme, highlight=False, force_terminal=sys.__stdout__.isatty() or None)

    @property
    def width(self) -> int:
        return self.ui.width

    def _new_session(self, shell: str, cwd: str) -> Session:
        try:
            return Session(shell, cwd=cwd, tty=self.settings.tty)
        except ShellNotFound:
            if shell == "bash":
                raise
            self.warn(f"shell {shell!r} not found — using bash")
            self.settings.shell = "bash"
            return Session("bash", cwd=cwd, tty=self.settings.tty)

    def _run_startup(self) -> None:
        """Every new session (start, restart, %shell, after the shell exited): startup lines, %vars baseline."""
        from shtick.magics.inspect import env_snapshot, snapshot

        for line in self.settings.startup:
            result = self.session.query(line)
            if result.exit != 0:
                self.warn(f"startup line failed (exit {result.status}): {line}")
        if self.session.kind == "zsh":
            snapshot(self.session)  # the first listing makes zsh autoload a few parameters (LOGCHECK, WATCHFMT…)
        self.vars_baseline = snapshot(self.session)
        self.env_baseline = env_snapshot(self.session)

    def switch_shell(self, shell: str) -> None:
        cwd = self.session.cwd
        new = Session(shell, cwd=cwd, tty=self.settings.tty)
        self.session.close()
        self.session = new
        self.settings.shell = shell
        self._run_startup()

    def restart(self) -> None:
        self.session.restart()
        self._run_startup()

    def set_setting(self, name: str, value: Any) -> None:
        value = self.settings.validate(name, value)
        if name == "theme":
            if value not in PALETTES:
                raise ValueError(f"unknown theme {value!r} — try: {', '.join(PALETTES)}")
            self.theme = get_theme(value)
            self.ui = self._make_console()
        elif name == "shell":
            try:
                self.switch_shell(value)
            except ShellNotFound as e:
                raise ValueError(str(e)) from None
        elif name == "tty":
            self.session.set_tty(value)
        setattr(self.settings, name, value)

    def close(self) -> None:
        if self.sandbox is not None:
            self.sandbox.close()
        self.session.close()
        self.history.end_session()

    # ── output helpers ────────────────────────────────────────────────────

    def print(self, renderable: RenderableType | str = "", end: str = "\n", **kwargs: Any) -> None:
        self.ui.print(renderable, end=end, **kwargs)

    def warn(self, message: str) -> None:
        self.print(Text.assemble(("⚠ ", "shtick.warn"), (message, "shtick.fg")))

    def error(self, message: str) -> None:
        self.print(Text.assemble(("✗ ", "shtick.err.bold"), (message, "shtick.fg")))

    def _ansi(self, *parts: tuple[str, str]) -> str:
        with self.ui.capture() as cap:
            self.ui.print(Text.assemble(*parts), end="")
        return cap.get()

    def rail_line(self, *parts: tuple[str, str]) -> None:
        """A line inside the current block, after the output."""
        self.print(Text.assemble(("│ ", "shtick.rail"), *parts))

    def spacing(self) -> None:
        self.print("\n")  # two blank lines between blocks

    # ── running input ─────────────────────────────────────────────────────

    def run_cell(self, raw: str) -> Cell | None:
        """Run what the user typed: a %command or shell code."""
        text = raw.strip("\n")
        if not text.strip():
            return None
        if m := MAGIC_RE.match(text.lstrip()):
            first, _, rest = text.lstrip().partition("\n")
            if rest.strip() and m["name"] not in BODY_COMMANDS:
                # a %command line followed by code: the command, then the code as its own cell
                self.run_cell(first)
                return None if self.exit_requested else self.run_cell(rest)
            self.run_magic(m["name"], m["args"] or "", text.lstrip())
            return None
        lines = text.split("\n")
        for i, line in enumerate(lines[1:], 1):
            if MAGIC_RE.match(line) and not is_unfinished("\n".join(lines[:i]), self.session.kind):
                # a %command line after complete code (not inside a heredoc): run them one after the other
                self.run_cell("\n".join(lines[:i]))
                return None if self.exit_requested else self.run_cell("\n".join(lines[i:]))
        step, self.step_pending = self.step_pending, None
        label = ""
        source = None
        if step is not None and self.script is not None and step < len(self.script.chunks):
            label = self.script.label(step)
            chunk = self.script.chunks[step]
            if text == chunk.code:
                source = (self.script.name, chunk.start)
            else:
                label += "  · edited"
            self.script.pos = step + 1
        return self.execute(text, label=label, source=source)

    def run_magic(self, name: str, args: str, source: str) -> Any:
        self.echo(source)
        spec = MAGICS.get(name)
        self.count += 1
        self.history.store_input(self.count, source)
        self.current_command = name
        outer_cells = self._magic_cells
        self._magic_depth += 1
        self._magic_cells = 0
        result = None
        try:
            if spec is None:
                close = difflib.get_close_matches(name, MAGICS, n=1)
                raise MagicError(f"unknown command %{name}" + (f" — did you mean %{close[0]}?" if close else " — see %help"))
            result = spec.fn(self, args)
        except MagicError as e:
            if not self._magic_cells:
                self.print()
            self.error(str(e))
            self.last_status = 1
            self._magic_cells = 0  # the error closes the block
        finally:
            self._magic_depth -= 1
            ran = self._magic_cells
            self._magic_cells = outer_cells
        if not ran:
            self.spacing()
        return result

    def echo(self, source: str, label: str = "") -> None:
        grid = Table.grid(padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        if MAGIC_RE.match(source):
            name, _, rest = source.partition(" ")
            first, nl, body = rest.partition("\n")
            head = Text.assemble((name, "shtick.accent.bold"), (" " + first if first else "", "shtick.fg"))
            content: RenderableType = head
            if nl:
                content = Table.grid()
                content.add_row(head)
                content.add_row(Syntax(body, "bash", theme=self.theme.syntax_theme, background_color="default", word_wrap=True))
        else:
            content = Syntax(source, "bash", theme=self.theme.syntax_theme, background_color="default", word_wrap=True)
        grid.add_row(Text(">", style="shtick.accent.bold"), content)
        if label:
            self.print(Text.assemble(("  ", ""), ("▸ ", "shtick.accent"), (label, "shtick.label")))
        self.print(grid)

    def run_setup(self, code: str, label: str) -> CellResult:
        """Run bookkeeping code the user didn't type (e.g. %open's `set -- args`) without drawing a
        block, but record it so %test and saved tests replay it."""
        result = self.session.run(code)
        self.cells.append(Cell(0, code, result, label))
        return result

    def origin(self, run: int, line: int, current: int | None = None) -> str | None:
        """Where line `line` of engine run `run` came from, for trace locations."""
        if run not in self.origins:
            return None
        n, script, first = self.origins[run]
        if script:
            return f"{script}:{first + line - 1}"
        return None if run == current else f"[{n}]:{line}"

    def execute(self, code: str, label: str = "", trace: bool = False, echo: bool = True, record: bool = True,
                source: tuple[str, int] | None = None) -> Cell:
        """Run shell code as a numbered cell and draw it: echo, railed output, footer."""
        code = code.strip("\n")
        if self._magic_depth:
            if not self._magic_cells:
                self.print()  # one blank line between the %command and the cells it runs
            self._magic_cells += 1
        self.count += 1
        n = self.count
        if record:
            self.history.store_input(n, code)
        if echo:
            self.echo(code, label)

        before = self.sandbox.snapshot() if self.sandbox is not None else None
        rails = {
            "out": self._ansi(("│ ", "shtick.rail")),
            "err": self._ansi(("│ ", "shtick.rail.err")),
            "in": self._ansi(("│ ", "shtick.accent")),
        }
        start_err, _, reset = self._ansi(("\x00", "shtick.stderr")).partition("\x00")
        state = RailState(rails, {"err": (start_err, reset)})
        spinner = Spinner(
            sys.__stdout__,
            state,
            lambda frame, elapsed: self._ansi(
                ("╰─ ", "shtick.border"), (frame, "shtick.accent"), (" running ", "shtick.muted"),
                (format_duration(elapsed), "shtick.faint"), ("  ctrl+c to interrupt", "shtick.faint"),
            ),
        )
        rail = Rail(sys.__stdout__, state, spinner)
        run_number = self.session.count + 1
        cell_file = re.compile(r"cell-(\d+)\.sh")

        def cell_names(stream: str, text: str) -> None:
            """Error messages name the engine's temp files (cell-12.sh): show the cell numbers instead."""
            if "cell-" in text:
                text = cell_file.sub(lambda m: f"[{n}]" if int(m[1]) == run_number else f"[{self.origins[int(m[1])][0]}]"
                                     if int(m[1]) in self.origins else m[0], text)
            rail.write(stream, text)

        shown: list[tuple[str, str]] = []

        def forward_shown(stream: str, text: str) -> None:
            shown.append((stream, text))
            cell_names(stream, text)

        splitter = tracing.TraceSplitter(forward_shown) if trace else None
        on_output = splitter.feed if splitter else cell_names
        run_code = tracing.wrap(code, self.session.kind) if trace else code

        interactive_input = sys.__stdin__.isatty()
        start_in, _, reset_in = self._ansi(("\x00", "shtick.input")).partition("\x00")
        line_input = LineInput(rail, rails["in"], (start_in, reset_in))
        typed: list[bytes] = []

        def on_input(data: bytes) -> tuple[bytes, bool]:
            forward, eof = (data, False) if self.settings.tty else line_input.feed(data)
            typed.append(forward)
            return forward, eof
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: self.session.interrupt())
        spinner.start()
        try:
            with keys_one_by_one(sys.__stdin__.fileno(), enabled=interactive_input):
                result = self.session.run(
                    run_code,
                    on_output=on_output,
                    input_fd=sys.__stdin__.fileno() if interactive_input else None,
                    on_input=on_input,
                )
        finally:
            signal.signal(signal.SIGINT, previous)
            if splitter:
                splitter.flush()
            rail.finish()
        result.code = code
        if trace:
            self.session.query(tracing.UNWRAP)
            # what the cell printed, without the trace lines (expectations check this)
            result.events = shown
            result.stdout = "".join(t for st, t in shown if st == "out")
            result.stderr = "".join(t for st, t in shown if st == "err")

        self.origins[result.number] = (n, *(source or ("", 0)))
        cell = Cell(n, code, result, label, stdin=b"".join(typed).decode(errors="replace"))
        if self.sandbox is not None:
            cell.sandbox = "copy" if self.sandbox.copied else "empty"
        if splitter:
            cell.trace = tracing.select(splitter.lines, result.number)
            self._show_trace(cell, wrote=state.wrote)
        if self.sandbox is not None and before is not None:
            cell.changes = self.sandbox.diff(before, self.sandbox.snapshot())
            self._show_changes(cell.changes, wrote=state.wrote or bool(cell.trace))
        if result.syntax_error:
            self._show_syntax_error(result.syntax_error)
        self.footer(cell)
        if result.died:
            self._run_startup()
        self.cells.append(cell)
        self.last_duration, self.last_status = result.duration, result.status
        if record:
            self.history.store_status(n, result.status, self.session.label, result.cwd or self.session.cwd)
        self.spacing()
        return cell

    # ── block pieces ──────────────────────────────────────────────────────

    def _show_syntax_error(self, message: str) -> None:
        self.rail_line()
        for line in message.split("\n"):
            self.print(Text.assemble(("│ ", "shtick.rail.err"), (line, "shtick.stderr")))
        self.rail_line()

    def _show_trace(self, cell: Cell, wrote: bool) -> None:
        if not cell.trace:
            return
        if not wrote:
            self.rail_line()
        self.rail_line(("trace", "shtick.muted.bold"), (f" · {len(cell.trace)} commands", "shtick.faint"))
        locations = [t.location(cell.result.number, lambda run, line: self.origin(run, line, cell.result.number)) for t in cell.trace]
        width = max(len(loc) for loc in locations)
        base = min(t.depth for t in cell.trace)
        for t, loc in zip(cell.trace, locations):
            indent = "  " * (t.depth - base)
            parts: list[tuple[str, str]] = [
                (loc.rjust(width), "shtick.trace.loc"), ("  ", ""),
                (indent, "shtick.trace.depth"), (t.command, "shtick.trace"),
            ]
            if t.function:
                parts.append((f"  ({t.function})", "shtick.faint"))
            self.rail_line(*parts)
        self.rail_line()

    def _show_changes(self, changes: Changes, wrote: bool) -> None:
        if not changes:
            return
        if not wrote:
            self.rail_line()
        parts: list[tuple[str, str]] = [("sandbox", "shtick.muted.bold"), ("  ", "")]
        for mark, style, paths in (("+", "shtick.added", changes.added), ("~", "shtick.modified", changes.modified), ("−", "shtick.deleted", changes.deleted)):
            for p in paths:
                parts += [(f"{mark} ", style), (p, "shtick.fg"), ("  ", "")]
        self.rail_line(*parts)
        self.rail_line()

    def footer(self, cell: Cell) -> None:
        r = cell.result
        parts: list[tuple[str, str]] = [("╰─ ", "shtick.border")]
        sep = (" · ", "shtick.faint")
        if r.syntax_error is not None:
            parts += [("✗ ", "shtick.err.bold"), ("syntax error", "shtick.err"), (" · not run", "shtick.faint")]
        elif r.died:
            how = f"killed by signal {-r.died_status}" if (r.died_status or 0) < 0 else f"exit {r.died_status}"
            parts += [("✗ ", "shtick.err.bold"), ("shell exited", "shtick.err"), sep, (how, "shtick.muted"), sep,
                      (format_duration(r.duration), "shtick.muted"), sep, ("session restarted, state lost", "shtick.warn")]
        else:
            if r.interrupted:
                parts += [("✗ ", "shtick.err.bold"), ("interrupted", "shtick.err"), sep]
            elif r.exit == 0:
                parts += [("✓ ", "shtick.ok")]
            else:
                parts += [("✗ ", "shtick.err.bold")]
            parts += [(f"exit {r.exit}", "shtick.muted" if r.exit == 0 else "shtick.err"), sep, (format_duration(r.duration), "shtick.muted")]
            tail: list[tuple[str, str]] = []
            if self.sandbox is not None:
                tail = [sep, ("sandbox", "shtick.muted")] if self.sandbox.contains(r.cwd) else [sep, ("outside the sandbox", "shtick.warn")]
            used = Text.assemble(*parts, *tail).cell_len + 3
            path = r.cwd or self.session.cwd
            if self.sandbox is not None and self.sandbox.contains(path):
                rel = os.path.relpath(os.path.realpath(path), self.sandbox.root)
                shown = "sandbox" if rel == "." else f"sandbox/{rel}"
                tail = []
            else:
                shown = short_path(path)
            parts += [sep, (fit_path(shown, self.width - used), "shtick.muted"), *tail]
        self.print(Text.assemble(*parts), no_wrap=True, overflow="ellipsis")
