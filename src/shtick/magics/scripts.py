"""Opening scripts, stepping through them, editing them, and tracing execution."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from shtick.engine import quote
from shtick.magics import MagicError, magic, parse_args, split_args
from shtick.scripts import Script

if TYPE_CHECKING:
    from shtick.shell import Shell


def _script(shell: Shell) -> Script:
    if shell.script is None:
        raise MagicError("no script open — %open FILE")
    return shell.script


def show_outline(shell: Shell, around: int | None = None, context: int = 4) -> None:
    script = _script(shell)
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    grid.add_column(justify="right", style="shtick.faint", no_wrap=True)
    grid.add_column(justify="right", style="shtick.muted", no_wrap=True)
    grid.add_column(overflow="ellipsis", no_wrap=True)
    chunks = script.chunks
    breaks = script.breakpoint_chunks()
    lo, hi = 0, len(chunks)
    if around is not None and len(chunks) > 2 * context + 1:
        lo = max(0, around - context)
        hi = min(len(chunks), lo + 2 * context + 1)
        lo = max(0, hi - 2 * context - 1)
    if lo > 0:
        grid.add_row("", "", "", Text(f"… {lo} above", style="shtick.faint"))
    for i in range(lo, hi):
        chunk = chunks[i]
        current = i == script.pos
        done = i < script.pos
        marker = Text("▸", style="shtick.accent.bold") if current else Text("●", style="shtick.err.bold") if i in breaks else Text("")
        summary = Text(chunk.summary, style="shtick.fg.bold" if current else "shtick.faint" if done else "shtick.fg")
        grid.add_row(marker, str(i + 1), chunk.lines, summary)
    if hi < len(chunks):
        grid.add_row("", "", "", Text(f"… {len(chunks) - hi} below", style="shtick.faint"))
    head = Text.assemble(("", ""), (script.name, "shtick.accent.bold"), (f"  {len(chunks)} commands", "shtick.muted"))
    if script.args:
        head.append(f"  args: {shlex.join(script.args)}", style="shtick.faint")
    if script.done:
        head.append("  · at the end", style="shtick.ok")
    shell.print(head)
    shell.print(grid)


@magic("open", doc="open a script to step through it: %open FILE [args…]",
       usage="Splits FILE into top-level commands (functions, if/for blocks and heredocs stay whole).\n"
             "args become the positional parameters ($1, $2, …) in the session.\n"
             "Then: %next runs the next command · %step puts it in the input to edit first · %run runs the rest.")
def m_open(shell: Shell, args: str):
    words = split_args(args)
    if not words:
        raise MagicError("usage: %open FILE [args…]")
    path = os.path.join(shell.session.cwd, os.path.expanduser(words[0]))
    try:
        script = Script.load(path, words[1:])
    except OSError as e:
        raise MagicError(f"can't open {words[0]}: {e.strerror or e}") from None
    if not script.chunks:
        raise MagicError(f"{words[0]} has no commands")
    shell.script = script
    shell.step_pending = None
    shell.run_setup("set -- " + " ".join(quote(a) for a in script.args) if script.args else "set --", label=f"%open {script.name}")
    show_outline(shell, around=0)


def _run_chunks(shell: Shell, count: int | None, keep_going: bool) -> None:
    script = _script(shell)
    if script.done:
        raise MagicError(f"{script.name} is at the end — %goto 1 to start over")

    def stop(message: str) -> None:
        shell.warn(message)
        shell.spacing()

    ran = 0
    breaks = script.breakpoint_chunks() if count is None else set()
    while not script.done and (count is None or ran < count):
        i = script.pos
        if ran and i in breaks:
            chunk = script.chunks[i]
            if not shell._magic_cells:
                shell.print()
            shell.print(Text.assemble(("● ", "shtick.err.bold"), (f"breakpoint at {script.name}:{chunk.lines}", "shtick.fg"),
                                      ("  ", ""), (chunk.summary, "shtick.muted")))
            shell.print(Text("%next runs it · %step edits it first · %run continues · %vars shows the state", style="shtick.faint"))
            shell.spacing()
            return
        script.pos += 1
        cell = shell.execute(script.chunks[i].code, label=script.label(i), source=(script.name, script.chunks[i].start))
        ran += 1
        if shell.exit_requested:
            return
        if cell.result.died:
            stop(f"{script.name} ended the shell at command {i + 1} — stopped")
            return
        if cell.result.interrupted:
            stop("interrupted — stopped")
            return
        if not cell.ok and not keep_going and count is None:
            stop(f"command {i + 1} failed (exit {cell.result.status}) — stopped · %run -k keeps going")
            return
    if count is not None and not script.done:
        return  # the footer and the mode line already say where we are
    if script.done:
        shell.print(Text.assemble(("✓ ", "shtick.ok"), (f"reached the end of {script.name}", "shtick.muted")))
    else:
        shell.print(Text.assemble(("next ", "shtick.faint"), (f"{script.pos + 1}/{len(script.chunks)} ", "shtick.muted"),
                                  (script.chunks[script.pos].summary, "shtick.fg")))
    shell.spacing()


@magic("next", "n", doc="run the next command of the open script: %next [N]")
def m_next(shell: Shell, args: str):
    n = args.strip() or "1"
    if not n.isdigit() or int(n) < 1:
        raise MagicError("usage: %next [N]")
    _run_chunks(shell, int(n), keep_going=True)


@magic("step", "s", doc="put the next command of the open script into the input, to edit before running")
def m_step(shell: Shell, args: str):
    script = _script(shell)
    if script.done:
        raise MagicError(f"{script.name} is at the end — %goto 1 to start over")
    shell.next_input = script.chunks[script.pos].code
    shell.step_pending = script.pos
    shell.print(Text.assemble(("▸ ", "shtick.accent"), (script.label(script.pos), "shtick.label"),
                              ("  in the input: enter runs it", "shtick.faint")))


@magic("run", doc="run the rest of the open script, or open and run a file: %run [-k] [--all] [FILE [args…]]",
       usage="-k      keep going after a failing command\n--all   start from the first command")
def m_run(shell: Shell, args: str):
    opts, rest = parse_args(args, "k", "all")
    if rest:
        m_open(shell, rest)
    script = _script(shell)
    if opts.get("all"):
        script.pos = 0
    _run_chunks(shell, None, keep_going=bool(opts.get("k")))


@magic("break", "b", doc="breakpoints for %run in the open script: %break LINE… · %break · -d LINE · --clear",
       usage="%break 12 30    stop %run before the commands containing lines 12 and 30\n"
             "%break          list breakpoints\n%break -d 12    remove one\n%break --clear  remove all\n\n"
             "At a breakpoint, inspect the session (%vars, any shell code), then %next or %run to go on.")
def m_break(shell: Shell, args: str):
    script = _script(shell)
    words = split_args(args)
    if not words:
        if not script.breakpoints:
            shell.print(Text(f"no breakpoints in {script.name} — %break LINE", style="shtick.muted"))
        for line in sorted(script.breakpoints):
            i = script.chunk_at(line)
            where = f"{script.chunks[i].lines}  {script.chunks[i].summary}" if i is not None else "past the end"
            shell.print(Text.assemble(("● ", "shtick.err.bold"), (f"{script.name}:{line}", "shtick.fg"), ("  → ", "shtick.faint"), (where, "shtick.muted")))
        return
    if words == ["--clear"]:
        script.breakpoints.clear()
        shell.print(Text("breakpoints cleared", style="shtick.muted"))
        return
    remove = words[0] == "-d"
    lines = words[1:] if remove else words
    if not lines or not all(w.isdigit() and int(w) > 0 for w in lines):
        raise MagicError("usage: %break LINE… · %break -d LINE · %break --clear")
    for w in lines:
        line = int(w)
        if remove:
            script.breakpoints.discard(line)
        else:
            if script.chunk_at(line) is None:
                raise MagicError(f"{script.name} has no command at or after line {line}")
            script.breakpoints.add(line)
    show_outline(shell, around=script.chunk_at(int(lines[0])))


@magic("goto", doc="move the open script's position: %goto N (command number) · %goto +N / -N")
def m_goto(shell: Shell, args: str):
    script = _script(shell)
    a = args.strip()
    try:
        target = script.pos + int(a) if a[:1] in "+-" and a[1:].isdigit() else int(a) - 1
    except ValueError:
        raise MagicError("usage: %goto N · %goto +N · %goto -N") from None
    if not 0 <= target <= len(script.chunks):
        raise MagicError(f"{script.name} has commands 1–{len(script.chunks)}")
    script.pos = target
    shell.step_pending = None
    show_outline(shell, around=target)


@magic("script", "where", doc="show the open script's commands and the current position")
def m_script(shell: Shell, args: str):
    script = _script(shell)
    show_outline(shell, around=None if args.strip() == "all" or len(script.chunks) <= 30 else script.pos)


@magic("edit", doc="edit the open script (or FILE) in $EDITOR and reload it: %edit [FILE]")
def m_edit(shell: Shell, args: str):
    words = split_args(args)
    if words:
        path = os.path.join(shell.session.cwd, os.path.expanduser(words[0]))
    else:
        path = str(_script(shell).path)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.call([*shlex.split(editor), path])
    except OSError as e:
        raise MagicError(f"can't start {editor}: {e}") from None
    if shell.script is not None and os.path.realpath(path) == str(shell.script.path):
        try:
            shell.script.reload()
        except OSError as e:
            raise MagicError(f"can't reload: {e.strerror or e}") from None
        shell.print(Text.assemble(("reloaded ", "shtick.muted"), (shell.script.name, "shtick.accent.bold")))
        show_outline(shell, around=shell.script.pos)
    elif words:
        m_open(shell, shlex.join(words))


@magic("close", doc="close the open script")
def m_close(shell: Shell, args: str):
    script = _script(shell)
    shell.script = None
    shell.step_pending = None
    shell.print(Text.assemble(("closed ", "shtick.muted"), (script.name, "shtick.fg")))


@magic("trace", doc="run code with set -x and show each executed command: %trace [code] · body on next lines",
       usage="%trace                 trace the previous cell again\n"
             "%trace CODE            trace CODE\n"
             "%trace --next          trace the open script's next command\n"
             "%trace FILE [args…]    trace a whole script file\n\n"
             "Each command is listed with its line, nesting and function, with variables expanded,\n"
             "separately from the output.")
def m_trace(shell: Shell, args: str):
    body = args.strip("\n")
    label = ""
    source = None
    if body == "--next":
        script = _script(shell)
        if script.done:
            raise MagicError(f"{script.name} is at the end")
        label = script.label(script.pos) + "  · traced"
        body = script.chunks[script.pos].code
        source = (script.name, script.chunks[script.pos].start)
        script.pos += 1
    elif not body.strip():
        last = next((c for c in reversed(shell.cells) if c.code), None)
        if last is None:
            raise MagicError("nothing to trace yet — %trace CODE")
        body = last.code
        label = f"cell [{last.number}] again · traced"
    elif "\n" not in body:
        words = split_args(body)
        path = os.path.join(shell.session.cwd, os.path.expanduser(words[0])) if words else ""
        if path and os.path.isfile(path):
            label = f"{words[0]} · traced"
            body = "set -- " + " ".join(quote(a) for a in words[1:]) + f"\n. {quote(path)}"
    shell.execute(body, label=label or "traced", trace=True, source=source)


@magic("watch", doc="run a script again every time it's saved: %watch FILE [args…] · q or ctrl+c stops",
       usage="Each run is a new process of the session's shell (`bash FILE args`), so every run starts clean\n"
             "and an `exit` in the script doesn't end your session. After each run, shellcheck's findings are\n"
             "counted. Stop with q or Ctrl+C.\n\n"
             "--runs N   stop after N runs")
def m_watch(shell: Shell, args: str):
    import select
    import sys
    import time

    from shtick import lint
    from shtick.shell import keys_one_by_one

    words = split_args(args)
    runs_limit = None
    if "--runs" in words:
        i = words.index("--runs")
        if i + 1 >= len(words) or not words[i + 1].isdigit():
            raise MagicError("--runs expects a number")
        runs_limit = int(words[i + 1])
        del words[i: i + 2]
    if not words:
        raise MagicError("usage: %watch FILE [args…]")
    path = os.path.join(shell.session.cwd, os.path.expanduser(words[0]))
    if not os.path.isfile(path):
        raise MagicError(f"no file {words[0]}")
    shell_word = shell.session.name if not os.path.isabs(shell.session.name) else shell.session.path
    command = shlex.join([shell_word, words[0], *words[1:]])
    interactive = sys.__stdin__.isatty()

    def mtime() -> float:
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return 0

    runs = 0
    seen = mtime()
    try:
        while True:
            runs += 1
            cell = shell.execute(command, label=f"{words[0]} · run {runs} · watching")
            findings = lint.check(Path(path).read_text(errors="replace"), shell.session.kind) if lint.shellcheck_path() else []
            if findings:
                shell.print(Text.assemble(("⚠ ", "shtick.warn"), (f"shellcheck: {len(findings)} findings", "shtick.fg"),
                                          (f" · first: SC{findings[0].code} line {findings[0].line}", "shtick.muted"),
                                          (f" · %lint {words[0]}", "shtick.faint")))
            if cell.result.interrupted or (runs_limit is not None and runs >= runs_limit):
                break
            shell.print(Text(f"watching {words[0]} · saves run it again · q or ctrl+c stops", style="shtick.faint"))
            with keys_one_by_one(sys.__stdin__.fileno(), enabled=interactive):
                while mtime() == seen:
                    if interactive:
                        ready, _, _ = select.select([sys.__stdin__], [], [], 0.3)
                        if ready and os.read(sys.__stdin__.fileno(), 64) in (b"q", b"Q", b"\x04", b"\x1b"):
                            raise KeyboardInterrupt
                    else:
                        time.sleep(0.3)
                time.sleep(0.1)  # editors often write in several steps
                seen = mtime()
    except KeyboardInterrupt:
        pass
    shell.print(Text(f"stopped watching {words[0]} after {runs} runs", style="shtick.muted"))
    shell.spacing()
