"""Expectations on cells (%expect), re-running them (%test) and test files (`shtick test`).

A test file is a shell script with cells and expectations in comments, so `bash file` still
runs its code:

    #!/usr/bin/env -S shtick test
    # shtick test · shell: bash · sandbox: off
    #%% [1]
    echo hello
    #% expect exit 0
    #% expect stdout contains "hello"
"""

from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from shtick.engine import CellResult, Session

if TYPE_CHECKING:
    from shtick.sandbox import Changes

USAGE = """\
exit N · exit != N · exit nonzero · ok · fails
stdout|stderr|output  contains TEXT · not-contains TEXT · equals TEXT · matches REGEX · empty · not-empty · lines N
file exists PATH · file missing PATH · file contains PATH TEXT · dir exists PATH
duration < 2s · duration < 500ms
sandbox changed PATH · sandbox unchanged"""


class ExpectationError(ValueError):
    pass


@dataclass
class Outcome:
    passed: bool
    detail: str = ""  # what was actually found, when it failed


@dataclass
class Context:
    """What an expectation is checked against."""

    result: CellResult
    changes: Changes | None = None

    @property
    def cwd(self) -> str:
        return self.result.cwd or os.getcwd()


@dataclass
class Expectation:
    text: str
    check: Callable[[Context], Outcome] = field(repr=False)

    def evaluate(self, ctx: Context) -> Outcome:
        return self.check(ctx)


def _show(text: str, limit: int = 60) -> str:
    text = text.rstrip("\n")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return repr(text)


def _duration(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)?", value)
    if not m:
        raise ExpectationError(f"bad duration {value!r} — try 2s or 500ms")
    n = float(m[1])
    return n / 1000 if m[2] == "ms" else n * 60 if m[2] == "m" else n


