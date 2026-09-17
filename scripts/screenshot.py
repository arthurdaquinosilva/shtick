"""Record a real shtick session in a pseudo-terminal and save it as SVG screenshots.

    python scripts/screenshot.py   # writes docs/assets/cover.svg and docs/assets/demo.svg

Needs the dev extras (pexpect, pyte) and shellcheck for the lint hint. The images are the actual
rendered terminal, not mock-ups.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pexpect
import pyte
from rich.console import Console
from rich.style import Style
from rich.terminal_theme import TerminalTheme
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
COLS, ROWS = 100, 54

# (keys to send, seconds to wait afterwards)
SCRIPT = [
    ("greet() { printf 'hello, %s\\n' \"$1\"; }\r", 1.0),
    ('for f in app.log db.log cache.log; do\r', 0.6),
    ('echo "$f: $(grep -c ERROR "$f")"\r', 0.6),
    ("done\r", 1.5),
    ('%trace greet "$USER"\r', 2.0),
    ("rm -rf $build_dir/", 2.5),
]
LOGS = {"app.log": "ok\nERROR disk full\nok\nERROR timeout\n", "db.log": "ok\nok\n"}

THEME_NAME = os.environ.get("SHTICK_THEME", "tide")
BACKGROUND = {"tide": (13, 17, 23), "phosphor": (18, 12, 5), "chalk": (255, 255, 255)}
THEME = TerminalTheme(
    BACKGROUND.get(THEME_NAME, (13, 17, 23)),
    {"chalk": (31, 35, 40)}.get(THEME_NAME, (216, 226, 236)),  # the terminal's default text color
    [(0, 0, 0), (255, 107, 107), (126, 226, 168), (242, 193, 78), (124, 196, 255), (201, 160, 255), (134, 199, 192), (231, 231, 231)],
    [(85, 85, 85), (255, 107, 107), (126, 226, 168), (242, 193, 78), (124, 196, 255), (201, 160, 255), (134, 199, 192), (255, 255, 255)],
)
_NAMED = {"black", "red", "green", "brown", "blue", "magenta", "cyan", "white"}


def _color(value: str) -> str | None:
    if value == "default":
        return None
    if value in _NAMED:
        return {"brown": "yellow"}.get(value, value)
    return f"#{value}" if len(value) == 6 else None


def capture(script: list[tuple[str, float]]) -> pyte.Screen:
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    with tempfile.TemporaryDirectory() as home:
        home = os.path.realpath(home)
        project = Path(home, "project")
        project.mkdir()
        for name, text in LOGS.items():
            (project / name).write_text(text)
        env = dict(
            os.environ, TERM="xterm-256color", COLORTERM="truecolor", PROMPT_TOOLKIT_NO_CPR="1", HOME=home, USER="arthur",
            SHTICK_THEME=THEME_NAME, XDG_CONFIG_HOME=f"{home}/config", XDG_DATA_HOME=f"{home}/data", LC_ALL="C", LANG="C.UTF-8",
        )
        child = pexpect.spawn(sys.executable, ["-m", "shtick"], env=env, dimensions=(ROWS, COLS), cwd=str(project))

        def pump(seconds: float) -> None:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    stream.feed(child.read_nonblocking(65536, timeout=0.05))
                except (pexpect.TIMEOUT, pexpect.EOF):
                    pass

        pump(3.0)
        for keys, wait in script:
            child.send(keys)
            pump(wait)
        child.terminate(force=True)
    return screen


def to_text(screen: pyte.Screen) -> Text:
    lines = [screen.buffer[y] for y in range(screen.lines)]
    last = max((y for y, row in enumerate(lines) if any(row[x].data.strip() or row[x].bg != "default" for x in range(screen.columns))), default=0)
    text = Text()
    for y in range(last + 1):
        row = lines[y]
        for x in range(screen.columns):
            ch = row[x]
            style = Style(color=_color(ch.fg), bgcolor=_color(ch.bg), bold=ch.bold, italic=ch.italics, underline=ch.underscore)
            text.append(ch.data or " ", style=style)
        text.append("\n")
    return text


def save(screen: pyte.Screen, name: str) -> None:
    if THEME_NAME != "tide":
        name = name.replace(".svg", f"-{THEME_NAME}.svg")
    console = Console(record=True, width=COLS, force_terminal=True, color_system="truecolor", file=open(os.devnull, "w"))
    console.print(to_text(screen), end="", overflow="crop", no_wrap=True)
    target = ROOT / "docs" / "assets" / name
    console.save_svg(str(target), title="shtick", theme=THEME)
    print(f"wrote {target}")


def main() -> None:
    save(capture([]), "cover.svg")
    save(capture(SCRIPT), "demo.svg")


if __name__ == "__main__":
    main()
