"""Color palettes shared by the prompt (prompt_toolkit), output (rich) and syntax (pygments)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.styles import merge_styles, style_from_pygments_cls
from pygments.style import Style as PygmentsStyle
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Token,
)
from rich.syntax import PygmentsSyntaxTheme
from rich.theme import Theme as RichTheme


@dataclass(frozen=True)
class Palette:
    name: str
    fg: str
    muted: str
    faint: str
    border: str
    surface: str
    selection: str
    bar: str  # filled input bar
    label: str  # key-bar labels and banner values
    accent: str
    accent2: str
    ok: str
    err: str
    warn: str
    info: str
    stderr: str  # text written to stderr
    # syntax
    keyword: str
    builtin: str
    function: str
    klass: str
    string: str
    number: str
    comment: str
    operator: str
    decorator: str


PALETTES: dict[str, Palette] = {
    # Blue-black with a teal accent, lime and amber highlights. The default.
    "tide": Palette(
        name="tide",
        fg="#d8e2ec",
        muted="#8a9aab",
        faint="#56657a",
        border="#2e3a4a",
        surface="#111821",
        selection="#22303f",
        bar="#1b2430",
        label="#f2c46d",
        accent="#3ddbc4",
        accent2="#a6e36e",
        ok="#7ee787",
        err="#ff7b72",
        warn="#f2c46d",
        info="#79c0ff",
        stderr="#ffa8a1",
        keyword="#3ddbc4",
        builtin="#79c0ff",
        function="#e6edf3",
        klass="#f2c46d",
        string="#a6e36e",
        number="#d2a8ff",
        comment="#5c6b7d",
        operator="#93a4b8",
        decorator="#d2a8ff",
    ),
    # Amber on dark, like an old CRT terminal.
    "phosphor": Palette(
        name="phosphor",
        fg="#ffdcaa",
        muted="#b88f56",
        faint="#7d5f36",
        border="#4a3720",
        surface="#1a1208",
        selection="#33240f",
        bar="#261b0c",
        label="#ffd166",
        accent="#ffb000",
        accent2="#ff7a1a",
        ok="#c5e063",
        err="#ff5f45",
        warn="#ffd166",
        info="#ffcc80",
        stderr="#ff9e80",
        keyword="#ffb000",
        builtin="#ffcc80",
        function="#ffe8c7",
        klass="#ff7a1a",
        string="#e3c27d",
        number="#ff9e40",
        comment="#7d5f36",
        operator="#c79a5a",
        decorator="#ff7a1a",
    ),
    # For light terminal backgrounds.
    "chalk": Palette(
        name="chalk",
        fg="#1f2328",
        muted="#57606a",
        faint="#8c959f",
        border="#c9d1d9",
        surface="#f6f8fa",
        selection="#d8e6f5",
        bar="#eaeef2",
        label="#8250df",
        accent="#0a7f73",
        accent2="#bf5700",
        ok="#1a7f37",
        err="#cf222e",
        warn="#9a6700",
        info="#0969da",
        stderr="#a40e26",
        keyword="#cf222e",
        builtin="#0969da",
        function="#1f2328",
        klass="#8250df",
        string="#0a3069",
        number="#953800",
        comment="#6e7781",
        operator="#57606a",
        decorator="#8250df",
    ),
}

DEFAULT_THEME = "tide"


class Theme:
    def __init__(self, palette: Palette):
        self.p = palette

    @property
    def name(self) -> str:
        return self.p.name

    @cached_property
    def pygments_style(self) -> type[PygmentsStyle]:
        p = self.p
        return type(
            f"Shtick{p.name.title()}Style",
            (PygmentsStyle,),
            {
                "background_color": None,
                "highlight_color": p.selection,
                "styles": {
                    Token: p.fg,
                    Text: p.fg,
                    Comment: f"italic {p.comment}",
                    Keyword: p.keyword,
                    Keyword.Constant: p.number,
                    Keyword.Namespace: p.keyword,
                    Operator: p.operator,
                    Operator.Word: p.keyword,
                    Punctuation: p.operator,
                    Name: p.fg,
                    Name.Builtin: p.builtin,
                    Name.Variable: p.accent2,
                    Name.Builtin.Pseudo: f"italic {p.builtin}",
                    Name.Function: p.function,
                    Name.Function.Magic: p.builtin,
                    Name.Class: f"bold {p.klass}",
                    Name.Decorator: p.decorator,
                    Name.Exception: p.klass,
                    Name.Namespace: p.klass,
                    Name.Variable.Magic: p.builtin,
                    String: p.string,
                    String.Doc: f"italic {p.comment}",
                    String.Escape: p.number,
                    String.Interpol: p.accent2,
                    String.Affix: p.keyword,
                    Number: p.number,
                    Generic.Deleted: p.err,
                    Generic.Inserted: p.ok,
                    Generic.Heading: f"bold {p.fg}",
                    Generic.Subheading: p.muted,
                    Generic.Error: p.err,
                    Generic.Traceback: p.err,
                    Error: p.err,
                },
            },
        )

    @cached_property
    def syntax_theme(self) -> PygmentsSyntaxTheme:
        return PygmentsSyntaxTheme(self.pygments_style)

    @cached_property
    def pt_style(self) -> PTStyle:
        p = self.p
        ui = PTStyle.from_dict(
            {
                "": p.fg,
                "bar": f"bg:{p.bar}",
                "bar.prompt": f"bg:{p.bar} bold {p.accent}",
                "bar.cont": f"bg:{p.bar} {p.faint}",
                "bar.count": f"bg:{p.bar} {p.faint}",
                "placeholder": p.faint,
                "keybar.label": f"bold {p.label}",
                "keybar.key": p.fg,
                "keybar.sep": p.faint,
                "modeline.mode": f"bold {p.label}",
                "box.border": p.border,
                "box.border.active": p.faint,
                "box.title": f"bold {p.accent}",
                "box.label": p.faint,
                "prompt": f"bold {p.accent}",
                "prompt.cont": p.faint,
                "status": p.muted,
                "status.dim": p.faint,
                "status.accent": p.accent,
                "status.ok": p.ok,
                "status.err": p.err,
                "status.warn": p.warn,
                "status.key": f"{p.fg}",
                "status.sep": p.faint,
                "lint.error": f"bold {p.err}",
                "lint.warning": f"bold {p.warn}",
                "lint.info": f"bold {p.info}",
                "lint.style": f"bold {p.muted}",
                "lint.text": p.fg,
                "lint.code": p.faint,
                "flag.name": f"bold {p.builtin}",
                "flag.current": f"bold underline {p.accent}",
                "flag.text": p.muted,
                "sig.icon": p.accent,
                "sig.name": f"bold {p.builtin}",
                "sig.param": p.muted,
                "sig.param.current": f"bold underline {p.accent}",
                "sig.type": p.klass,
                "sig.type.inferred": f"italic {p.muted}",
                "sig.return": f"bold {p.klass}",
                "sig.default": p.faint,
                "sig.punct": p.faint,
                "auto-suggestion": p.faint,
                "matching-bracket.cursor": f"bg:{p.selection} bold",
                "matching-bracket.other": f"bg:{p.selection} bold",
                "selected": f"bg:{p.selection}",
                "completion-menu": f"bg:{p.surface} {p.fg}",
                "completion-menu.completion": f"bg:{p.surface} {p.fg}",
                "completion-menu.completion.current": f"bg:{p.selection} bold {p.accent}",
                "completion-menu.meta.completion": f"bg:{p.surface} {p.faint}",
                "completion-menu.meta.completion.current": f"bg:{p.selection} {p.muted}",
                "completion-menu.multi-column-meta": f"bg:{p.surface} {p.muted}",
                "comp.icon": p.faint,
                "comp.icon.function": p.builtin,
                "comp.icon.class": p.klass,
                "comp.icon.module": p.decorator,
                "comp.icon.keyword": p.keyword,
                "comp.icon.instance": p.string,
                "comp.icon.magic": p.accent,
                "comp.icon.path": p.accent2,
                "scrollbar.background": f"bg:{p.surface}",
                "scrollbar.button": f"bg:{p.border}",
                "search": f"bg:{p.selection}",
                "search.current": f"bg:{p.accent} #000000",
                "search-toolbar": p.muted,
                "search-toolbar.prompt": f"bold {p.accent}",
                "search-toolbar.text": p.fg,
            }
        )
        return merge_styles([style_from_pygments_cls(self.pygments_style), ui])

    @cached_property
    def rich_theme(self) -> RichTheme:
        p = self.p
        return RichTheme(
            {
                "shtick.fg": p.fg,
                "shtick.muted": p.muted,
                "shtick.faint": p.faint,
                "shtick.border": p.border,
                "shtick.accent": p.accent,
                "shtick.accent2": p.accent2,
                "shtick.ok": p.ok,
                "shtick.err": p.err,
                "shtick.warn": p.warn,
                "shtick.info": p.info,
                "shtick.label": f"bold {p.label}",
                "shtick.accent.bold": f"bold {p.accent}",
                "shtick.accent.blink": f"bold blink {p.accent}",
                "shtick.err.bold": f"bold {p.err}",
                "shtick.fg.bold": f"bold {p.fg}",
                "shtick.info.bold": f"bold {p.info}",
                "shtick.keyword": p.keyword,
                "shtick.class": p.klass,
                "shtick.string": p.string,
                "shtick.warn.bold": f"bold {p.warn}",
                "shtick.ok.bold": f"bold {p.ok}",
                "shtick.muted.bold": f"bold {p.muted}",
                "shtick.rail": p.border,
                "shtick.rail.err": p.err,
                "shtick.input": f"bold {p.accent2}",
                "shtick.stderr": p.stderr,
                "shtick.trace": p.muted,
                "shtick.trace.loc": p.faint,
                "shtick.trace.depth": p.border,
                "shtick.added": p.ok,
                "shtick.modified": p.warn,
                "shtick.deleted": p.err,
                "repr.path": p.decorator,
                "table.header": f"bold {p.accent}",
                "table.footer": p.muted,
                "table.caption": f"italic {p.muted}",
                "rule.line": p.border,
            }
        )


def get_theme(name: str | None) -> Theme:
    return Theme(PALETTES.get(name or DEFAULT_THEME, PALETTES[DEFAULT_THEME]))
