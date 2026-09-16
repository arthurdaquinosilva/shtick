"""Checking scripts: shellcheck reports, the sandbox, expectations and tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from shtick import lint, testing
from shtick.engine import quote
from shtick.magics import MagicError, magic, split_args, take_flags
from shtick.magics.core import flag

if TYPE_CHECKING:
    from shtick.shell import Cell, Shell


# ── %lint ─────────────────────────────────────────────────────────────────


def render_findings(shell: Shell, source: str, findings: list[lint.Finding], name: str) -> None:
    lines = source.split("\n")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        shell.print(Text.assemble(
            (f"{f.severity:<7} ", f"lint.{f.severity}"), (f.label, "shtick.accent"), ("  ", ""),
            (f"{name}:{f.line}:{f.column}", "shtick.faint"),
        ))
        if 0 < f.line <= len(lines):
            code = lines[f.line - 1].expandtabs(8)
            shell.print(Text.assemble(("  │ ", "shtick.rail"), (code, "shtick.fg")))
            width = max(1, f.end_column - f.column) if f.end_column > f.column else 1
            shell.print(Text.assemble(("  │ ", "shtick.rail"), (" " * (f.column - 1) + "^" + "~" * (width - 1), f"lint.{f.severity}")))
        shell.print(Text.assemble(("  ", ""), (f.message, "shtick.fg")))
        shell.print(Text.assemble(("  ", ""), (f.url, "shtick.faint")))
        shell.print()
    summary = " · ".join(f"{n} {sev}" for sev, n in sorted(counts.items(), key=lambda kv: lint.SEVERITY_ORDER.get(kv[0], 9)))
    shell.print(Text.assemble(("✗ " if findings else "✓ ", "shtick.warn" if findings else "shtick.ok"),
                              (summary or "no findings", "shtick.muted")))


@magic("lint", doc="shellcheck report for the last cell, a file, or the open script: %lint [FILE | script | N]",
       usage="%lint           the previous cell\n%lint N         cell N\n%lint FILE      a file\n%lint script    the open script\n\n"
             "While typing, the most important finding is shown in the key bar (%config lint=off to hide it).\n"
             "Codes in the lint_exclude setting are skipped (cells often use variables set in other cells).")
def m_lint(shell: Shell, args: str):
    if lint.shellcheck_path() is None:
        raise MagicError("shellcheck isn't installed — brew install shellcheck · apt install shellcheck")
    kind = shell.session.kind
    if lint.dialect(kind) is None:
        raise MagicError(f"shellcheck can't check {kind} scripts")
    words = split_args(args)
    exclude = shell.settings.lint_exclude
    if not words:
        cell = next((c for c in reversed(shell.cells) if c.code), None)
        if cell is None:
            raise MagicError("no cell to lint yet — %lint FILE")
        source, name = cell.code, f"[{cell.number}]"
    elif words[0].isdigit():
        cell = next((c for c in shell.cells if c.number == int(words[0])), None)
        if cell is None:
            raise MagicError(f"no cell [{words[0]}]")
        source, name = cell.code, f"[{cell.number}]"
    elif words[0] == "script" and shell.script is not None:
        source, name, exclude = shell.script.path.read_text(), shell.script.name, []
    else:
        path = Path(shell.session.cwd, os.path.expanduser(words[0]))
        try:
            source, name, exclude = path.read_text(), words[0], []
        except OSError as e:
            raise MagicError(f"can't read {words[0]}: {e.strerror or e}") from None
        if source.startswith("#!"):
            kind = _shebang_kind(source) or kind
    render_findings(shell, source, lint.check(source, kind, exclude), name)


def _shebang_kind(source: str) -> str | None:
    first = source.split("\n", 1)[0]
    for kind in ("bash", "dash", "ksh", "zsh", "sh"):
        if first.rstrip().endswith(("/" + kind, " " + kind)):
            return kind
    return None


# ── %sandbox ──────────────────────────────────────────────────────────────


@magic("sandbox", doc="run cells in a throwaway directory and show what they change: %sandbox on [--copy] · off · status",
       usage="%sandbox on          an empty temporary directory\n"
             "%sandbox on --copy   a temporary copy of the current directory\n"
             "%sandbox off         go back to the original directory and delete the sandbox\n\n"
             "After each cell, files created (+), modified (~) and deleted (−) are listed.\n"
             "It's a working-directory sandbox, not a security boundary: absolute paths and cd reach outside.")
def m_sandbox(shell: Shell, args: str):
    from shtick.sandbox import Sandbox
    from shtick.shell import short_path

    found, words = take_flags(split_args(args), "-c", "--copy")
    copy = bool(found)
    action = words[0].lower() if words else ("status" if shell.sandbox else "on")
    if action in ("status", "show"):
        if shell.sandbox is None:
            shell.print(Text.assemble(("sandbox ", "shtick.muted"), ("off", "shtick.muted")))
        else:
            sb = shell.sandbox
            shell.print(Text.assemble(("sandbox ", "shtick.muted"), ("on", "shtick.ok"), ("  ", ""), (short_path(sb.root), "repr.path"),
                                      (f"  (copy of {short_path(sb.source)})" if sb.copied else "  (started empty)", "shtick.faint")))
        return
    try:
        on = flag(action, shell.sandbox is not None)
    except MagicError:
        raise MagicError("usage: %sandbox on [--copy] · %sandbox off · %sandbox status") from None
    if on:
        if shell.sandbox is not None:
            raise MagicError("the sandbox is already on — %sandbox off first")
        source = shell.session.cwd
        try:
            sandbox = Sandbox(source, copy=copy)
        except OSError as e:
            raise MagicError(f"can't create the sandbox: {e}") from None
        result = shell.session.query(f"cd {quote(str(sandbox.root))}")
        if result.exit != 0:
            sandbox.close()
            raise MagicError(f"can't cd into the sandbox: {result.stderr.strip()}")
        shell.session.cwd = str(sandbox.root)
        shell.sandbox = sandbox
        shell.print(Text.assemble(("sandbox ", "shtick.muted"), ("on", "shtick.ok"), ("  ", ""),
                                  (f"a copy of {short_path(source)}" if copy else "an empty directory", "shtick.fg")), no_wrap=True, overflow="ellipsis")
        shell.print(Text(f"in {short_path(sandbox.root)} · deleted by %sandbox off", style="shtick.faint"), no_wrap=True, overflow="ellipsis")
    else:
        sandbox = shell.sandbox
        if sandbox is None:
            raise MagicError("the sandbox is off")
        back = sandbox.source
        if sandbox.contains(shell.session.cwd):
            shell.session.query(f"cd {quote(back)}")
            shell.session.cwd = back
        sandbox.close()
        shell.sandbox = None
        shell.print(Text.assemble(("sandbox ", "shtick.muted"), ("off", "shtick.muted"), ("  back in ", "shtick.faint"), (short_path(shell.session.cwd), "repr.path")),
                    no_wrap=True, overflow="ellipsis")


# ── %expect / %test ───────────────────────────────────────────────────────


def _last_cell(shell: Shell) -> Cell:
    cell = next((c for c in reversed(shell.cells) if c.code), None)
    if cell is None:
        raise MagicError("no cell to check yet — run some code first")
    return cell


def show_outcome(shell: Shell, text: str, outcome: testing.Outcome, indent: str = "") -> None:
    if outcome.passed:
        shell.print(Text.assemble((indent, ""), ("✓ ", "shtick.ok"), (text, "shtick.fg")))
    else:
        shell.print(Text.assemble((indent, ""), ("✗ ", "shtick.err.bold"), (text, "shtick.fg"), ("  got ", "shtick.faint"), (outcome.detail, "shtick.err")))


@magic("expect", doc='check the last cell and record it for %test: %expect exit 0 · stdout contains "done"',
       usage=testing.USAGE + "\n\n%expect           list the last cell's expectations\n%expect --clear   remove them")
def m_expect(shell: Shell, args: str):
    cell = _last_cell(shell)
    text = args.strip()
    if not text or text == "--list":
        if not cell.expectations:
            shell.print(Text(f"cell [{cell.number}] has no expectations — {testing.USAGE.splitlines()[0]}", style="shtick.muted"))
        for e in cell.expectations:
            show_outcome(shell, e.text, e.evaluate(testing.Context(cell.result, cell.changes)))
        return
    if text == "--clear":
        cell.expectations.clear()
        shell.print(Text(f"cleared the expectations of cell [{cell.number}]", style="shtick.muted"))
        return
    try:
        expectation = testing.parse(text)
    except testing.ExpectationError as e:
        raise MagicError(str(e)) from None
    outcome = expectation.evaluate(testing.Context(cell.result, cell.changes))
    cell.expectations.append(expectation)
    show_outcome(shell, f"[{cell.number}] {expectation.text}", outcome)
    shell.last_status = 0 if outcome.passed else 1


def recorded(shell: Shell) -> testing.TestFile:
    cells = [c for c in shell.cells[shell.test_start:] if c.code and c.result.syntax_error is None]
    modes = {c.sandbox for c in cells} - {"off"}
    # if any recorded cell ran in the sandbox, the whole test runs in one: never touch the real directory
    sandbox = "copy" if "copy" in modes else "empty" if modes else "off"
    return testing.TestFile(
        [testing.TestCell(c.code, [e.text for e in c.expectations], c.label) for c in cells],
        shell=shell.session.name,
        sandbox=sandbox,
    )


def print_report_cell(shell: Shell, report: testing.CellReport) -> None:
    r = report.result
    code = report.cell.code.split("\n")
    summary = code[0] + (" …" if len(code) > 1 else "")
    if not report.outcomes and r.syntax_error is None:
        note = "  (exited the shell)" if r.died else ""
        shell.print(Text.assemble(("· ", "shtick.faint"), (f"[{report.index}] ", "shtick.faint"), (summary, "shtick.faint"), (note, "shtick.faint")))
        return
    mark = ("✓ ", "shtick.ok") if report.passed else ("✗ ", "shtick.err.bold")
    shell.print(Text.assemble(mark, (f"[{report.index}] ", "shtick.muted"), (summary, "shtick.fg")))
    if r.died:
        shell.print(Text.assemble(("    ", ""), ("· ", "shtick.faint"), (f"the shell exited ({r.status}) and was restarted", "shtick.muted")))
    if r.syntax_error is not None:
        shell.print(Text.assemble(("    ", ""), ("✗ ", "shtick.err.bold"), (r.syntax_error, "shtick.err")))
    for text, outcome in report.outcomes:
        show_outcome(shell, text, outcome, indent="    ")


def print_summary(shell: Shell, report: testing.Report) -> None:
    from shtick.output import format_duration

    checks, failures = report.checks, report.failures
    if report.passed:
        shell.print(Text.assemble(("✓ ", "shtick.ok.bold"), (f"{checks} passed", "shtick.ok"),
                                  (f" · {len(report.cells)} cells · {format_duration(report.duration)}", "shtick.muted")))
    else:
        shell.print(Text.assemble(("✗ ", "shtick.err.bold"), (f"{failures} failed", "shtick.err"), (f" · {checks - min(failures, checks)} passed", "shtick.muted"),
                                  (f" · {len(report.cells)} cells · {format_duration(report.duration)}", "shtick.muted")))


@magic("test", doc="re-run the recorded cells in a fresh session and check their expectations: %test · %test reset",
       usage="Runs every cell since the session started (or since %test reset) in a new session of the same\n"
             "shell, in a fresh sandbox if the sandbox is on (otherwise in the directory shtick started in),\n"
             "and checks each cell's %expect lines. Cells without expectations still run, as setup.\n\n"
             "%test reset      start recording from here\n%test list       show what would run")
def m_test(shell: Shell, args: str):
    action = args.strip()
    if action == "reset":
        shell.test_start = len(shell.cells)
        shell.print(Text("recording tests from the next cell", style="shtick.muted"))
        return
    tf = recorded(shell)
    if not tf.cells:
        raise MagicError("no cells recorded yet — run code and add %expect lines")
    if action == "list":
        for i, cell in enumerate(tf.cells, 1):
            first = cell.code.split("\n")[0]
            shell.print(Text.assemble((f"[{i}] ", "shtick.muted"), (first, "shtick.fg"), (f"  {len(cell.expectations)} expectations", "shtick.faint")))
        return
    if action:
        raise MagicError("usage: %test · %test reset · %test list")
    if not any(c.expectations for c in tf.cells):
        shell.warn("no expectations recorded — the cells will run, but nothing is checked")
    source = shell.sandbox.source if shell.sandbox is not None else shell.start_dir
    report = testing.run(tf, source, on_cell=lambda r: print_report_cell(shell, r))
    shell.print()
    print_summary(shell, report)
    shell.last_status = 0 if report.passed else 1


@magic("save-test", doc="save the recorded cells and expectations as a test file: %save-test FILE [-f]",
       usage="Run it later with: shtick test FILE (exits non-zero on failure — for CI).\n"
             "The file is a shell script: cells are separated by #%% lines, expectations are #% expect comments.")
def m_save_test(shell: Shell, args: str):
    force, words = take_flags(split_args(args), "-f", "--force")
    if len(words) != 1:
        raise MagicError("usage: %save-test FILE [-f]")
    tf = recorded(shell)
    if not tf.cells:
        raise MagicError("no cells recorded yet")
    path = Path(shell.start_dir if shell.sandbox else shell.session.cwd, os.path.expanduser(words[0]))
    if path.exists() and not force:
        raise MagicError(f"{words[0]} exists — %save-test {words[0]} -f to overwrite")
    try:
        path.write_text(tf.dump())
        path.chmod(0o755)
    except OSError as e:
        raise MagicError(f"can't write {words[0]}: {e.strerror or e}") from None
    from shtick.shell import short_path

    n = sum(len(c.expectations) for c in tf.cells)
    shell.print(Text.assemble(("✓ ", "shtick.ok"), (f"wrote {words[0]}", "shtick.fg"), (f"  {len(tf.cells)} cells · {n} expectations", "shtick.muted")))
    shell.print(Text(f"in {short_path(path.parent)}", style="shtick.faint"), no_wrap=True, overflow="ellipsis")
    shell.print(Text(f"run it with: shtick test {words[0]}", style="shtick.faint"))
