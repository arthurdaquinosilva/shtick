"""Importing the aliases of your interactive shell (the `aliases` setting).

Sessions don't read startup files, so an interactive bash or zsh is asked once for its aliases,
and each new session defines the ones its shell understands.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from shtick.engine import Session, shell_kind

TIMEOUT = 15  # seconds for the interactive shell to load its startup files

# The listing is written to a file, so whatever the startup files print doesn't get mixed in.
# bash's `alias -p` only uses single quotes, which shlex reads; zsh's also uses $'…', so zsh lists
# kind, name and value separated by NULs.
LIST = {
    "bash": 'alias -p >"$SHTICK_ALIAS_OUT"',
    "zsh": (
        '{ for k v in "${(@kv)aliases}"; do printf "\\0%s\\0%s\\0" "$k" "$v"; done; '
        'for k v in "${(@kv)galiases}"; do printf "global\\0%s\\0%s\\0" "$k" "$v"; done; '
        'for k v in "${(@kv)saliases}"; do printf "suffix\\0%s\\0%s\\0" "$k" "$v"; done; } >"$SHTICK_ALIAS_OUT"'
    ),
}

# Names each session shell accepts; anything else is skipped rather than failing in the session.
NAME = {
    "bash": re.compile(r"[^\s/$`=\\'\"|&;()<>]+"),
    "zsh": re.compile(r"[^\s=\\'\"`$|&;()<>]+"),
    "posix": re.compile(r"[A-Za-z0-9_!%,@.:+][A-Za-z0-9_!%,@.:+-]*"),
}


class AliasError(Exception):
    pass


@dataclass(frozen=True)
class Alias:
    name: str
    value: str
    kind: str = ""  # "" · "global" · "suffix" (zsh only)


def source_shell(setting: str) -> str:
    """The shell whose aliases to import: "auto" means $SHELL."""
    name = os.environ.get("SHELL", "") if setting == "auto" else setting
    path = shutil.which(os.path.expanduser(name)) if name else None
    if not path:
        raise AliasError(f"can't import aliases: shell {name or '$SHELL'!r} not found")
    if shell_kind(path) not in LIST:
        raise AliasError(f"can't import aliases from {os.path.basename(path)}: only bash and zsh are supported")
    return path


def parse(listing: str) -> list[Alias]:
    """bash's `alias -p` output, or zsh's NUL-separated kind, name, value triples → aliases."""
    if "\0" in listing:
        fields = listing.split("\0")
        return [Alias(n, v, k) for k, n, v in zip(fields[0::3], fields[1::3], fields[2::3]) if n]
    try:
        words = shlex.split(listing, comments=False, posix=True)
    except ValueError as e:
        raise AliasError(f"can't read the alias listing: {e}") from None
    aliases: list[Alias] = []
    for word in words:
        if word not in ("alias", "--") and "=" in word:
            name, value = word.split("=", 1)
            aliases.append(Alias(name, value))
    return aliases


_cache: dict[str, list[Alias] | AliasError] = {}  # failures too, so a restart doesn't wait for them again


def load(setting: str, refresh: bool = False) -> list[Alias]:
    """Ask the interactive shell for its aliases, once per shtick process (unless `refresh`)."""
    path = source_shell(setting)
    if path not in _cache or refresh:
        try:
            _cache[path] = parse(_list(path))
        except AliasError as e:
            _cache[path] = e
    found = _cache[path]
    if isinstance(found, AliasError):
        raise found
    return found


def _list(path: str) -> str:
    kind = shell_kind(path)
    fd, out = tempfile.mkstemp(prefix="shtick-aliases-")
    os.close(fd)
    try:
        try:
            proc = subprocess.run(
                [path, "-ic", LIST[kind]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env={**os.environ, "SHTICK_ALIAS_OUT": out}, timeout=TIMEOUT,
                start_new_session=True,  # keep an interactive shell's job control off our terminal
            )
        except subprocess.TimeoutExpired:
            raise AliasError(f"can't import aliases: {kind} -i took longer than {TIMEOUT}s to start") from None
        with open(out, encoding="utf-8", errors="replace") as f:
            listing = f.read()
    finally:
        os.unlink(out)
    if proc.returncode != 0 and not listing:
        raise AliasError(f"can't import aliases: {kind} -i exited with status {proc.returncode}")
    return listing


def usable(aliases: list[Alias], target: str) -> list[Alias]:
    """The aliases a `target` session can define."""
    names = NAME.get(target, NAME["posix"])
    return [a for a in aliases if (not a.kind or target == "zsh") and names.fullmatch(a.name)]


def definitions(aliases: list[Alias], target: str) -> str:
    """Code that defines (already `usable`) aliases in a `target` session."""
    dashes = "-- " if target in ("bash", "zsh") else ""
    return "\n".join(
        f"alias {FLAG.get(a.kind, '')}{dashes}{shlex.quote(a.name + '=' + a.value)}" for a in aliases
    )


FLAG = {"global": "-g ", "suffix": "-s "}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.S)
WORD = re.compile(r"[A-Za-z0-9_./+@%:,-]+")


def command_word(value: str) -> str:
    """The command an alias runs (after any VAR=value prefixes), or "" when it isn't a plain word."""
    try:
        words = shlex.split(value, comments=True)
    except ValueError:
        words = value.split()
    for word in words:
        if not ASSIGNMENT.fullmatch(word):
            word = word.lstrip("\\")
            return word if WORD.fullmatch(word) else ""
    return ""


def define(session: Session, aliases: list[Alias]) -> list[Alias]:
    """Define the aliases the session's shell can use and returns them.

    Aliases often call functions from the startup files (oh-my-zsh's `history` runs `omz_history`),
    and functions aren't imported, so an alias whose command doesn't exist in the session is removed
    again — the name then means what it means in a plain shell. Checked after all are defined, so
    an alias may run another alias; repeated because removing one can break those that ran it.
    """
    kept = usable(aliases, session.kind)
    if not kept:
        return kept
    session.query(definitions(kept, session.kind))
    dashes = "-- " if session.kind in ("bash", "zsh") else ""
    for _ in range(4):
        words = {a.name: command_word(a.value) for a in kept if not a.kind}
        check = " ".join(sorted({shlex.quote(w) for w in words.values() if w}))
        if not check:
            break
        listing = session.query(
            f'for __shtick_w in {check}; do command -v -- "$__shtick_w" >/dev/null 2>&1 || printf \'%s\\n\' "$__shtick_w"; done; unset __shtick_w'
        )
        missing = set(listing.stdout.splitlines())
        broken = [a for a in kept if words.get(a.name) in missing]
        if not broken:
            break
        session.query(f"unalias {dashes}" + " ".join(shlex.quote(a.name) for a in broken) + " 2>/dev/null")
        kept = [a for a in kept if a not in broken]
    return kept
