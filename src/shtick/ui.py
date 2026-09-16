"""The interactive prompt: a full-width input bar, a context-aware key bar and a mode line."""

from __future__ import annotations

import os
import re
import sys
import time

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.cursor_shapes import ModalCursorShapeConfig
from prompt_toolkit.document import Document
from prompt_toolkit.enums import DEFAULT_BUFFER, EditingMode
from prompt_toolkit.filters import (
    Condition,
    emacs_mode,
    has_completions,
    has_focus,
    has_selection,
    vi_insert_mode,
    vi_mode,
    vi_navigation_mode,
)
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AppendAutoSuggestion,
    HighlightMatchingBracketProcessor,
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import DynamicStyle
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import SearchToolbar
from pygments.lexers.shell import BashLexer
from rich.text import Text

from shtick import __version__, lint
from shtick.banner import wordmark
from shtick.completer import FlagHinter, ShtickCompleter
from shtick.history import PromptHistory
from shtick.output import format_duration
from shtick.paths import fit_path, short_path
from shtick.shell import Shell
from shtick.syntax import has_prompts, is_complete, strip_prompts

INDENT = "  "

# Terminals only tell Shift+Enter apart from Enter when asked. While the prompt is open we request
# xterm's modifyOtherKeys (level 1), which terminals like xterm, iTerm2, WezTerm, Ghostty and tmux
# (with `extended-keys on`) honour; everyone else ignores the request.
ENABLE_MODIFIED_KEYS = "\x1b[>4;1m"
DISABLE_MODIFIED_KEYS = "\x1b[>4;0m"


def _register_modified_key_sequences() -> None:
    """Teach prompt_toolkit the modified-key encodings: modified Enter → newline, other combos → ignored."""
    for mod in range(2, 9):
        # xterm format (CSI 27;mod;code ~) and CSI-u format (CSI code;mod u)
        for seq in (f"\x1b[27;{mod};13~", f"\x1b[13;{mod}u"):
            ANSI_SEQUENCES[seq] = Keys.ControlJ
        if mod == 2:
            continue  # shifted printable keys still arrive as plain characters
        for code in (9, 27, 127, *range(32, 127)):
            for seq in (f"\x1b[27;{mod};{code}~", f"\x1b[{code};{mod}u"):
                ANSI_SEQUENCES.setdefault(seq, Keys.Ignore)


_register_modified_key_sequences()
MENU_HEIGHT = 8


def _width(fragments: StyleAndTextTuples) -> int:
    return sum(get_cwidth(f[1]) for f in fragments)


def _truncate(fragments: StyleAndTextTuples, width: int) -> StyleAndTextTuples:
    out: StyleAndTextTuples = []
    used = 0
    for style, text, *_ in fragments:
        if used + get_cwidth(text) >= width:
            out.append((style, text[: max(0, width - used - 1)] + "…"))
            break
        out.append((style, text))
        used += get_cwidth(text)
    return out


class Placeholder(Processor):
    """Faint hint text shown while the buffer is empty."""

    def __init__(self, text):
        self.text = text

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if ti.lineno == 0 and not ti.document.text:
            return Transformation([*ti.fragments, ("class:placeholder", self.text())])
        return Transformation(ti.fragments)


CLOSERS = ("done", "fi", "esac", "}", "else", "elif", ")")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _first_word(line: str) -> str:
    return re.split(r"[\s;]", line.lstrip(), maxsplit=1)[0]


def dedent_closer(doc: Document) -> Document | None:
    """Line `done`, `fi`, `}`, `else`… up with the line that opened its block. None when nothing changes."""
    row = doc.cursor_position_row
    line = doc.current_line
    if _first_word(line) not in CLOSERS:
        return None
    depth = 0
    target = None
    for above in reversed(doc.lines[:row]):
        if not above.strip():
            continue
        if _first_word(above) in CLOSERS and _first_word(above) not in ("else", "elif"):
            depth += 1
        if len(next_indent(above)) > _indent_of(above):
            if depth == 0:
                target = _indent_of(above)
                break
            depth -= 1
    if target is None or _indent_of(line) <= target:
        return None
    start = doc.translate_row_col_to_index(row, 0)
    removed = _indent_of(line) - target
    text = doc.text[:start] + line[removed:] + doc.text[start + len(line):]
    return Document(text, max(start, doc.cursor_position - removed))


