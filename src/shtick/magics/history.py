"""History and saving work: %history, %save, %rerun, %recall."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from rich.syntax import Syntax
from rich.text import Text

from shtick.history import Entry
from shtick.magics import MagicError, magic, parse_args, split_args, take_flags

if TYPE_CHECKING:
    from shtick.shell import Shell


RANGES = "ranges: 4 · 4-6 · 4:7 (exclusive) · ~1/ (previous session) · ~1/2-3 · several separated by spaces"


def _is_command(source: str) -> bool:
    return source.lstrip().startswith("%")


def _entries(shell: Shell, spec: str) -> list[Entry]:
    try:
        return shell.history.get_range_by_str(spec)
    except ValueError as e:
        raise MagicError(str(e)) from None


@magic("history", "hist", doc="show history: %history [-n] [-g pattern] [-l N] [range]", usage=RANGES + "\n\n"
       "-n          show cell numbers\n-g PATTERN  search all sessions (glob; plain text matches anywhere)\n"
       "-l N        the last N entries\n-s          show exit statuses")
def m_history(shell: Shell, args: str):
    opts, rest = parse_args(args, "ng:l:s")
    if opts.get("g") is not None:
        entries = shell.history.search(str(opts["g"]), unique=True)
    elif rest:
        entries = _entries(shell, rest)
    elif opts.get("l") is not None:
        entries = shell.history.get_tail(int(opts["l"]), include_latest=True)
    else:
        entries = shell.history.get_range(shell.history.session)
    statuses = shell.history.statuses() if opts.get("s") else {}
    if not entries:
        shell.print(Text("no history yet", style="shtick.muted"))
        return
    for e in entries:
        label = f"{e.line}" if e.session == shell.history.session else f"{e.session}/{e.line}"
        prefix = Text()
        if opts.get("n") or opts.get("g") is not None or rest:
            prefix.append(f"{label:>6}  ", style="shtick.faint")
        if statuses and e.session == shell.history.session and e.line in statuses:
            st = statuses[e.line]
            prefix.append("✓ " if st == 0 else "✗ ", style="shtick.ok" if st == 0 else "shtick.err")
        shell.print(prefix, end="")
        if _is_command(e.source):
            shell.print(Text(e.source, style="shtick.accent"))
        else:
            shell.print(Syntax(e.source, "bash", theme=shell.theme.syntax_theme, background_color="default", word_wrap=True))


@magic("save", doc="write successful cells into a script: %save FILE [range] [-a] [-f]", usage=RANGES + "\n\n"
       "Without a range: every cell of this session that exited 0. %commands are never included.\n"
       "-a   include cells that failed\n-f   overwrite FILE\n\n"
       "The script starts with a shebang for the session's shell (setting: save_shebang) and is made executable.")
def m_save(shell: Shell, args: str):
    flags, words = take_flags(split_args(args), "-a", "--all", "-f", "--force", "-af", "-fa")
    if not words:
        raise MagicError("usage: %save FILE [range] [-a] [-f]")
    target, spec = words[0], " ".join(words[1:])
    statuses = shell.history.statuses()
    include_failed = bool(flags & {"-a", "--all", "-af", "-fa"})
    if spec:
        entries = [e for e in _entries(shell, spec) if not _is_command(e.source)]
    else:
        entries = [e for e in shell.history.get_range(shell.history.session) if not _is_command(e.source) and e.line in statuses]
    skipped = [e for e in entries if statuses.get(e.line, 0) != 0 and e.session == shell.history.session]
    if not include_failed:
        entries = [e for e in entries if e not in skipped]
    if not entries:
        raise MagicError("no successful cells to save" + (" in that range" if spec else ""))
    path = Path(shell.session.cwd, os.path.expanduser(target))
    if shell.sandbox is not None and not os.path.isabs(os.path.expanduser(target)):
        path = Path(shell.sandbox.source, os.path.expanduser(target))  # don't save into the throwaway directory
    if path.exists() and not flags & {"-f", "--force", "-af", "-fa"}:
        raise MagicError(f"{target} exists — %save {target} ... -f to overwrite")
    shebang = shell.settings.save_shebang or _shebang(shell)
    body = "\n\n".join(e.source.rstrip("\n") for e in entries)
    try:
        path.write_text(f"{shebang}\n\n{body}\n")
        path.chmod(path.stat().st_mode | 0o111)
    except OSError as e:
        raise MagicError(f"can't write {target}: {e.strerror or e}") from None
    from shtick.paths import short_path

    shell.print(Text.assemble(("✓ ", "shtick.ok"), (f"wrote {target}", "shtick.fg"), (f"  {len(entries)} cells · {shebang}", "shtick.muted")))
    shell.print(Text(f"in {short_path(path.parent)}", style="shtick.faint"), no_wrap=True, overflow="ellipsis")
    if skipped and not include_failed:
        shell.print(Text(f"left out {len(skipped)} failed cells: " + ", ".join(str(e.line) for e in skipped) + " — -a includes them",
                         style="shtick.faint"))


def _shebang(shell: Shell) -> str:
    s = shell.session
    if os.path.isabs(s.name) and s.name != shutil.which(os.path.basename(s.name)):
        return f"#!{s.path}"  # a specific binary, like /bin/bash
    return f"#!/usr/bin/env {s.kind if s.kind != 'sh' else 'sh'}"


@magic("rerun", doc="run earlier cells again: %rerun [range] · -l N the last N")
def m_rerun(shell: Shell, args: str):
    opts, rest = parse_args(args, "l:")
    if rest:
        entries = _entries(shell, rest)
    else:
        n = int(opts.get("l", 1))
        entries = [e for e in shell.history.get_range(shell.history.session) if not _is_command(e.source)][-n:]
    entries = [e for e in entries if not _is_command(e.source)]
    if not entries:
        raise MagicError("nothing to rerun")
    for e in entries:
        shell.execute(e.source, label=f"rerun of [{e.line}]" if e.session == shell.history.session else f"rerun of [{e.session}/{e.line}]")


@magic("recall", doc="put an earlier cell (or a range) into the input for editing: %recall [range]")
def m_recall(shell: Shell, args: str):
    spec = args.strip()
    if spec:
        entries = _entries(shell, spec)
    else:
        entries = [e for e in shell.history.get_range(shell.history.session) if not _is_command(e.source)][-1:]
    if not entries:
        raise MagicError("nothing to recall")
    shell.next_input = "\n".join(e.source for e in entries)
    shell.print(Text("in the input — edit and press enter", style="shtick.faint"))
