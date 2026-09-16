"""Command-line entry point: `shtick`, `shtick SCRIPT`, `shtick -c CODE`, `shtick test FILE…`."""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from shtick import __version__
from shtick.theme import PALETTES


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["test"]:
        return test_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="shtick",
        description="A playground for writing and testing shell scripts.",
        epilog="shtick test FILE…  runs saved tests non-interactively (exit status 1 on failure).",
    )
    parser.add_argument("script", nargs="?", help="open a script to step through (%%open)")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="positional parameters for the script")
    parser.add_argument("-c", dest="command", help="run shell code as cells, print the blocks and exit")
    parser.add_argument("-s", "--shell", help="shell for the session: bash (default), sh, dash, zsh or a path")
    parser.add_argument("--profile", help="use a named profile (separate config and history)")
    parser.add_argument("--theme", choices=list(PALETTES), help="color theme")
    parser.add_argument("--vi", action="store_true", help="vi key bindings")
    parser.add_argument("--version", action="version", version=f"shtick {__version__}")
    opts = parser.parse_args(argv)

    from shtick.config import load_profile, load_settings
    from shtick.engine import ShellNotFound
    from shtick.shell import Shell

    profile = load_profile(opts.profile)
    settings, warnings = load_settings(profile)
    if opts.theme:
        settings.theme = opts.theme
    if opts.vi:
        settings.editing_mode = "vi"
    if opts.shell:
        settings.shell = opts.shell
    try:
        shell = Shell(settings=settings, profile=profile)
    except ShellNotFound as e:
        print(f"shtick: {e}", file=sys.stderr)
        return 2

    if opts.command is not None or not sys.stdin.isatty():
        code = opts.command if opts.command is not None else sys.stdin.read()
        try:
            for w in warnings:
                shell.warn(w)
            shell.run_cell(code)
            return shell.last_status or 0
        finally:
            shell.close()

    from shtick.ui import Repl

    repl = Repl(shell)
    if opts.script:
        repl.banner(warnings)
        warnings = []
        shell.run_cell("%open " + shlex.join([opts.script, *opts.args]))
        return repl.run(warnings, show_banner=False)
    return repl.run(warnings)


def test_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="shtick test", description="Run shtick test files; exit status 1 if any check fails.")
    parser.add_argument("files", nargs="+", help="test files saved with %%save-test")
    parser.add_argument("-s", "--shell", help="override the shell recorded in the files")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print failures and the summary")
    opts = parser.parse_args(argv)

    from rich.console import Console
    from rich.text import Text

    from shtick import testing
    from shtick.config import Settings
    from shtick.engine import ShellNotFound
    from shtick.magics.checks import print_report_cell, print_summary
    from shtick.theme import get_theme

    class Printer:
        """Just enough of Shell for the report printers."""

        def __init__(self) -> None:
            self.settings = Settings()
            self.ui = Console(theme=get_theme(os.environ.get("SHTICK_THEME")).rich_theme, highlight=False)

        def print(self, renderable="", end="\n", **kwargs) -> None:
            self.ui.print(renderable, end=end, **kwargs)

    out = Printer()
    failed = 0
    for name in opts.files:
        try:
            with open(name) as f:
                tf = testing.load(f.read())
        except (OSError, testing.ExpectationError) as e:
            out.print(Text.assemble(("✗ ", "shtick.err.bold"), (f"{name}: {e}", "shtick.fg")))
            failed += 1
            continue
        if opts.shell:
            tf.shell = opts.shell
        out.print(Text.assemble(("● ", "shtick.accent"), (name, "shtick.fg.bold"), (f"  {tf.shell} · {len(tf.cells)} cells", "shtick.muted")))

        def on_cell(report: testing.CellReport) -> None:
            if not opts.quiet or not report.passed:
                print_report_cell(out, report)  # type: ignore[arg-type]

        cwd = os.path.dirname(os.path.abspath(name))
        try:
            report = testing.run(tf, cwd, on_cell=on_cell)
        except ShellNotFound as e:
            out.print(Text.assemble(("✗ ", "shtick.err.bold"), (str(e), "shtick.fg")))
            failed += 1
            continue
        print_summary(out, report)  # type: ignore[arg-type]
        out.print()
        failed += 0 if report.passed else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