def next_indent(line: str) -> str:
    """Keep the indentation; one level deeper after then/do/else/in/{/(, one less after a case item's ;;."""
    indent = line[: len(line) - len(line.lstrip(" \t"))]
    stripped = line.rstrip()
    words = stripped.split()
    if stripped.endswith(";;"):
        return indent[: max(0, len(indent) - len(INDENT))]
    if stripped.endswith(("{", "(")) or (words and words[-1] in ("then", "do", "else", "in")):
        return indent + INDENT
    return indent


class Repl:
    def __init__(self, shell: Shell):
        self.shell = shell
        self.flash: tuple[str, float] | None = None
        self.shift_enter_works = False  # flips once the terminal delivers a real Shift+Enter
        self.linter = lint.BackgroundLinter(self._invalidate)
        self.hinter = FlagHinter(self._invalidate)

        self.buffer = Buffer(
            name=DEFAULT_BUFFER,
            multiline=True,
            history=PromptHistory(shell.history),
            completer=ThreadedCompleter(ShtickCompleter(shell)),
            complete_while_typing=False,
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=Condition(lambda: "\n" not in self.buffer.text),
            on_text_changed=self._on_change,
            on_cursor_position_changed=self._on_cursor,
        )
        self.search = SearchToolbar(text_if_not_searching="", forward_search_prompt="  search ↓ ", backward_search_prompt="  search ↑ ")
        self.app = self._build_app()

    # ── state helpers ─────────────────────────────────────────────────────

    def _invalidate(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _on_change(self, _buffer: Buffer) -> None:
        self.flash = None
        if self.shell.settings.lint:
            self.linter.request(self.buffer.text, self.shell.session.kind, self.shell.settings.lint_exclude)
        self.hinter.request(self.buffer.document)

    def _on_cursor(self, _buffer: Buffer) -> None:
        self.hinter.request(self.buffer.document)

    def _incomplete(self) -> bool:
        text = self.buffer.text
        return bool(text.strip()) and not is_complete(text, self.shell.session.kind)

    # ── layout ────────────────────────────────────────────────────────────

    def _input_window(self) -> Window:
        processors = [
            HighlightMatchingBracketProcessor(chars="[](){}"),
            AppendAutoSuggestion(),
            Placeholder(lambda: "Type shell code…  %help for commands"),
        ]
        return Window(
            BufferControl(
                buffer=self.buffer,
                lexer=PygmentsLexer(BashLexer),
                input_processors=processors,
                search_buffer_control=self.search.control,
                preview_search=True,
            ),
            height=Dimension(min=1, max=16),
            wrap_lines=True,
            get_line_prefix=self._bar_prefix,
            dont_extend_height=True,
            style="class:bar",
        )

    def _build_app(self) -> Application:
        pad = lambda **kw: Window(style="class:bar", **kw)  # noqa: E731
        bar = HSplit([
            pad(height=1),
            VSplit([
                pad(width=2),
                self._input_window(),
                Window(FormattedTextControl(self._bar_counter), dont_extend_width=True, style="class:bar"),
                pad(width=2),
            ]),
            pad(height=1),
        ])
        keybar = Window(FormattedTextControl(self._keybar), height=1)
        modeline = Window(FormattedTextControl(self._modeline), height=1)
        reserve = ConditionalContainer(Window(height=MENU_HEIGHT), filter=has_completions)
        body = HSplit([bar, self.search, Window(height=1), keybar, Window(height=1), modeline, reserve])
        root = FloatContainer(
            content=body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=MENU_HEIGHT, scroll_offset=1, extra_filter=has_focus(DEFAULT_BUFFER)),
                )
            ],
        )
        app = Application(
            layout=Layout(root),
            key_bindings=self._bindings(),
            style=DynamicStyle(lambda: self.shell.theme.pt_style),
            include_default_pygments_style=False,
            erase_when_done=True,
            mouse_support=False,
            cursor=ModalCursorShapeConfig(),
        )
        app.ttimeoutlen = 0.05  # escape sequences arrive together; don't make vi users wait on Esc
        app.key_processor.after_key_press += lambda _: app.invalidate()
        return app

    def _bar_prefix(self, line_number: int, wrap_count: int) -> StyleAndTextTuples:
        if wrap_count:
            return [("class:bar.cont", "  ")]
        return [("class:bar.prompt", "> ")] if line_number == 0 else [("class:bar.cont", "· ")]

    def _bar_counter(self) -> StyleAndTextTuples:
        return [("class:bar.count", f"  [{self.shell.count + 1}]")]

    def _vi_mode(self) -> str | None:
        if self.app.editing_mode != EditingMode.VI:
            return None
        mode = self.app.vi_state.input_mode
        return "NORMAL" if mode == InputMode.NAVIGATION else "REPLACE" if mode == InputMode.REPLACE else "INSERT"

    def _keybar_items(self) -> list[tuple[str, str]]:
        if self.buffer.complete_state:
            return [("NEXT", "Tab"), ("ACCEPT", "Enter"), ("CLOSE", "Esc")]
        items: list[tuple[str, str]] = []
        mode = self._vi_mode()
        if mode == "INSERT":
            items.append(("NORMAL MODE", "Esc"))
        elif mode:
            items.append(("INSERT MODE", "i"))
        text = self.buffer.text
        script = self.shell.script
        if not text.strip() and script is not None and not script.done:
            items += [("NEXT", "%next"), ("STEP", "%step"), ("REST", "%run")]
        elif self._incomplete():
            items += [("NEWLINE", "Enter"), ("RUN ANYWAY", "Enter on 2 blank lines")]
        else:
            items += [("RUN", "Enter"), ("NEWLINE", "Shift+Enter" if self.shift_enter_works else "Alt+Enter")]
        items += [("EXIT", "Ctrl+D"), ("EDITOR", "Ctrl+O"), ("HELP", "%help")]
        return items  # least important last: they're dropped first on narrow terminals

    def _keybar(self) -> StyleAndTextTuples:
        width = self.app.output.get_size().columns - 4
        text = self.buffer.text
        if text.strip() and not self.buffer.complete_state:
            if hint := self.hinter.render(self.buffer.document, width):
                return [("", "  "), *hint]
            finding = self.linter.current(text) if self.shell.settings.lint else None
            if finding and finding.code < 2000 and self._incomplete():
                finding = None  # "couldn't parse" while the code is still being typed is just noise
            if finding:
                icon = "✗" if finding.severity == "error" else "⚠" if finding.severity == "warning" else "·"
                line = f"line {finding.line}: " if "\n" in text else ""
                out: StyleAndTextTuples = [
                    ("", "  "), (f"class:lint.{finding.severity}", f"{icon} {finding.label}"), ("class:lint.code", f"  {line}"),
                    ("class:lint.text", finding.message), ("class:keybar.sep", "  |  "), ("class:keybar.label", "DETAILS"), ("class:keybar.key", ": %lint"),
                ]
                return out if _width(out) <= width + 2 else _truncate(out[:5], width + 2)
        items = self._keybar_items()

        def render(entries: list[tuple[str, str]]) -> StyleAndTextTuples:
            out: StyleAndTextTuples = [("", "  ")]
            for i, (label, key) in enumerate(entries):
                if i:
                    out.append(("class:keybar.sep", "  |  "))
                out += [("class:keybar.label", label), ("class:keybar.key", f": {key}")]
            return out

        while len(items) > 1 and _width(render(items)) > width + 2:
            items.pop()
        return render(items)

    def _modeline(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if mode := self._vi_mode():
            out += [("class:modeline.mode", f"[{mode}]"), ("", "  ")]
        else:
            out.append(("", "  "))  # line up with the key bar
        if self.flash and time.monotonic() < self.flash[1]:
            return out + [("class:status.warn", self.flash[0])]
        shell = self.shell
        sep = ("class:status.sep", "  ·  ")
        out += [("class:status", shell.session.label)]
        sandbox = shell.sandbox
        cwd = shell.session.cwd
        if sandbox is not None and sandbox.contains(cwd):
            rel = os.path.relpath(os.path.realpath(cwd), sandbox.root)
            out += [sep, ("class:status.accent", "sandbox"), ("class:status", "" if rel == "." else f"/{rel}")]
        else:
            out += [sep, ("class:status", fit_path(short_path(cwd), max(24, self.app.output.get_size().columns // 3)))]
        if shell.last_status is not None:
            ok = shell.last_status == 0
            out += [sep, ("class:status.ok", "✓ ") if ok else ("class:status.err", "✗ "), ("class:status", f"exit {shell.last_status}")]
            if shell.last_duration is not None:
                out += [("class:status.dim", f" {format_duration(shell.last_duration)}")]
        if shell.script is not None:
            s = shell.script
            out += [sep, ("class:status.accent", s.name), ("class:status.dim", f" {min(s.pos + 1, len(s.chunks))}/{len(s.chunks)}" if not s.done else " end")]
        if shell.settings.tty:
            out += [sep, ("class:status.dim", "tty")]
        if shell.settings.lint and lint.shellcheck_path() is None:
            out += [sep, ("class:status.dim", "no shellcheck")]
        width = self.app.output.get_size().columns - 1
        return out if _width(out) <= width else _truncate(out, width)

    # ── keys ──────────────────────────────────────────────────────────────

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()
        focused = has_focus(DEFAULT_BUFFER)
        insert_mode = emacs_mode | vi_insert_mode

        @kb.add(Keys.BracketedPaste, filter=focused)
        def _paste(event: KeyPressEvent) -> None:
            data = event.data.replace("\r\n", "\n").replace("\r", "\n")
            if has_prompts(data):
                data = strip_prompts(data)
            event.current_buffer.insert_text(data)

        @kb.add("enter", filter=focused & vi_mode & vi_navigation_mode & ~has_selection)
        def _vi_enter(event: KeyPressEvent) -> None:
            if is_complete(event.current_buffer.text, self.shell.session.kind):
                self._submit(event)

        @kb.add("enter", filter=focused & ~has_selection & insert_mode)
        def _enter(event: KeyPressEvent) -> None:
            b = event.current_buffer
            state = b.complete_state
            if state and state.current_completion:
                b.apply_completion(state.current_completion)
                return
            if state:
                b.cancel_completion()
            if (dedented := dedent_closer(b.document)) is not None:
                b.document = dedented
            at_end = not b.document.text_after_cursor.strip()
            if (at_end or "\n" not in b.text) and is_complete(b.text, self.shell.session.kind):
                self._submit(event)
                return
            self._newline(b)

        @kb.add("escape", "enter", filter=focused & insert_mode)
        @kb.add("c-j", filter=focused & insert_mode)  # also Shift+Enter, via the sequences registered above
        def _newline(event: KeyPressEvent) -> None:
            if event.key_sequence[-1].data.startswith("\x1b["):
                self.shift_enter_works = True
            self._newline(event.current_buffer)

        @kb.add("tab", filter=focused & ~has_selection & insert_mode)
        def _tab(event: KeyPressEvent) -> None:
            b = event.current_buffer
            if b.complete_state:
                b.complete_next()
            elif not b.document.current_line_before_cursor.strip():
                b.insert_text(INDENT)
            else:
                b.start_completion(insert_common_part=True)

        @kb.add("s-tab", filter=focused & insert_mode)
        def _stab(event: KeyPressEvent) -> None:
            b = event.current_buffer
            if b.complete_state:
                b.complete_previous()
                return
            doc = b.document
            line = doc.current_line
            remove = min(len(INDENT), len(line) - len(line.lstrip(" ")))
            if remove:
                start = doc.translate_row_col_to_index(doc.cursor_position_row, 0)
                text = doc.text[:start] + doc.text[start + remove:]
                b.document = Document(text, max(start, doc.cursor_position - remove))

        @kb.add("escape", filter=focused & has_completions, eager=True)
        def _escape(event: KeyPressEvent) -> None:
            b = event.current_buffer
            b.cancel_completion()
            # In vi, Esc must still leave insert mode even though it also closed the menu.
            if event.app.editing_mode == EditingMode.VI and event.app.vi_state.input_mode != InputMode.NAVIGATION:
                event.app.vi_state.input_mode = InputMode.NAVIGATION
                b.cursor_position += b.document.get_cursor_left_position()

        @kb.add("c-c", filter=focused)
        def _ctrl_c(event: KeyPressEvent) -> None:
            b = event.current_buffer
            if b.text:
                b.reset()
            else:
                self.flash = ("press ctrl+d to exit", time.monotonic() + 2.5)
                event.app.invalidate()

        @kb.add("c-d", filter=focused & Condition(lambda: not self.buffer.text))
        def _ctrl_d(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        @kb.add("c-l")
        def _clear(event: KeyPressEvent) -> None:
            event.app.renderer.clear()

        @kb.add("c-o", filter=focused)
        @kb.add("f2", filter=focused)
        def _editor(event: KeyPressEvent) -> None:
            event.current_buffer.open_in_editor(validate_and_handle=False)

        return kb

    def _submit(self, event: KeyPressEvent) -> None:
        b = event.current_buffer
        text = b.text
        if text.strip():
            b.document = Document(text.rstrip())
            b.append_to_history()
        event.app.exit(result=text)

    def _newline(self, b: Buffer) -> None:
        line = b.document.current_line_before_cursor
        b.insert_text("\n" + next_indent(line))

    # ── main loop ─────────────────────────────────────────────────────────

    def banner(self, warnings: list[str] | None = None) -> None:
        shell = self.shell
        ui = shell.ui
        ui.print()
        width = ui.width
        if width >= 48:
            for line in wordmark("SHTICK_"):
                ui.print(line, overflow="crop", no_wrap=True)
        else:
            ui.print(Text("  SHTICK_", style="shtick.accent.bold"))
        ui.print()
        ui.print(Text(f"  v{__version__}", style="shtick.faint"))
        ui.print()
        s = shell.session
        rows = [("Shell", f"{s.label}  {s.path}"), ("Directory", short_path(s.cwd))]
        rows.append(("Lint", "shellcheck" if lint.shellcheck_path() else "off — install shellcheck for live linting"))
        if shell.profile is not None and shell.profile.name != "default":
            rows.append(("Profile", shell.profile.name))
        rows.append(("Theme", shell.theme.name))
        key_width = max(len(k) for k, _ in rows) + 2
        for key, value in rows:
            ui.print(Text.assemble(("  " + key.ljust(key_width), "shtick.fg"), (value, "shtick.label")), overflow="ellipsis", no_wrap=True)
        ui.print()
        for warning in warnings or []:
            shell.warn(warning)
        if warnings:
            ui.print()

    def read(self) -> str | None:
        initial, self.shell.next_input = self.shell.next_input, ""
        self.flash = None

        vi = self.shell.settings.editing_mode == "vi"
        self.app.editing_mode = EditingMode.VI if vi else EditingMode.EMACS
        # Esc is a prefix of alt+key bindings; vi users need it to take effect almost immediately.
        self.app.timeoutlen = 0.15 if vi else 1.0

        def pre_run() -> None:
            self.buffer.reset(Document(initial, len(initial)))
            if self.app.editing_mode == EditingMode.VI:
                self.app.vi_state.input_mode = InputMode.INSERT

        interactive = sys.__stdout__.isatty()
        if interactive:
            sys.__stdout__.write(ENABLE_MODIFIED_KEYS)
            sys.__stdout__.flush()
        try:
            with patch_stdout(raw=True):
                return self.app.run(pre_run=pre_run)
        finally:
            if interactive:
                # Programs run from cells (read, editors, ssh…) expect a plain keyboard.
                sys.__stdout__.write(DISABLE_MODIFIED_KEYS)
                sys.__stdout__.flush()

    def run(self, warnings: list[str] | None = None, show_banner: bool = True) -> int:
        if show_banner:
            self.banner(warnings)
        try:
            while True:
                try:
                    text = self.read()
                except (EOFError, KeyboardInterrupt):
                    break
                if text is None:
                    break
                self.shell.run_cell(text)
                if self.shell.exit_requested:
                    break
        finally:
            self.shell.close()
        self.shell.ui.print(Text("  goodbye ✓", style="shtick.faint"))
        return 0
