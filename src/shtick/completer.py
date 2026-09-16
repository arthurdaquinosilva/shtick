"""Completion (commands, files, $variables, %commands) and flag hints for the key bar."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth

from shtick.magics import MAGICS

if TYPE_CHECKING:
    from shtick.shell import Shell

KEYWORDS = ("if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in", "function", "select", "time")
BUILTINS = (
    "alias", "bg", "break", "builtin", "cd", "command", "continue", "declare", "echo", "eval", "exec", "exit", "export",
    "false", "fg", "getopts", "hash", "jobs", "kill", "local", "printf", "pwd", "read", "readonly", "return", "set",
    "shift", "shopt", "source", "test", "trap", "true", "type", "typeset", "ulimit", "umask", "unalias", "unset", "wait",
)
PATH_COMMANDS = ("open", "edit", "lint", "run", "trace", "save", "save-test")

# Where a new simple command starts: line start, after | ; & && || ( $( ` and keywords.
_COMMAND_START = re.compile(r"(?:^|[|;&(`]|\$\(|\b(?:then|do|else|elif|if|while|until)\s)\s*(?:(?:sudo|env|exec|time|command|nohup)\s+|[A-Za-z_]\w*=\S*\s+)*$")
_VAR = re.compile(r"\$\{?([A-Za-z_]\w*)?$")


def _path_executables() -> list[str]:
    names: set[str] = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            for entry in os.scandir(d):
                if entry.is_file() and os.access(entry.path, os.X_OK):
                    names.add(entry.name)
        except OSError:
            continue
    return sorted(names)


class SessionNames:
    """Functions, aliases and variables defined in the session, refreshed after cells run."""

    QUERIES = {
        "bash": "compgen -A function; echo ---; compgen -a; echo ---; compgen -v",
        "zsh": "print -rl -- ${(k)functions}; echo ---; print -rl -- ${(k)aliases}; echo ---; print -rl -- ${(k)parameters}",
        "default": "echo ---; alias | sed 's/=.*//; s/^alias //'; echo ---; set | sed -n 's/^\\([A-Za-z_][A-Za-z0-9_]*\\)=.*/\\1/p'",
    }

    def __init__(self, shell: Shell):
        self.shell = shell
        self._key: tuple[int, int] | None = None
        self.functions: list[str] = []
        self.aliases: list[str] = []
        self.variables: list[str] = []

    def refresh(self) -> None:
        s = self.shell
        key = (id(s.session), s.count)
        if key == self._key:
            return
        self._key = key
        query = self.QUERIES.get(s.session.kind, self.QUERIES["default"])
        try:
            out = s.session.query(query).stdout
        except Exception:
            return
        parts = (out.split("---\n") + ["", "", ""])[:3]
        clean = lambda text: sorted({w for w in text.split() if not w.startswith("__shtick")})  # noqa: E731
        self.functions, self.aliases, self.variables = clean(parts[0]), clean(parts[1]), clean(parts[2])


class ShtickCompleter(Completer):
    def __init__(self, shell: Shell):
        self.shell = shell
        self.names = SessionNames(shell)
        self.paths = PathCompleter(expanduser=True, get_paths=lambda: [shell.session.cwd])
        self._commands: list[str] | None = None

    @property
    def commands(self) -> list[str]:
        if self._commands is None:
            self._commands = _path_executables()
        return self._commands

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        before = document.text_before_cursor
        line = document.current_line_before_cursor

        if document.text.lstrip().startswith("%") and "\n" not in before:
            m = re.match(r"^\s*%([\w-]*)$", before)
            if m:
                typed = m[1]
                seen = set()
                for name, spec in sorted(MAGICS.items()):
                    if name.startswith(typed) and spec.name not in seen:
                        seen.add(spec.name)
                        yield Completion("%" + name, start_position=-len(typed) - 1, display_meta=spec.doc.split(":")[0][:40])
                return
            name = re.match(r"^\s*%([\w-]+)", before)
            if name and name[1] in PATH_COMMANDS:
                yield from self._paths(document)
            return

        if m := _VAR.search(before):
            typed = m[1] or ""
            self.names.refresh()
            names = sorted(set(self.names.variables) | set(os.environ))
            for n in names:
                if n.startswith(typed):
                    yield Completion(n, start_position=-len(typed), display_meta="variable")
            return

        word = re.search(r"[^\s|;&()<>`'\"]*$", line)
        typed = word.group(0) if word else ""
        prefix = line[: len(line) - len(typed)]
        if "/" not in typed and _COMMAND_START.search(prefix):
            self.names.refresh()
            seen: set[str] = set()
            groups: tuple[tuple[Iterable[str], str], ...] = (
                (self.names.functions, "function"), (self.names.aliases, "alias"), (KEYWORDS, "keyword"),
                (BUILTINS, "builtin"), (self.commands, "command"),
            )
            for names, meta in groups:
                for n in names:
                    if n.startswith(typed) and n not in seen:
                        seen.add(n)
                        yield Completion(n, start_position=-len(typed), display_meta=meta)
            if typed:
                return
        yield from self._paths(document)

    def _paths(self, document: Document) -> Iterable[Completion]:
        line = document.current_line_before_cursor
        word = re.search(r"[^\s|;&()<>`'\"=]*$", line)
        typed = word.group(0) if word else ""
        sub = Document(typed, len(typed))
        for c in self.paths.get_completions(sub, CompleteEvent(completion_requested=True)):
            yield Completion(c.text, start_position=c.start_position, display=c.display, display_meta="path")


# ── flag hints ────────────────────────────────────────────────────────────


@dataclass
class Flag:
    names: tuple[str, ...]  # "-f", "--file"
    arg: str
    text: str

    @property
    def short(self) -> str:
        """A few words: 'extract to disk' for 'Extract to disk from the archive.'"""
        text = re.sub(r"^\([^)]*\)\s*", "", self.text)  # "(c mode only) …"
        text = re.split(r"(?<=[.;:,])\s", text, maxsplit=1)[0].rstrip(".;:,")
        words = text.split()[:3]
        short = " ".join(words)
        if words and not (len(words[0]) > 1 and words[0][1:2].isupper()):
            short = short[:1].lower() + short[1:]
        return short if len(short) <= 24 else short[:23] + "…"


# Lines documenting options in man pages and --help output:
#   -x      Extract to disk…         -f file, --file file       -C, --context=NUM   print NUM lines…
_OPT_LINE = re.compile(r"^\s{1,12}(?P<spec>-{1,2}[A-Za-z0-9?#@][^\s,]*(?:[ =][^\s,-][^\s,]*)?(?:,\s*-{1,2}[A-Za-z0-9][^\s,]*(?:[ =][^\s,-][^\s,]*)?)*)(?:\s{2,}|\t\s*|\s*$)(?P<text>.*)$")
SYSTEM_BIN_DIRS = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/opt/homebrew/bin", "/opt/homebrew/sbin",
                   "/home/linuxbrew/.linuxbrew/bin", "/nix/var/nix/profiles/default/bin", "/run/current-system/sw/bin")


def parse_options(text: str) -> dict[str, Flag]:
    flags: dict[str, Flag] = {}
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _OPT_LINE.match(line)
        if not m:
            continue
        names, arg = [], ""
        for part in re.split(r",\s*", m["spec"]):
            pieces = re.split(r"[ =]", part, maxsplit=1)
            name = pieces[0]
            if "[" in name:  # -C[WHEN], --color[=WHEN]
                name, _, optional = name.partition("[")
                pieces = [name, "[" + optional]
            if len(pieces) > 1 and not arg:
                arg = pieces[1]
            names.append(name.rstrip("[").split("[")[0])
        desc = m["text"].strip()
        if not desc:
            # description on the following, more indented line(s)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt.strip() and not _OPT_LINE.match(nxt):
                desc = nxt.strip()
        desc = re.sub(r"\s+", " ", desc)
        flag = Flag(tuple(names), arg, desc)
        for n in names:
            flags.setdefault(n, flag)
    return flags


def load_flags(command: str) -> dict[str, Flag]:
    """Flags for a command from its man page, else from `--help` — but only for system binaries,
    so typing a flag never runs a script from the current directory."""
    env = {**os.environ, "LC_ALL": "C", "MANPAGER": "cat", "PAGER": "cat", "MANWIDTH": "120", "COLUMNS": "120"}
    text = ""
    if shutil.which("man"):
        try:
            proc = subprocess.run(["man", command], capture_output=True, text=True, timeout=3, env=env, stdin=subprocess.DEVNULL)
            text = re.sub(r".\x08", "", proc.stdout)  # strip overstrike bold/underline
        except (OSError, subprocess.TimeoutExpired):
            text = ""
    flags = parse_options(text) if text else {}
    if len(flags) < 2:
        path = shutil.which(command)
        if path and (os.path.dirname(path) in SYSTEM_BIN_DIRS or os.path.dirname(os.path.realpath(path)).startswith(SYSTEM_BIN_DIRS)):
            try:
                proc = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=2, env=env, stdin=subprocess.DEVNULL)
                helped = parse_options((proc.stdout or proc.stderr)[:200_000])
                if len(helped) > len(flags):
                    flags = helped
            except (OSError, subprocess.TimeoutExpired):
                pass
    return flags


_WORD = re.compile(r"(?:[^\s\\'\"]|\\.|'[^']*'|\"(?:[^\"\\]|\\.)*\")+")


def command_words(document: Document) -> tuple[str, list[str], str] | None:
    """(command, flag words before the cursor, the word at the cursor) for the simple command being typed."""
    before = document.text_before_cursor
    segment = re.split(r"\|\||&&|[|;&\n(]|\$\(|`", before)[-1]
    words = _WORD.findall(segment)
    at_cursor = "" if segment.endswith((" ", "\t")) or not words else words[-1]
    done = words[:-1] if at_cursor else words
    while done and (re.match(r"^[A-Za-z_]\w*=", done[0]) or done[0] in ("sudo", "env", "exec", "time", "command", "nohup", "xargs")):
        done = done[1:]
    if not done:
        return None
    command = done[0]
    if "/" in command or command.startswith("%") or command in KEYWORDS:
        return None
    return command, [w for w in done[1:] if w.startswith("-") and w != "--"], at_cursor


class FlagHinter:
    """Parses a command's flags in the background and renders the ones being typed."""

    def __init__(self, on_ready: Callable[[], None]):
        self.on_ready = on_ready
        self.cache: dict[str, dict[str, Flag]] = {}
        self._loading: set[str] = set()
        self._lock = threading.Lock()

    def request(self, document: Document) -> None:
        info = command_words(document)
        if info is None:
            return
        command, flags, current = info
        if not flags and not current.startswith("-"):
            return
        with self._lock:
            if command in self.cache or command in self._loading or not shutil.which(command):
                return
            self._loading.add(command)
        threading.Thread(target=self._load, args=(command,), name="shtick-flags", daemon=True).start()

    def _load(self, command: str) -> None:
        flags = load_flags(command)
        with self._lock:
            self.cache[command] = flags
            self._loading.discard(command)
        if flags:
            self.on_ready()

    def lookup(self, command: str, word: str) -> list[tuple[str, Flag | None, bool]]:
        """Expand a typed word into (flag, info, is_last): -xzf → -x -z -f."""
        flags = self.cache.get(command, {})
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if name in flags:
                return [(name, flags[name], True)]
            matches = [n for n in flags if n.startswith(name) and n.startswith("--")]
            return [(n, flags[n], len(matches) == 1) for n in matches[:3]]
        if word.startswith("-") and len(word) > 1:
            if word in flags:  # single-dash long options like -name
                return [(word, flags[word], True)]
            out = []
            letters = word[1:]
            for i, ch in enumerate(letters):
                f = flags.get("-" + ch)
                out.append(("-" + ch, f, i == len(letters) - 1))
                if f is not None and f.arg:
                    break  # the rest of the word is this flag's value
            return out
        return []

    def render(self, document: Document, width: int) -> StyleAndTextTuples | None:
        info = command_words(document)
        if info is None:
            return None
        command, done, current = info
        if command not in self.cache or not self.cache[command]:
            return None
        entries: list[tuple[str, Flag | None, bool]] = []
        for word in done:
            entries += [(n, f, False) for n, f, _ in self.lookup(command, word)]
        if current.startswith("-"):
            entries += self.lookup(command, current)
        entries = [e for e in entries if e[1] is not None]
        if not entries:
            return None
        return self._render_entries(command, entries, width)

    def _render_entries(self, command: str, entries: list[tuple[str, Flag | None, bool]], width: int) -> StyleAndTextTuples:
        def build(full: bool) -> StyleAndTextTuples:
            out: StyleAndTextTuples = [("class:flag.text", command), ("", "  ")]
            for i, (name, flag, is_current) in enumerate(entries):
                assert flag is not None
                if i:
                    out.append(("class:keybar.sep", " · "))
                arg = f" {flag.arg}" if flag.arg else ""
                if is_current:
                    out += [("class:flag.current", name), ("class:flag.name", arg), ("", " "), ("class:lint.text", flag.text if full else flag.short)]
                elif arg:
                    out += [("class:flag.name", name + arg)]
                else:
                    out += [("class:flag.name", name), ("", " "), ("class:flag.text", flag.short)]
            return out

        if sum(get_cwidth(t) for _, t in build(full=False)) > width and len(entries) > 1:
            return self._render_entries(command, entries[1:], width)  # the earliest flags make room first
        return _fit(build(full=True), width)


def _fit(fragments: StyleAndTextTuples, width: int) -> StyleAndTextTuples:
    out: StyleAndTextTuples = []
    used = 0
    for style, text, *_ in fragments:
        w = get_cwidth(text)
        if used + w > width:
            out.append((style, text[: max(0, width - used - 1)] + "…"))
            break
        out.append((style, text))
        used += w
    return out
