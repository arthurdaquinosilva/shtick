import shutil

import pytest

from shtick.syntax import is_complete, split_script, strip_prompts


@pytest.mark.parametrize("code", [
    "if true; then", 'echo "abc', "echo 'abc", "cat <<EOF\nhi", "ls |", "true &&", "false ||", "echo \\",
    "for i in 1 2; do", "f() {", "echo $(ls", "case x in", "[[ a == b", "x=(1 2", "echo ${x", "echo `ls",
    "while read -r line; do\n  echo \"$line\"",
])
def test_unfinished(code):
    assert not is_complete(code)


@pytest.mark.parametrize("code", [
    "", "echo hi", "fi", "echo )", "if true; then echo; fi", "cat <<EOF\nhi\nEOF", "echo \\\\",
    "for f in *.log; do grep -c ERROR \"$f\"; done", "%help", "%open script.sh", "f() {\n  echo\n}",
])
def test_complete(code):
    assert is_complete(code)


def test_two_blank_lines_submit_but_not_inside_heredoc():
    assert not is_complete("if true; then\n  echo\n")
    assert is_complete("if true; then\n  echo\n\n")
    assert not is_complete("cat <<EOF\nline\n\n")


def test_magic_with_body():
    assert not is_complete("%trace\nif true; then")
    assert is_complete("%trace\nif true; then echo; fi")


SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

# say hello
greet() {
  echo "hi $1"
}

for f in a b; do
  greet "$f"
done
cat <<'EOF'
if this were code; then
EOF
echo one \\
  two
x=1; y=2
ls |
  wc -l
"""


def test_split_script():
    chunks = split_script(SCRIPT)
    assert [c.lines for c in chunks] == ["2", "4-7", "9-11", "12-14", "15-16", "17", "18-19"]
    assert chunks[1].code.startswith("# say hello\ngreet() {")
    assert chunks[1].summary == "greet() { …"
    assert chunks[5].code == "x=1; y=2"


def test_split_script_unterminated_tail():
    chunks = split_script("echo a\nif true; then\n  echo b\n")
    assert [c.lines for c in chunks] == ["1", "2-3"]


def test_strip_prompts():
    pasted = "$ echo hello\nhello\n$ ls -1 \\\n>   /tmp\nfoo\n"
    assert strip_prompts(pasted) == "echo hello\nls -1 \\\n  /tmp"
    assert strip_prompts("# apt install foo\nReading…") == "apt install foo"
    assert strip_prompts("user@host:~$ make\nok") == "make"
    assert strip_prompts("echo $HOME") == "echo $HOME"
    assert strip_prompts("# just a comment\necho hi") == "# just a comment\necho hi"


def test_split_script_word_endings_are_not_continuations():
    assert [c.code for c in split_script("echo logged in\necho I do\nx=(\n  1\n)\n")] == ["echo logged in", "echo I do", "x=(\n  1\n)"]


needs_zsh = pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")


@needs_zsh
@pytest.mark.parametrize("code, complete", [
    ('g() { print "x" }', True), ("if true; then", False), ('echo "abc', False), ("ls |", False), ("true &&", False),
    ("cat <<EOF\nhi", False), ("for i in 1 2; do", False), ("echo $(ls", False), ("fi", True), ("echo hi", True),
])
def test_zsh_completeness(code, complete):
    assert is_complete(code, "zsh") is complete
