"""Editing helpers and key encodings of the prompt."""

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys

from shtick.ui import ENABLE_MODIFIED_KEYS, dedent_closer, next_indent  # importing shtick.ui registers the key sequences


def parse(data: str):
    keys = []
    parser = Vt100Parser(keys.append)
    parser.feed(data)
    parser.flush()
    return [k.key for k in keys]


@pytest.mark.parametrize("seq", ["\x1b[13;2u", "\x1b[27;2;13~", "\x1b[13;6u", "\x1b[13;5u", "\x1b[27;5;13~"])
def test_modified_enter_is_newline(seq):
    assert parse(seq) == [Keys.ControlJ]


def test_requests_modify_other_keys():
    assert ENABLE_MODIFIED_KEYS == "\x1b[>4;1m"


def test_plain_enter_still_submits():
    assert parse("\r") == [Keys.ControlM]


def test_other_modified_keys_are_ignored_not_inserted():
    assert parse("\x1b[53;6u\x1b[13;2u") == [Keys.Ignore, Keys.ControlJ]


@pytest.mark.parametrize("line, indent", [
    ("for f in *; do", "  "), ("if true; then", "  "), ("  else", "    "), ("f() {", "  "), ("case $x in", "  "),
    ("    a) echo;;", "  "), ("  echo hi", "  "), ("echo login", ""),
])
def test_next_indent(line, indent):
    assert next_indent(line) == indent


def _doc(text):
    return Document(text, len(text))


def test_closer_dedents_at_body_level():
    assert dedent_closer(_doc("for f in *; do\n  echo\n  done")).text == "for f in *; do\n  echo\ndone"
    assert dedent_closer(_doc("if true; then\n  fi")).text == "if true; then\nfi"
    assert dedent_closer(_doc("f() {\n  echo\n  }")).text == "f() {\n  echo\n}"


def test_closer_already_aligned_is_left_alone():
    assert dedent_closer(_doc("  for f in *; do\n    echo\n  done")) is None
    assert dedent_closer(_doc("echo\ndone")) is None
    assert dedent_closer(_doc("for f in *; do\n  echo done")) is None


def test_wordmark_is_four_lines_of_half_blocks():
    from shtick.banner import pixel_rows

    rows = pixel_rows("$HTICK")
    assert len(rows) == 4
    assert set("".join(rows)) <= set("█▀▄ ")
    assert rows[0].startswith(" ▄█▄▄")  # the $ with its stroke above and below


def test_closer_finds_its_opener():
    text = "for a in 1; do\n  for b in 2; do\n    echo\n  done\n    done"
    assert dedent_closer(_doc(text)).text.endswith("\n  done\ndone")
    assert dedent_closer(_doc("if x; then\n  echo\n  else")).text.endswith("\nelse")
