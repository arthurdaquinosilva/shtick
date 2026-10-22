"""The color palette shared by the prompt (prompt_toolkit), output (rich) and syntax (pygments)."""

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
    # pitch: cool blue/violet hues on a pitch-black terminal, shared by all the tools;
    # each one gets its own accent (shtick's is cyan).
    "pitch": Palette(
        name="pitch",
        fg="#c0caf5",
        muted="#737aa2",
        faint="#565f89",
        border="#2a2e42",
        surface="#0b0d14",
        selection="#161925",
        bar="#161925",
        label="#bb9af7",
        accent="#7dcfff",
        accent2="#73daca",
        ok="#9ece6a",
        err="#f7768e",
        warn="#e0af68",
        info="#7aa2f7",
        stderr="#f7768e",
        keyword="#bb9af7",
        builtin="#7dcfff",
        function="#7aa2f7",
        klass="#73daca",
        string="#9ece6a",
        number="#ff9e64",
        comment="#565f89",
        operator="#a9b1d6",
        decorator="#e0af68",
    ),
}

DEFAULT_THEME = "pitch"


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
                "rule": p.border,
                "rule.prompt": p.muted,
                "rule.prompt.cont": p.faint,
                "footer.mode": p.muted,
                "footer.hint": p.faint,
                "shortcut.key": f"bold {p.muted}",
                "shortcut.desc": p.faint,
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
                # Completions are a plain list under the prompt, like querky's: no box, accent on the current one.
                "completion-menu": p.fg,
                "completion-menu.completion": p.fg,
                "completion-menu.completion.current": f"bold {p.accent}",
                "completion-menu.meta.completion": p.faint,
                "completion-menu.meta.completion.current": p.accent,
                "completion-menu.multi-column-meta": p.muted,
                "comp.icon": p.faint,
                "comp.icon.function": p.builtin,
                "comp.icon.class": p.klass,
                "comp.icon.module": p.decorator,
                "comp.icon.keyword": p.keyword,
                "comp.icon.instance": p.string,
                "comp.icon.magic": p.accent,
                "comp.icon.path": p.accent2,
                "scrollbar.background": "",
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
