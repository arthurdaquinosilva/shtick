"""Variables and portability: %vars and %compare."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich import box
from rich.table import Table
from rich.text import Text

from shtick.engine import CellResult, Session, ShellNotFound, available_shells
from shtick.magics import MagicError, magic, split_args, take_flags

from shtick.paths import fit_path

if TYPE_CHECKING:
    from shtick.shell import Shell


# ── %vars ─────────────────────────────────────────────────────────────────

# One NUL-terminated record per variable (`declare -p` / `typeset -p` / `set` line) and per function
# (`name<TAB>checksum`), so values with newlines can't confuse the parser.
QUERIES = {
    "bash": (
        'for __shtick_n in $(compgen -v); do declare -p "$__shtick_n" 2>/dev/null; printf "\\0"; done; printf "\\1\\0"; '
        'for __shtick_n in $(compgen -A function); do printf "%s\\t" "$__shtick_n"; declare -f "$__shtick_n" | cksum; printf "\\0"; done; '
        "unset __shtick_n"
    ),
    "zsh": (
        'for __shtick_n in ${(k)parameters}; do case ${parameters[$__shtick_n]} in *special*) ;; *) typeset -p -- "$__shtick_n"; printf "\\0";; esac; done; '
        'printf "\\1\\0"; for __shtick_n in ${(k)functions}; do printf "%s\\t" "$__shtick_n"; functions -- "$__shtick_n" | cksum; printf "\\0"; done; '
        "unset __shtick_n"
    ),
    # dash and POSIX sh: `set` lists variables (values quoted, maybe over several lines); functions can't be listed
    "sh": 'set; printf "\\0\\1\\0"',
}

DYNAMIC = re.compile(
    r"^(BASH_\w+|BASHPID|BASHOPTS|COLUMNS|LINES|EPOCHREALTIME|EPOCHSECONDS|FUNCNAME|LINENO|OLDPWD|PWD|PIPESTATUS|PPID|RANDOM|"
    r"SECONDS|SRANDOM|_|HISTCMD|SHLVL|COMP_WORDBREAKS|OPTIND|OPTARG|IFS|PS4|__shtick\w*|argv|status|pipestatus|funcfiletrace|"
    r"funcsourcetrace|funcstack|functrace|zsh_eval_context|TTYIDLE|ZSH_\w+|EUID|UID|GID|EGID|ERRNO|TRY_BLOCK_\w+|BUFFER\w*)$"
)
_NAME = re.compile(r"^(?:(?:declare|typeset|local|export|readonly)\s+(?:-\S+\s+)*)?([A-Za-z_][A-Za-z0-9_]*)(?:=|$)")


@dataclass
class Snapshot:
    variables: dict[str, str] = field(default_factory=dict)  # name → declaration text
    functions: dict[str, str] = field(default_factory=dict)  # name → checksum of the body
    can_list_functions: bool = True


def _split_set_output(text: str) -> list[str]:
    """`set` output → one entry per variable; single-quoted values may span lines."""
    entries: list[str] = []
    current = ""
    for line in text.split("\n"):
        current = f"{current}\n{line}" if current else line
        if current.count("'") % 2 == 0:
            if current.strip():
                entries.append(current)
            current = ""
    return entries


def snapshot(session: Session) -> Snapshot:
    kind = session.kind if session.kind in QUERIES else "sh"
    result = session.query(QUERIES[kind])
    variables_part, _, functions_part = result.stdout.partition("\x01\x00")
    snap = Snapshot(can_list_functions=kind != "sh")
    records = _split_set_output(variables_part.strip("\x00")) if kind == "sh" else variables_part.split("\x00")
    for record in records:
        record = record.strip("\n")
        m = _NAME.match(record)
        if m and not DYNAMIC.match(m[1]):
            snap.variables[m[1]] = record
    for record in functions_part.split("\x00"):
        name, _, digest = record.strip("\n").partition("\t")
        if name and not name.startswith("__shtick"):
            snap.functions[name] = digest
    return snap


def _value(declaration: str) -> str:
    """The value part of `declare -- x="41"` / `typeset x=41` / `x='41'`."""
    _, eq, value = declaration.partition("=")
    return value if eq else ""


@magic("vars", doc="variables and functions this session defined or changed: %vars · %vars NAME · -a all",
       usage="Compares the shell's variables and functions with how they were when the session started:\n"
             "+ defined · ~ changed · − unset. Shell-managed variables (RANDOM, LINENO, PWD…) are left out.\n"
             "%vars NAME   show one declaration in full\n-a          list every variable, not just the changes")
def m_vars(shell: Shell, args: str):
    flags, words = take_flags(split_args(args), "-a", "--all")
    if shell.vars_baseline is None:
        raise MagicError("no baseline for this session")
    now = snapshot(shell.session)
    base = shell.vars_baseline
    if words:
        for name in words:
            if name in now.variables:
                shell.print(Text(now.variables[name], style="shtick.fg"))
            elif name in now.functions:
                body = shell.session.query(f"declare -f {shlex.quote(name)} 2>/dev/null || functions -- {shlex.quote(name)}").stdout
                from rich.syntax import Syntax

                shell.print(Syntax(body.rstrip(), "bash", theme=shell.theme.syntax_theme, background_color="default"))
            else:
                raise MagicError(f"{name} isn't set")
        return

    width = max(20, shell.ui.width - 8)
    rows: list[Text] = []

    def clip(text: str) -> str:
        text = text.replace("\n", "↵")
        return text if len(text) <= width else text[: width - 1] + "…"

    names = sorted(now.variables) if flags else sorted(set(now.variables) | set(base.variables))
    for name in names:
        new, old = now.variables.get(name), base.variables.get(name)
        if new is not None and old is None:
            rows.append(Text.assemble(("+ ", "shtick.added"), (name, "shtick.fg.bold"), ("=", "shtick.faint"), (clip(_value(new)), "shtick.string")))
        elif new is None and old is not None:
            rows.append(Text.assemble(("− ", "shtick.deleted"), (name, "shtick.fg.bold"), ("  unset", "shtick.faint")))
        elif new != old:
            rows.append(Text.assemble(("~ ", "shtick.modified"), (name, "shtick.fg.bold"), ("=", "shtick.faint"), (clip(_value(new or "")), "shtick.string")))
        elif flags:
            rows.append(Text.assemble(("  ", ""), (name, "shtick.fg"), ("=", "shtick.faint"), (clip(_value(new or "")), "shtick.muted")))
    if rows:
        shell.print(Text("variables", style="shtick.muted.bold"))
        for row in rows:
            shell.print(Text.assemble(("  ", ""), row), no_wrap=True, overflow="ellipsis")
    functions: list[Text] = []
    for name in sorted(set(now.functions) | set(base.functions)):
        new, old = now.functions.get(name), base.functions.get(name)
        if old is None:
            functions.append(Text.assemble(("+ ", "shtick.added"), (name, "shtick.fg.bold"), ("()", "shtick.faint")))
        elif new is None:
            functions.append(Text.assemble(("− ", "shtick.deleted"), (name, "shtick.fg.bold"), ("()  unset", "shtick.faint")))
        elif new != old:
            functions.append(Text.assemble(("~ ", "shtick.modified"), (name, "shtick.fg.bold"), ("()  redefined", "shtick.faint")))
    if functions:
        if rows:
            shell.print()
        shell.print(Text("functions", style="shtick.muted.bold"))
        for row in functions:
            shell.print(Text.assemble(("  ", ""), row))
    if not rows and not functions:
        shell.print(Text("nothing defined or changed since the session started", style="shtick.muted"))
    if not now.can_list_functions:
        shell.print(Text(f"{shell.session.kind} can't list functions, so they aren't shown", style="shtick.faint"))


# ── %compare ──────────────────────────────────────────────────────────────


def _default_shells() -> list[str]:
    shells = available_shells()
    if os.path.exists("/bin/bash") and os.path.realpath("/bin/bash") != os.path.realpath(shutil.which("bash") or ""):
        shells.insert(1, "/bin/bash")
    return shells


def _clip_lines(text: str, limit: int = 12) -> str:
    lines = text.rstrip("\n").split("\n") if text else []
    if len(lines) > limit:
        lines = lines[:limit] + [f"… {len(lines) - limit} more lines"]
    return "\n".join(lines)


@magic("compare", doc="run code in fresh sessions of several shells, side by side: %compare [shells…] [-- code]",
       usage="%compare bash dash zsh -- echo $((1 + 1))\n"
             "%compare /bin/bash bash        the previous cell, in macOS's bash 3.2 and Homebrew's bash\n"
             "%compare                       the previous cell in every installed shell\n"
             "%compare dash zsh              …with the code on the following lines\n\n"
             "Each shell gets a new session in its own empty temporary directory (--here: the current directory).\n"
             "Rows that differ between shells are highlighted.")
def m_compare(shell: Shell, args: str):
    first, _, body = args.partition("\n")
    sep = re.search(r"(?:^|\s)--(?:\s|$)", first)
    head, code = (first[: sep.start()], first[sep.end():] + ("\n" + body if body else "")) if sep else (first, body)
    here, shells = take_flags(split_args(head), "--here")
    if not code.strip():
        last = next((c for c in reversed(shell.cells) if c.code and c.number), None)
        if last is None:
            raise MagicError("no code to compare — %compare bash dash -- CODE")
        code = last.code
    shells = shells or _default_shells()
    if len(shells) < 2:
        raise MagicError("name at least two shells, e.g. %compare bash dash")

    sessions: list[Session] = []
    try:
        for name in shells:
            sessions.append(Session(name, cwd=shell.session.cwd if here else tempfile.mkdtemp(prefix="shtick-compare-")))
    except ShellNotFound as e:
        for s in sessions:
            s.close()
        raise MagicError(str(e)) from None
    kinds = [s.kind for s in sessions]
    results: list[tuple[str, CellResult]] = []
    for s in sessions:
        try:
            width = max(12, (shell.ui.width - 10) // len(sessions) - 2)
            label = s.label + (f"\n{fit_path(s.path, width)}" if kinds.count(s.kind) > 1 else "")  # bash vs /bin/bash: say which
            results.append((label, s.run(code)))
        finally:
            workdir = s.cwd
            s.close()
            if not here:
                shutil.rmtree(workdir, ignore_errors=True)

    def status(r: CellResult) -> str:
        if r.syntax_error is not None:
            return "syntax error"
        if r.died:
            return f"shell exited {r.status}"
        return f"exit {r.exit}"

    def errors(r: CellResult) -> str:
        return _clip_lines(re.sub(r"cell-\d+\.sh", "cell", r.syntax_error or r.stderr))

    rows = [
        ("status", [status(r) for _, r in results]),
        ("stdout", [_clip_lines(r.stdout) for _, r in results]),
        ("stderr", [errors(r) for _, r in results]),
    ]
    table = Table(box=box.SIMPLE_HEAD, border_style="shtick.border", header_style="shtick.accent.bold", show_edge=False, pad_edge=False, expand=True)
    table.add_column("", style="shtick.muted", no_wrap=True)
    for label, _ in results:
        table.add_column(label, overflow="fold", ratio=1)
    differing = []
    for name, values in rows:
        same = len(set(values)) == 1
        if not same:
            differing.append(name)
        cells = []
        for v in values:
            style = "shtick.faint" if same else "shtick.stderr" if name == "stderr" else "shtick.fg"
            cells.append(Text(v if v else "—", style=style if v else "shtick.faint"))
        table.add_row(Text(name, style="shtick.muted" if same else "shtick.warn.bold"), *cells)
    shell.print(table)
    if differing:
        shell.print(Text.assemble(("✗ ", "shtick.warn"), ("differs in " + ", ".join(differing), "shtick.fg")))
    else:
        shell.print(Text.assemble(("✓ ", "shtick.ok"), ("same exit status and output in all shells", "shtick.muted")))
