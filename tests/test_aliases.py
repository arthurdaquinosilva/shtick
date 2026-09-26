import shutil

import pytest

from shtick import aliases
from shtick.aliases import Alias
from shtick.config import Settings
from shtick.engine import Session

BASHRC = """\
echo "startup files may print things"
alias ll='ls -l'
alias greet='echo "it'\\''s $USER"'
alias two='echo one
echo two'
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A HOME whose interactive bash and zsh define a few aliases."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USER", "ada")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setattr(aliases, "_cache", {})
    (tmp_path / ".bashrc").write_text(BASHRC)
    (tmp_path / ".zshrc").write_text(BASHRC + "alias -g G='| grep'\nalias -s txt=cat\n")
    return tmp_path


def shell_with(home, **settings):
    from shtick.shell import Shell

    return Shell(Settings(**settings), cwd=str(home))


def test_parse_bash_listing():
    listing = "alias ll='ls -l'\nalias q='echo it'\\''s'\nalias two='echo one\necho two'\n"
    assert aliases.parse(listing) == [Alias("ll", "ls -l"), Alias("q", "echo it's"), Alias("two", "echo one\necho two")]


def test_parse_zsh_listing():
    listing = "\0ll\0ls -l\0\0x\0a\nb\0global\0G\0| grep\0suffix\0txt\0cat\0"
    assert aliases.parse(listing) == [Alias("ll", "ls -l"), Alias("x", "a\nb"), Alias("G", "| grep", "global"), Alias("txt", "cat", "suffix")]


def test_only_what_the_session_shell_can_define():
    found = [Alias("ll", "ls -l"), Alias("-", "cd -"), Alias("G", "| grep", "global")]
    assert aliases.definitions(aliases.usable(found, "bash"), "bash") == "alias -- 'll=ls -l'\nalias -- '-=cd -'"
    assert aliases.definitions(aliases.usable(found, "zsh"), "zsh") == "alias -- 'll=ls -l'\nalias -- '-=cd -'\nalias -g -- 'G=| grep'"
    assert aliases.definitions(aliases.usable(found, "dash"), "dash") == "alias 'll=ls -l'"


def test_command_word():
    assert aliases.command_word("git status -sb") == "git"
    assert aliases.command_word("LANG=C FOO='a b' sort -u") == "sort"
    assert aliases.command_word("\\ls -G") == "ls"
    assert aliases.command_word("$(which vim)") == aliases.command_word("{ echo; }") == ""


@pytest.mark.parametrize("target", [s for s in ("bash", "zsh", "dash") if shutil.which(s)])
def test_aliases_whose_command_is_missing_are_left_out(tmp_path, target):
    found = [
        Alias("history", "omz_history"),  # oh-my-zsh: a function from the startup files
        Alias("g", "git"),
        Alias("gst", "g status"),  # runs another alias
        Alias("lg", "call_lazygit --all"),
        Alias("glg", "lg --graph"),  # runs an alias that is left out
        Alias("sorted", "LC_ALL=C sort"),
    ]
    with Session(target, cwd=str(tmp_path)) as session:
        kept = aliases.define(session, found)
        assert [a.name for a in kept] == ["g", "gst", "sorted"]
        for name in ("history", "lg", "glg"):
            assert session.run(f"alias {name} >/dev/null 2>&1").exit != 0
        assert session.run("printf 'b\\na\\n' | sorted").stdout == "a\nb\n"


@pytest.mark.parametrize("target", [s for s in ("bash", "zsh", "dash") if shutil.which(s)])
def test_imported_bash_aliases_run_in_each_shell(home, target):
    with Session(target, cwd=str(home)) as session:
        aliases.define(session, aliases.load("bash"))
        result = session.run("greet; two")
    assert result.stdout == "it's ada\none\ntwo\n"


@pytest.mark.skipif(not shutil.which("zsh"), reason="needs zsh")
def test_zsh_global_aliases_only_reach_zsh_sessions(home):
    found = aliases.load("zsh")
    assert Alias("G", "| grep", "global") in found and Alias("ll", "ls -l") in found
    with Session("zsh", cwd=str(home)) as session:
        aliases.define(session, found)
        assert session.run("printf 'a\\nb\\n' G b").stdout == "b\n"


def test_setting_defines_aliases_in_every_session(home, capfd):
    sh = shell_with(home, aliases="bash", startup=["alias ll='echo overridden'"])
    try:
        sh.run_cell("greet")
        assert sh.cells[-1].result.stdout == "it's ada\n"
        sh.run_cell("%restart")
        sh.run_cell("two")
        assert sh.cells[-1].result.stdout == "one\ntwo\n"
        sh.run_cell("ll")  # startup lines run after the import, so they can change an alias
        assert sh.cells[-1].result.stdout == "overridden\n"
    finally:
        sh.close()


def test_config_imports_again(home):
    sh = shell_with(home)
    try:
        sh.run_cell("greet")
        assert sh.cells[-1].result.exit != 0
        (home / ".bashrc").write_text("alias hi='echo new'\n")
        sh.run_cell("%config aliases=bash")
        sh.run_cell("hi")
        assert sh.cells[-1].result.stdout == "new\n"
    finally:
        sh.close()


def test_unknown_shell_warns(home, capfd):
    sh = shell_with(home, aliases="no-such-shell")
    try:
        assert "can't import aliases: shell 'no-such-shell' not found" in capfd.readouterr().out
    finally:
        sh.close()


def test_off_values():
    assert Settings().validate("aliases", "off") == "" and Settings().validate("aliases", "zsh") == "zsh"
