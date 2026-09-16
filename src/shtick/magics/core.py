"""General commands: help, settings, themes, the session's shell and terminal mode."""

from __future__ import annotations

import sys
from dataclasses import fields
from typing import TYPE_CHECKING

from rich import box
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shtick.config import Settings, setting_names
from shtick.engine import ShellNotFound, available_shells
from shtick.magics import MAGICS, ORDER, MagicError, MagicSpec, magic, magic_docs, split_args
from shtick.theme import PALETTES

if TYPE_CHECKING:
    from shtick.shell import Shell


def flag(value: str, current: bool) -> bool:
    value = value.strip().lower()
    if not value:
        return not current
    if value in ("on", "1", "true", "yes"):
        return True
    if value in ("off", "0", "false", "no"):
        return False
    raise MagicError(f"expected on/off, got {value!r}")


def state(on: bool) -> tuple[str, str]:
    return ("on", "shtick.ok") if on else ("off", "shtick.muted")


KEYS = [
    ("enter", "run the cell (inserts a newline while the code is unfinished)"),
    ("shift+enter", "insert a newline (or alt+enter / ctrl+j)"),
    ("tab", "complete commands, files, $variables and %commands"),
    ("↑ ↓", "history (prefix-aware)"),
    ("ctrl+r", "search history"),
    ("ctrl+o", "edit the cell in $EDITOR"),
    ("ctrl+c", "clear the input · interrupt a running cell (twice: kill the session)"),
    ("ctrl+d", "exit · end a running cell's stdin"),
    ("ctrl+l", "clear the screen"),
]


