"""Reading shell code: is it complete, where do top-level commands start, pasted prompts, magics.

Completeness is decided by a real shell's parser (`bash -n`) instead of guessing with regexes;
only its messages about hitting end-of-input count as "unfinished". Any other syntax error is
"complete", so running the cell shows the error.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache

MAGIC_RE = re.compile(r"^%(?P<name>[A-Za-z][\w-]*)(?:\s+(?P<args>.*))?$", re.S)

_UNFINISHED = (
    "unexpected end of file",  # bash: if/for/while/case/{ … without their end, trailing | && ||
    "unexpected EOF while looking for",  # bash: open quotes, $( ${ ` [[
    "delimited by end-of-file",  # bash: here-document without its delimiter (only a warning)
    "end of file unexpected",  # dash
)


def is_magic(text: str) -> bool:
    return bool(MAGIC_RE.match(text.lstrip()))


def _trailing_backslash(text: str) -> bool:
    line = text.rstrip("\n").split("\n")[-1]
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


@lru_cache(maxsize=4)
def _checker(kind: str = "bash") -> str | None:
    if kind == "zsh" and shutil.which("zsh"):
        return shutil.which("zsh")
    return shutil.which("bash") or shutil.which("sh")


@lru_cache(maxsize=256)
def parse_check(code: str, kind: str = "bash") -> tuple[bool, str]:
    """(parses_ok, message) from `<shell> -n` (zsh for zsh sessions, bash otherwise).
    bash's warning for an unterminated heredoc counts as not ok."""
    shell = _checker(kind)
    if shell is None:
        return True, ""
    fd, path = tempfile.mkstemp(prefix="shtick-check-", suffix=".sh")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code if code.endswith("\n") else code + "\n")
        proc = subprocess.run([shell, "-n", path], capture_output=True, text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return True, ""
    finally:
        os.unlink(path)
    message = proc.stderr.replace(path, "input").strip()
    return proc.returncode == 0 and "delimited by end-of-file" not in message, message


_ZSH_LINE = re.compile(r":(\d+): ")
_TRAILING_OPERATOR = re.compile(r"(\|\||&&|\|)\s*$")


def is_unfinished(code: str, kind: str = "bash") -> bool:
    """True when more lines are needed: open blocks, quotes, heredocs, trailing \\ | && ||."""
    if not code.strip():
        return False
    if _trailing_backslash(code):
        return True
    if kind == "zsh" and _checker("zsh") != _checker("bash"):
        if _TRAILING_OPERATOR.search(code.split("\n")[-1].split(" #")[0]) or is_heredoc_open(code):
            return True  # zsh -n accepts both
        ok, message = parse_check(code, "zsh")
        if ok:
            return False
        m = _ZSH_LINE.search(message)
        # zsh reports running out of input as an error on the line after the last one
        return "unmatched" in message or bool(m and int(m[1]) > code.rstrip("\n").count("\n") + 1)
    ok, message = parse_check(code)
    return not ok and any(marker in message for marker in _UNFINISHED)


def is_complete(text: str, kind: str = "bash") -> bool:
    """Should pressing Enter run this input (True) or insert a newline (False)?"""
    if not text.strip():
        return True
    if is_magic(text):
        first, _, rest = text.lstrip().partition("\n")
        if not rest:
            return not _trailing_backslash(first)
        # `%trace` and friends take a body on the following lines
        return not is_unfinished(rest, kind)
    lines = text.split("\n")
    if len(lines) > 2 and not lines[-1].strip() and not lines[-2].strip() and not is_heredoc_open(text):
        return True  # two blank lines: run it anyway (and see the syntax error)
    return not is_unfinished(text, kind)


def is_heredoc_open(text: str) -> bool:
    ok, message = parse_check(text)
    return not ok and "delimited by end-of-file" in message


# ── scripts → top-level commands ─────────────────────────────────────────


@dataclass
class Chunk:
    start: int  # 1-based first line (including leading comments)
    end: int  # 1-based last line
    code: str

    @property
    def lines(self) -> str:
        return f"{self.start}" if self.start == self.end else f"{self.start}-{self.end}"

    @property
    def summary(self) -> str:
        body = [ln for ln in self.code.split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
        first = body[0].strip() if body else self.code.strip().split("\n")[0]
        return first + (" …" if len(body) > 1 else "")


def split_script(source: str) -> list[Chunk]:
    """Split a script into top-level commands. Functions, if/for/case blocks, heredocs and
    continued lines stay whole; comment lines stick to the command below them; a shebang and
    blank lines are dropped."""
    lines = source.replace("\r\n", "\n").split("\n")
    chunks: list[Chunk] = []
    pending: list[str] = []
    pending_start = 0
    comments: list[str] = []
    comments_start = 0
    for i, line in enumerate(lines, 1):
        if not pending:
            stripped = line.strip()
            if i == 1 and line.startswith("#!"):
                continue
            if not stripped:
                comments, comments_start = [], 0
                continue
            if stripped.startswith("#"):
                if not comments:
                    comments_start = i
                comments.append(line)
                continue
            pending_start = comments_start or i
            pending = comments + [line]
            comments, comments_start = [], 0
        else:
            pending.append(line)
        code = "\n".join(pending)
        if _could_end(line) and not is_unfinished(code):
            chunks.append(Chunk(pending_start, i, code))
            pending = []
    if pending:
        code = "\n".join(pending).rstrip()
        chunks.append(Chunk(pending_start, pending_start + code.count("\n"), code))
    return chunks


_CONTINUES = re.compile(r"(\||&&|\\)\s*$")


def _could_end(line: str) -> bool:
    """Cheap pre-filter so the parser only runs where a command could plausibly end."""
    stripped = line.rstrip()
    return bool(stripped) and not _CONTINUES.search(stripped)


# ── pasted prompts ───────────────────────────────────────────────────────

_PROMPT = re.compile(r"^(?P<indent>\s*)(?:[\w.@:~/-]*\s?)?(?P<mark>[$#%]) (?P<cmd>.*)$")
_CONT = re.compile(r"^\s*> (?P<cmd>.*)$")


_ROOT_TOOLS = {"apt", "apt-get", "dnf", "yum", "apk", "pacman", "zypper", "systemctl", "service", "mount", "useradd", "chown", "chmod"}


def _prompt_mark(line: str) -> str | None:
    """`$`, `%` or `#` when the line starts with a shell prompt, else None."""
    m = _PROMPT.match(line)
    if not m or not (line.lstrip().startswith(("$ ", "% ", "# ")) or re.match(r"^\s*[\w.@:~/-]+[$#%] ", line)):
        return None
    if m["mark"] == "#":
        # `# ` is also a comment: only a prompt when a command follows
        word = m["cmd"].split(" ", 1)[0]
        if not word or not (word in _ROOT_TOOLS or shutil.which(word)):
            return None
    return m["mark"]


def has_prompts(text: str) -> bool:
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    return _prompt_mark(first) is not None


def strip_prompts(text: str) -> str:
    """Remove `$ ` / `# ` / `% ` prompts from commands copied from docs, dropping their output lines.

    `> ` continuation lines of a prompted command are kept (without the `> `)."""
    lines = text.replace("\r\n", "\n").split("\n")
    first = next((ln for ln in lines if ln.strip()), "")
    mark = _prompt_mark(first)
    if mark is None:
        return text
    kept: list[str] = []
    in_command = False
    for ln in lines:
        pm = _PROMPT.match(ln)
        if pm and pm["mark"] == mark:
            kept.append(pm["cmd"])
            in_command = True
            continue
        if in_command and (cm := _CONT.match(ln)):
            kept.append(cm["cmd"])
            continue
        if in_command and kept and _trailing_backslash(kept[-1]):
            kept.append(ln)
            continue
        in_command = False  # an output line
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)
