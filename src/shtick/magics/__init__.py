"""Command registry: `%name args` lines. Built-in commands live in the submodules imported at the bottom."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


class MagicError(Exception):
    """An error raised by a command, shown as a one-line message instead of a traceback."""


class Struct(dict):
    """A dict with attribute access, for parsed options."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


@dataclass
class MagicSpec:
    name: str
    fn: Callable[..., Any]
    doc: str
    category: str = "general"
    usage: str = ""
    aliases: tuple[str, ...] = ()


MAGICS: dict[str, MagicSpec] = {}

CATEGORIES = {
    "core": "general",
    "scripts": "scripts & tracing",
    "checks": "linting, sandbox & tests",
    "history": "history & saving",
    "inspect": "variables & portability",
}
ORDER = list(CATEGORIES.values())


def _category(fn: Callable) -> str:
    return CATEGORIES.get(fn.__module__.rsplit(".", 1)[-1], "general")


def magic(*names: str, doc: str, usage: str = ""):
    def deco(fn: Callable) -> Callable:
        spec = MagicSpec(names[0], fn, doc, _category(fn), usage.strip("\n"), names[1:])
        for n in names:
            MAGICS[n] = spec
        return fn

    return deco


def magic_docs() -> dict[str, dict[str, str]]:
    """{category: {"%a, %b": doc}}, grouping aliases."""
    grouped: dict[str, dict[str, str]] = {}
    for name, spec in MAGICS.items():
        if name != spec.name:
            continue
        names = ", ".join("%" + n for n in (spec.name, *spec.aliases))
        grouped.setdefault(spec.category, {})[names] = spec.doc
    return grouped


# ── option parsing for built-in magics ─────────────────────────────────────

_TOKEN = re.compile(r"\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\S+)")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_args(args: str, spec: str = "", *long_opts: str) -> tuple[Struct, str]:
    """getopt-style parsing (`"n:r:qo"`, `"out="`) that returns the *untouched* remainder text.

    Parsing stops at the first token that isn't a known option, so the rest stays intact.
    Repeated options collect into lists."""
    takes = {c: spec[i + 1 : i + 2] == ":" for i, c in enumerate(spec) if c != ":"}
    long_takes = {o.rstrip("="): o.endswith("=") for o in long_opts}
    opts = Struct()

    def add(key: str, value: Any) -> None:
        if key in opts:
            opts[key] = [*opts[key], value] if isinstance(opts[key], list) else [opts[key], value]
        else:
            opts[key] = value

    def token(pos: int) -> tuple[str | None, int]:
        m = _TOKEN.match(args, pos)
        return (m.group(1), m.end()) if m else (None, pos)

    pos = 0
    while True:
        tok, end = token(pos)
        if tok is None or not tok.startswith("-") or tok == "-":
            break
        if tok == "--":
            pos = end
            break
        if tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            if name not in long_takes:
                break
            if long_takes[name]:
                if not eq:
                    value, end = token(end)
                    if value is None:
                        raise MagicError(f"option --{name} needs a value")
                add(name, _unquote(value))
            else:
                add(name, True)
            pos = end
            continue
        letters = tok[1:]
        if letters[0] not in takes:
            break
        for i, c in enumerate(letters):
            if c not in takes:
                raise MagicError(f"unknown option -{c}")
            if takes[c]:
                value = letters[i + 1 :]
                if not value:
                    value, end = token(end)
                    if value is None:
                        raise MagicError(f"option -{c} needs a value")
                add(c, _unquote(value))
                break
            add(c, True)
        pos = end
    return opts, args[pos:].strip()



def split_args(args: str) -> list[str]:
    """Shell-like word splitting for command arguments."""
    import shlex

    try:
        return shlex.split(args)
    except ValueError as e:
        raise MagicError(f"can't parse arguments: {e}") from None


from shtick.magics import checks, core, history, inspect, scripts  # noqa: E402

BUILTIN_MODULES = (core, scripts, checks, history, inspect)  # importing them registers the commands