def parse(text: str) -> Expectation:
    """`exit 0`, `stdout contains "done"`, `file exists out.txt`, … (see USAGE)."""
    try:
        words = shlex.split(text)
    except ValueError as e:
        raise ExpectationError(str(e)) from None
    if not words:
        raise ExpectationError("empty expectation")
    head, rest = words[0], words[1:]
    canonical = shlex.join(words)

    def need(n: int, what: str) -> None:
        if len(rest) != n:
            raise ExpectationError(f"{head} expects {what}")

    if head in ("ok", "fails"):
        need(0, "no arguments")
        want_ok = head == "ok"
        return Expectation(canonical, lambda c: Outcome((c.result.exit == 0) == want_ok, f"exit {c.result.status}"))

    if head == "exit":
        if rest == ["nonzero"]:
            return Expectation(canonical, lambda c: Outcome(c.result.status != 0, f"exit {c.result.status}"))
        negate = rest[:1] == ["!="]
        if negate:
            rest = rest[1:]
        if len(rest) != 1 or not rest[0].isdigit():
            raise ExpectationError("exit expects a number, != number, or nonzero")
        code = int(rest[0])
        return Expectation(canonical, lambda c: Outcome((c.result.status == code) != negate, f"exit {c.result.status}"))

    if head in ("stdout", "stderr", "output"):
        if not rest:
            raise ExpectationError(f"{head} expects: contains, not-contains, equals, matches, empty, not-empty or lines")
        op, args = rest[0], rest[1:]

        def stream(c: Context) -> str:
            r = c.result
            return r.stdout if head == "stdout" else r.stderr if head == "stderr" else r.stdout + r.stderr

        if op in ("empty", "not-empty"):
            if args:
                raise ExpectationError(f"{op} takes no argument")
            empty = op == "empty"
            return Expectation(canonical, lambda c: Outcome((stream(c) == "") == empty, f"{head} was {_show(stream(c))}"))
        if len(args) != 1:
            raise ExpectationError(f"{head} {op} expects one argument (quote it)")
        arg = args[0]
        if op == "contains":
            return Expectation(canonical, lambda c: Outcome(arg in stream(c), f"{head} was {_show(stream(c))}"))
        if op == "not-contains":
            return Expectation(canonical, lambda c: Outcome(arg not in stream(c), f"{head} was {_show(stream(c))}"))
        if op == "equals":
            return Expectation(canonical, lambda c: Outcome(stream(c).rstrip("\n") == arg.rstrip("\n"), f"{head} was {_show(stream(c))}"))
        if op == "matches":
            try:
                pattern = re.compile(arg, re.M)
            except re.error as e:
                raise ExpectationError(f"bad regex: {e}") from None
            return Expectation(canonical, lambda c: Outcome(bool(pattern.search(stream(c))), f"{head} was {_show(stream(c))}"))
        if op == "lines":
            if not arg.isdigit():
                raise ExpectationError("lines expects a number")
            n = int(arg)
            return Expectation(canonical, lambda c: Outcome(len(stream(c).splitlines()) == n, f"{head} had {len(stream(c).splitlines())} lines"))
        raise ExpectationError(f"unknown {head} check {op!r}")

    if head in ("file", "dir"):
        if not rest:
            raise ExpectationError(f"{head} expects: exists PATH" + (", missing PATH, contains PATH TEXT" if head == "file" else ""))
        op, args = rest[0], rest[1:]

        def path(c: Context, p: str) -> str:
            return os.path.join(c.cwd, os.path.expanduser(p))

        if op == "exists" and len(args) == 1:
            test = os.path.isdir if head == "dir" else os.path.isfile
            return Expectation(canonical, lambda c: Outcome(test(path(c, args[0])), f"no {head} {args[0]}"))
        if op == "missing" and len(args) == 1:
            return Expectation(canonical, lambda c: Outcome(not os.path.lexists(path(c, args[0])), f"{args[0]} exists"))
        if head == "file" and op == "contains" and len(args) == 2:
            def contains(c: Context) -> Outcome:
                try:
                    content = Path(path(c, args[0])).read_text(errors="replace")
                except OSError as e:
                    return Outcome(False, e.strerror or str(e))
                return Outcome(args[1] in content, f"{args[0]} was {_show(content)}")

            return Expectation(canonical, contains)
        raise ExpectationError(f"usage: {head} exists PATH" + (" · file missing PATH · file contains PATH TEXT" if head == "file" else ""))

    if head == "duration":
        if len(rest) != 2 or rest[0] not in ("<", "<=", ">", ">="):
            raise ExpectationError("duration expects < or > and a time, e.g. duration < 2s")
        limit, op = _duration(rest[1]), rest[0]
        compare = {"<": float.__lt__, "<=": float.__le__, ">": float.__gt__, ">=": float.__ge__}[op]
        return Expectation(canonical, lambda c: Outcome(compare(float(c.result.duration), limit), f"took {c.result.duration:.3f}s"))

    if head == "sandbox":
        if rest == ["unchanged"]:
            return Expectation(canonical, lambda c: Outcome(c.changes is not None and not c.changes, _changes(c)))
        if len(rest) == 2 and rest[0] == "changed":
            target = rest[1].rstrip("/")

            def changed(c: Context) -> Outcome:
                if c.changes is None:
                    return Outcome(False, "the sandbox off")
                paths = [p.rstrip("/") for p in c.changes.added + c.changes.modified + c.changes.deleted]
                return Outcome(target in paths, _changes(c))

            return Expectation(canonical, changed)
        raise ExpectationError("usage: sandbox changed PATH · sandbox unchanged")

    raise ExpectationError(f"unknown expectation {head!r} — see %help expect")


def _changes(c: Context) -> str:
    if c.changes is None:
        return "the sandbox off"
    ch = c.changes
    parts = [f"+{p}" for p in ch.added] + [f"~{p}" for p in ch.modified] + [f"-{p}" for p in ch.deleted]
    return "changes: " + (" ".join(parts) if parts else "none")


