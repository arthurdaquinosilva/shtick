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


def test_definitions_skip_what_the_session_shell_cannot_define():
    found = [Alias("ll", "ls -l"), Alias("-", "cd -"), Alias("G", "| grep", "global")]
    code, skipped = aliases.definitions(found, "bash")
    assert skipped == 1 and "alias -- 'll=ls -l'" in code and "'-=cd -'" in code
    assert aliases.definitions(found, "zsh") == ("alias -- 'll=ls -l'\nalias -- '-=cd -'\nalias -g -- 'G=| grep'", 0)
    assert aliases.definitions(found, "dash") == ("alias 'll=ls -l'", 2)


@pytest.mark.parametrize("target", [s for s in ("bash", "zsh", "dash") if shutil.which(s)])
def test_imported_bash_aliases_run_in_each_shell(home, target):
    code, _ = aliases.definitions(aliases.load("bash"), target)
    with Session(target, cwd=str(home)) as session:
        session.query(code)
        result = session.run("greet; two")
    assert result.stdout == "it's ada\none\ntwo\n"


@pytest.mark.skipif(not shutil.which("zsh"), reason="needs zsh")
def test_zsh_global_aliases_only_reach_zsh_sessions(home):
    found = aliases.load("zsh")
    assert Alias("G", "| grep", "global") in found and Alias("ll", "ls -l") in found
    with Session("zsh", cwd=str(home)) as session:
        session.query(aliases.definitions(found, "zsh")[0])
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
