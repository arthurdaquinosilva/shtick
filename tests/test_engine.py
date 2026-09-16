import os
import threading
import time

import pytest

from shtick.engine import Session, ShellNotFound, shell_info


def interrupt_later(session, delay):
    t = threading.Timer(delay, session.interrupt)
    t.start()
    return t


def test_state_persists(session):
    assert session.run("x=41; f() { echo $((x + 1)); }").exit == 0
    assert session.run("f").stdout == "42\n"


def test_exit_codes(session):
    assert session.run("true").exit == 0
    assert session.run("sh -c 'exit 3'").exit == 3
    assert session.run("false").exit == 1


def test_status_carries_into_next_cell(session):
    session.run("sh -c 'exit 7'")
    assert session.run('echo "$?"').stdout == "7\n"


def test_cd_persists_and_is_reported(session, tmp_path):
    (tmp_path / "sub").mkdir()
    r = session.run("cd sub")
    assert os.path.realpath(r.cwd) == os.path.realpath(tmp_path / "sub")
    assert os.path.realpath(session.run("pwd").stdout.strip()) == os.path.realpath(tmp_path / "sub")


def test_stderr_is_separate(session):
    r = session.run("echo out; echo err >&2; echo out2")
    assert r.stdout == "out\nout2\n"
    assert r.stderr == "err\n"
    assert {s for s, _ in r.events} == {"out", "err"}


def test_multiline_and_heredoc(session):
    code = "cat <<'EOF'\nline 'one'\n  $HOME\nEOF\nif true; then\n  echo \"yes\"\nfi"
    assert session.run(code).stdout == "line 'one'\n  $HOME\nyes\n"


def test_aliases(session):
    session.run("alias hi='echo hi'")
    assert session.run("hi").stdout == "hi\n"


def test_stdin_data_and_eof(session):
    assert session.run('read a b; echo "$a/$b"', stdin="hello world\n").stdout == "hello/world\n"
    assert session.run("cat; echo done").stdout == "done\n"  # no stdin → immediate EOF
    assert session.run("wc -l", stdin="a\nb\nc\n").stdout.strip() == "3"


def test_input_fd_is_forwarded(session):
    r, w = os.pipe()
    threading.Timer(0.2, lambda: (os.write(w, b"typed\n"), os.close(w))).start()
    out = session.run('read line; echo "got $line"; cat; echo end', input_fd=r)
    os.close(r)
    assert out.stdout == "got typed\nend\n"


def test_exit_restarts_session(session, tmp_path):
    session.run("x=1; cd /")
    r = session.run("echo bye; exit 7")
    assert r.died and r.died_status == 7 and r.stdout == "bye\n"
    assert session.restarts == 1
    after = session.run('echo "x=$x"; pwd')
    assert after.stdout.startswith("x=\n")  # state is gone…
    assert after.stdout.split("\n")[1] == "/"  # …but the directory is kept


def test_set_e_failure_ends_session(session):
    r = session.run("set -e; false; echo unreachable")
    assert r.died and r.stdout == ""
    assert session.run("echo alive").stdout == "alive\n"


def test_exec_replaces_shell(session):
    r = session.run("exec echo replaced")
    assert r.died and r.stdout == "replaced\n"
    assert session.run("echo ok").exit == 0


def test_syntax_error_is_caught_before_running(session):
    r = session.run("if true; then echo x")
    assert r.syntax_error and r.exit is None and not r.died
    assert session.alive and session.restarts == 0


def test_streaming_output(session):
    seen = []
    start = time.perf_counter()
    session.run("echo one; sleep 0.4; echo two", on_output=lambda s, t: seen.append((time.perf_counter() - start, t)))
    assert [t for _, t in seen] == ["one\n", "two\n"]
    assert seen[0][0] < 0.3 and seen[1][0] >= 0.35