# ── test files ────────────────────────────────────────────────────────────


@dataclass
class TestCell:
    code: str
    expectations: list[str] = field(default_factory=list)
    label: str = ""


@dataclass
class TestFile:
    cells: list[TestCell]
    shell: str = "bash"
    sandbox: str = "off"  # off · empty · copy

    def dump(self) -> str:
        out = [
            "#!/usr/bin/env -S shtick test",
            f"# shtick test · shell: {self.shell} · sandbox: {self.sandbox}",
        ]
        for i, cell in enumerate(self.cells, 1):
            out.append(f"#%% [{i}]" + (f" {cell.label}" if cell.label else ""))
            out.append(cell.code.rstrip("\n"))
            out += [f"#% expect {e}" for e in cell.expectations]
        return "\n".join(out) + "\n"


_HEADER = re.compile(r"^# shtick test\b(?P<rest>.*)$")


def load(text: str) -> TestFile:
    tf = TestFile([])
    current: TestCell | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        if m := _HEADER.match(line):
            for part in m["rest"].split("·"):
                key, _, value = part.strip().partition(":")
                if key.strip() == "shell" and value.strip():
                    tf.shell = value.strip()
                elif key.strip() == "sandbox" and value.strip():
                    tf.sandbox = value.strip()
            continue
        if line.startswith("#%%"):
            label = re.sub(r"^#%%\s*(\[\d+\])?\s*", "", line)
            current = TestCell("", label=label)
            tf.cells.append(current)
            continue
        if line.startswith("#% expect "):
            if current is None:
                raise ExpectationError("expectation before the first #%% cell")
            current.expectations.append(line[len("#% expect "):].strip())
            continue
        if current is None:
            continue  # shebang and header comments
        current.code += line + "\n"
    for cell in tf.cells:
        cell.code = cell.code.strip("\n")
    return tf


@dataclass
class CellReport:
    index: int
    cell: TestCell
    result: CellResult
    outcomes: list[tuple[str, Outcome]]

    @property
    def passed(self) -> bool:
        return all(o.passed for _, o in self.outcomes) and self.result.syntax_error is None


@dataclass
class Report:
    cells: list[CellReport]
    duration: float

    @property
    def checks(self) -> int:
        return sum(len(c.outcomes) for c in self.cells)

    @property
    def failures(self) -> int:
        return sum(1 for c in self.cells for _, o in c.outcomes if not o.passed) + sum(
            1 for c in self.cells if c.result.syntax_error is not None
        )

    @property
    def passed(self) -> bool:
        return self.failures == 0


def run(tf: TestFile, cwd: str, on_cell: Callable[[CellReport], None] | None = None) -> Report:
    """Run a test file's cells in a fresh session and check the expectations."""
    from shtick.sandbox import Sandbox

    sandbox = Sandbox(cwd, copy=tf.sandbox == "copy") if tf.sandbox in ("empty", "copy") else None
    start = time.perf_counter()
    reports: list[CellReport] = []
    try:
        with Session(tf.shell, cwd=str(sandbox.root) if sandbox else cwd) as session:
            for i, cell in enumerate(tf.cells, 1):
                before = sandbox.snapshot() if sandbox else None
                result = session.run(cell.code)
                changes = sandbox.diff(before, sandbox.snapshot()) if sandbox and before is not None else None
                ctx = Context(result, changes)
                outcomes = []
                for text in cell.expectations:
                    try:
                        outcomes.append((text, parse(text).evaluate(ctx)))
                    except ExpectationError as e:
                        outcomes.append((text, Outcome(False, f"invalid expectation: {e}")))
                report = CellReport(i, cell, result, outcomes)
                reports.append(report)
                if on_cell:
                    on_cell(report)
    finally:
        if sandbox:
            sandbox.close()
    return Report(reports, time.perf_counter() - start)
