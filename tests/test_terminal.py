"""The real program in a pseudo-terminal: keys go in, the screen is rendered with pyte and checked."""

import os
import sys
import time

import pytest

pexpect = pytest.importorskip("pexpect")
pyte = pytest.importorskip("pyte")

COLS, ROWS = 100, 40


class Terminal:
    def __init__(self, tmp_path, *args):
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        env = dict(
            os.environ, TERM="xterm-256color", PROMPT_TOOLKIT_NO_CPR="1", VISUAL="", EDITOR="true",
            XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"),
        )
        self.child = pexpect.spawn(sys.executable, ["-m", "shtick", *args], env=env, dimensions=(ROWS, COLS), cwd=str(tmp_path))
        self.wait_for("Type shell code")

    def pump(self, seconds=0.2):
        end = time.time() + seconds
        while time.time() < end:
            try:
                self.stream.feed(self.child.read_nonblocking(65536, timeout=0.05))
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def wait_for(self, needle, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.1)
            if needle in self.text():
                return self.text()
        raise AssertionError(f"{needle!r} never appeared on screen:\n{self.text()}")

    def send(self, keys, wait=0.3):
        self.child.send(keys)
        self.pump(wait)

    def close(self):
        self.child.terminate(force=True)


@pytest.fixture
def term(tmp_path):
    t = Terminal(tmp_path)
    yield t
    t.close()


def test_layout_and_a_block(term):
    screen = term.text()
    assert "RUN: Enter" in screen and "HELP: %help" in screen
    assert "bash" in screen  # mode line
    term.send("echo hello; echo oops >&2; false\r")
    screen = term.wait_for("exit 1")
    assert "> echo hello; echo oops >&2; false" in screen
    assert "│ hello" in screen and "│ oops" in screen
    assert "[2]" in screen  # the input bar counts cells


def test_smart_enter_and_shift_enter(term):
    term.send("for i in 1 2; do\r")
    term.send("echo $i\r")
    term.wait_for("NEWLINE: Enter")  # unfinished: Enter adds a line
    term.send("done\r")
    screen = term.wait_for("exit 0")
    assert "│ 1" in screen and "│ 2" in screen
    term.send("echo a\x1b[13;2u")  # Shift+Enter in CSI-u form
    term.send("echo b\r")
    screen = term.wait_for("│ b")
    assert "NEWLINE: Shift+Enter" in screen


def test_ctrl_c_interrupts_the_cell_not_shtick(term):
    term.send("keep=yes; sleep 30\r", wait=1.0)
    term.wait_for("running")
    term.send("\x03")
    term.wait_for("interrupted")
    term.send('echo "keep=$keep"\r')
    term.wait_for("│ keep=yes")


def test_typing_input_for_read(term):
    term.send('read -r name; echo "hi $name"\r', wait=0.8)
    term.send("world\r")
    screen = term.wait_for("│ hi world")
    assert "│ world" in screen


def test_ctrl_d_ends_stdin_then_exits(term):
    term.send("cat; echo after\r", wait=0.8)
    term.send("line\r")
    term.send("\x04")
    term.wait_for("│ after")
    term.send("\x04")  # empty prompt: exit
    term.wait_for("goodbye")


@pytest.mark.skipif(not __import__("shutil").which("shellcheck"), reason="shellcheck not installed")
def test_live_lint_in_key_bar(term):
    term.send("rm $file")
    term.wait_for("SC2086")


def test_flag_hints(term):
    term.send("ls -l")
    screen = term.wait_for("ls  -l", timeout=15)
    assert "RUN: Enter" not in screen.split("ls  -l")[1]


def test_vi_mode_escape(tmp_path):
    t = Terminal(tmp_path, "--vi")
    try:
        t.wait_for("[INSERT]")
        t.send("echo vi", wait=0.1)
        t.send("\x1b", wait=0.5)
        t.wait_for("[NORMAL]")
        t.send("\r")
        t.wait_for("│ vi")
    finally:
        t.close()


def test_open_script_from_the_command_line(tmp_path):
    (tmp_path / "s.sh").write_text("echo one\necho two\n")
    t = Terminal(tmp_path, "s.sh")
    try:
        t.wait_for("s.sh  2 commands")
        t.wait_for("NEXT: %next")
        t.send("%next\r")
        t.wait_for("│ one")
        t.wait_for("s.sh 2/2")
    finally:
        t.close()


def test_typed_input_is_saved_in_tests(term, tmp_path):
    term.send('read -r name; echo "hi $name"\r', wait=0.8)
    term.send("world\r")
    term.wait_for("│ hi world")
    term.send("%expect stdout equals 'hi world'\r")
    term.wait_for("✓ [1]")
    term.send("%save-test t.shtick\r")
    term.wait_for("wrote t.shtick")
    text = (tmp_path / "t.shtick").read_text()
    assert '#% stdin "world\\n"' in text


def test_vi_v_opens_editor_and_visual_mode(tmp_path):
    t = Terminal(tmp_path, "--vi")
    try:
        t.wait_for("[INSERT]")
        t.send("echo abc", wait=0.1)
        t.send("\x1b", wait=0.5)
        t.wait_for("[NORMAL]")
        t.send("V")
        t.wait_for("[VISUAL]")
        t.send("\x1b", wait=0.5)
        t.wait_for("[NORMAL]")
        t.send("v", wait=1.0)
        t.send("\r")
        t.wait_for("│ abc")
    finally:
        t.close()