def help_panel() -> RenderableType:
    def grid(rows: list[tuple[str, str]]) -> Table:
        g = Table.grid(padding=(0, 2))
        g.add_column(style="shtick.accent.bold", no_wrap=True)
        g.add_column(style="shtick.muted")
        for k, v in rows:
            g.add_row(k, v)
        return g

    def section(title: str, body: RenderableType) -> RenderableType:
        return Group(Text(title, style="shtick.fg.bold"), Padding(body, (0, 0, 1, 2)))

    docs = magic_docs()
    parts = [section("keys", grid(KEYS))]
    for category in sorted(docs, key=lambda c: ORDER.index(c) if c in ORDER else len(ORDER)):
        parts.append(section(category, grid(sorted(docs[category].items()))))
    parts.append(Text("%help name shows details for one command", style="shtick.faint"))
    return Panel(
        Group(*parts),
        title=Text.assemble(("✓ ", "shtick.accent"), ("shtick", "shtick.fg.bold"), (" help", "shtick.muted")),
        title_align="left",
        border_style="shtick.border",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def command_help(spec: MagicSpec) -> RenderableType:
    body: list[RenderableType] = [Text(spec.doc, style="shtick.fg")]
    if spec.usage:
        body += [Text(""), Text(spec.usage, style="shtick.muted")]
    if spec.aliases:
        body += [Text(""), Text("also: " + ", ".join("%" + a for a in spec.aliases), style="shtick.faint")]
    return Panel(
        Group(*body),
        title=Text.assemble(("✓ ", "shtick.accent"), ("%" + spec.name, "shtick.fg.bold")),
        title_align="left",
        border_style="shtick.border",
        box=box.ROUNDED,
        padding=(0, 1),
    )


@magic("help", doc="keys and commands · %help name for one command")
def m_help(shell: Shell, args: str):
    name = args.strip().lstrip("%")
    if not name:
        shell.print(help_panel())
        return
    spec = MAGICS.get(name)
    if spec is None:
        raise MagicError(f"no command named %{name}")
    shell.print(command_help(spec))


@magic("theme", doc="list or switch color themes: %theme · %theme nebula")
def m_theme(shell: Shell, args: str):
    name = args.strip()
    if not name:
        for n, p in PALETTES.items():
            mark = "●" if n == shell.theme.name else "○"
            swatch = Text.assemble(*[("██", c) for c in (p.accent, p.accent2, p.keyword, p.string, p.builtin, p.number)])
            shell.print(Text.assemble((f"{mark} ", p.accent), (f"{n:<10}", "shtick.fg.bold"), swatch))
        return
    try:
        shell.set_setting("theme", name)
    except ValueError as e:
        raise MagicError(str(e)) from None
    shell.print(Text.assemble(("theme → ", "shtick.muted"), (name, "shtick.accent.bold")))


@magic("config", doc="show or change settings: %config · %config name · %config name=value",
       usage="Settings for this session. To keep them, put them in config.toml (see %config).")
def m_config(shell: Shell, args: str):
    a = args.strip()
    if not a:
        table = Table(box=box.SIMPLE_HEAD, border_style="shtick.border", header_style="shtick.accent.bold", show_edge=False, pad_edge=False)
        table.add_column("setting", style="shtick.fg.bold", no_wrap=True)
        table.add_column("value", style="shtick.string")
        for f in fields(Settings):
            table.add_row(f.name, repr(getattr(shell.settings, f.name)))
        shell.print(table)
        if shell.profile:
            from shtick.paths import short_path

            shell.print(Text(f"saved defaults live in {short_path(shell.profile.config_file)}", style="shtick.faint"))
        return
    key, eq, value = a.partition("=")
    key = key.strip()
    if key not in setting_names():
        raise MagicError(f"unknown setting {key!r} — see %config")
    if not eq:
        shell.print(Text.assemble((key, "shtick.fg.bold"), (" = ", "shtick.faint"), (repr(getattr(shell.settings, key)), "shtick.string")))
        return
    try:
        shell.set_setting(key, value.strip())
    except ValueError as e:
        raise MagicError(str(e)) from None
    shell.print(Text.assemble((key, "shtick.fg.bold"), (" = ", "shtick.faint"), (repr(getattr(shell.settings, key)), "shtick.string")))


@magic("shell", doc="show or switch the session's shell: %shell dash · %shell /bin/bash",
       usage="Switching starts a fresh session in the same directory: variables, functions and options don't carry over.")
def m_shell(shell: Shell, args: str):
    name = args.strip()
    s = shell.session
    if not name:
        shell.print(Text.assemble(("shell ", "shtick.muted"), (s.label, "shtick.accent.bold"), (f"  {s.path}", "shtick.faint")))
        others = [n for n in available_shells()]
        shell.print(Text("installed: " + ", ".join(others), style="shtick.faint"))
        return
    try:
        shell.switch_shell(name)
    except ShellNotFound as e:
        raise MagicError(str(e)) from None
    shell.print(Text.assemble(("shell → ", "shtick.muted"), (shell.session.label, "shtick.accent.bold"), (f"  {shell.session.path}", "shtick.faint")))
    shell.print(Text("fresh session: earlier variables and functions are gone", style="shtick.faint"))


@magic("restart", doc="restart the shell session (clears variables, functions, options)")
def m_restart(shell: Shell, args: str):
    shell.restart()
    shell.print(Text.assemble(("session restarted · ", "shtick.muted"), (shell.session.label, "shtick.accent.bold")))


@magic("tty", doc="run cells attached to a terminal (merges stdout and stderr): %tty on|off",
       usage="For programs that check [ -t 1 ] or only print colors on a terminal. stderr is merged into stdout.")
def m_tty(shell: Shell, args: str):
    shell.set_setting("tty", flag(args, shell.settings.tty))
    shell.print(Text.assemble(("tty mode ", "shtick.muted"), state(shell.settings.tty)))


@magic("clear", "cls", doc="clear the screen")
def m_clear(shell: Shell, args: str):
    sys.__stdout__.write("\x1b[2J\x1b[3J\x1b[H")
    sys.__stdout__.flush()


@magic("editmode", "vi", "emacs", doc="switch key bindings: %editmode vi|emacs (or %vi / %emacs)")
def m_editmode(shell: Shell, args: str):
    words = split_args(args)
    mode = words[0].lower() if words else shell.current_command
    if mode not in ("vi", "emacs"):
        raise MagicError("editmode must be vi or emacs")
    shell.settings.editing_mode = mode
    shell.print(Text.assemble(("editing mode → ", "shtick.muted"), (mode, "shtick.accent.bold")))


@magic("exit", "quit", doc="leave shtick")
def m_exit(shell: Shell, args: str):
    shell.exit_requested = True