def test_interrupt_external_command(session):
    session.run("keep=yes")
    interrupt_later(session, 0.3)
    r = session.run("echo a; sleep 5; echo NOT")
    assert r.exit == 130 and r.stdout == "a\n" and r.duration < 3
    assert session.run('echo "$keep"').stdout == "yes\n"


def test_interrupt_builtin_loop(session):
    interrupt_later(session, 0.3)
    r = session.run("while :; do :; done; echo NOT")
    assert r.exit == 130 and "NOT" not in r.stdout


def test_repeated_interrupts_keep_working(session):
    # bash used to stop running the trap in builtin loops after a few interrupted children.
    session.run("keep=yes")
    for body in ("sleep 5", "sleep 5", "sleep 5", "while :; do :; done", "sleep 5", "while :; do :; done"):
        interrupt_later(session, 0.25)
        r = session.run(body + "; echo NOT")
        assert r.exit == 130 and not r.died, body
    assert session.run('echo "$keep"').stdout == "yes\n"


def test_interrupt_pipeline(session):
    interrupt_later(session, 0.3)
    r = session.run("yes | cat | head -c 99999999999 > /dev/null; echo NOT")
    assert r.exit == 130 and "NOT" not in r.stdout


def test_second_interrupt_kills_session(bash):
    bash.run("trap '' INT")  # the cell ignores Ctrl+C
    t1, t2 = interrupt_later(bash, 0.2), interrupt_later(bash, 0.5)
    r = bash.run("sleep 5")
    t1.join(); t2.join()
    assert r.died and r.interrupted and r.duration < 3
    assert bash.run("echo back").stdout == "back\n"


def test_background_output_arrives_later(session):
    assert session.run("(sleep 0.3; echo late) & echo now").stdout == "now\n"
    time.sleep(0.5)
    assert session.run("echo next").stdout == "late\nnext\n"


def test_return_ends_cell(session):
    r = session.run("echo r; return 4; echo NOT")
    assert r.exit == 4 and r.stdout == "r\n"


def test_internal_query_keeps_status(session):
    session.run("false")
    assert session.query("echo hi").stdout == "hi\n"
    assert session.run('echo "$?"').stdout == "1\n"


def test_temp_paths_are_hidden_in_errors(bash):
    r = bash.run("no_such_command_xyz")
    assert r.exit == 127
    assert str(bash.dir) not in r.stderr and "cell-" in r.stderr


def test_tty_mode(session):
    assert session.run("[ -t 1 ] && echo tty || echo notty").stdout == "notty\n"
    session.set_tty(True)
    r = session.run("[ -t 1 ] && echo tty || echo notty; echo err >&2")
    assert r.stdout == "tty\nerr\n" and r.stderr == ""
    session.set_tty(False)
    assert session.run("[ -t 1 ] && echo tty || echo notty").stdout == "notty\n"


def test_tty_mode_input(session):
    session.set_tty(True)
    assert session.run("read x; echo \"[$x]\"", stdin="abc\n").stdout.endswith("[abc]\n")


def test_unicode_output(session):
    assert session.run("printf 'ação ✓\\n'").stdout == "ação ✓\n"


def test_unknown_shell():
    with pytest.raises(ShellNotFound):
        Session("no-such-shell-xyz")


def test_shell_info():
    kind, version = shell_info(Session("bash").path)
    assert kind == "bash" and version[0].isdigit()


def test_close_cleans_up(tmp_path):
    s = Session("bash", cwd=str(tmp_path))
    d, proc = s.dir, s.proc
    s.close()
    assert not d.exists() and proc.poll() is not None


def test_user_int_trap_is_respected(session):
    session.run("trap 'echo trapped' INT")
    interrupt_later(session, 0.3)
    r = session.run("sleep 1; echo continued")
    assert "trapped" in r.stdout and "continued" in r.stdout
    interrupt_later(session, 0.3)
    assert "trapped" in session.run("sleep 1").stdout  # still the user's trap after an interrupt
