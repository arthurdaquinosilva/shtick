"""The controller and its commands, end to end (output is written to the real stdout: use capfd)."""

import os
import shutil
import subprocess
import sys

import pytest

from shtick import lint, testing
from shtick.config import Settings
from shtick.completer import command_words, parse_options
from shtick.sandbox import Sandbox

needs_shellcheck = pytest.mark.skipif(lint.shellcheck_path() is None, reason="shellcheck not installed")

SCRIPT = """#!/usr/bin/env bash
target=${1:-staging}

build() {
  mkdir -p out
  echo "built for $target" > out/app.txt
}

build
echo "done: $(cat out/app.txt)"
"""


def run(shell, *cells):
    for c in cells:
        shell.run_cell(c)
    return shell.cells[-1] if shell.cells else None


# ── blocks ────────────────────────────────────────────────────────────────


def test_block_shows_output_and_footer(shell, capfd):
    cell = run(shell, 'echo out; echo err >&2; sh -c "exit 2"')
    out = capfd.readouterr().out
    assert "> echo out" in out and "│ out" in out and "│ err" in out
    assert "✗ exit 2" in out
    assert cell.result.stdout == "out\n" and cell.result.stderr == "err\n"
    assert shell.last_status == 2


def test_cells_share_state(any_shell, capfd):
    run(any_shell, "x=41", 'echo "$((x + 1))"')
    assert any_shell.cells[-1].result.stdout == "42\n"


def test_shell_exit_is_reported(shell, capfd):
    run(shell, "v=1", "exit 5")
    assert "shell exited" in capfd.readouterr().out
    assert run(shell, 'echo "v=$v"').result.stdout == "v=\n"


def test_syntax_error_is_not_run(shell, capfd):
    run(shell, "fi")
    out = capfd.readouterr().out
    assert "syntax error" in out and "not run" in out


def test_error_messages_name_cells(shell, capfd):
    run(shell, "true", "no_such_cmd_xyz")
    assert "[2]" in shell.cells[-1].result.stderr or "[2]" in capfd.readouterr().out


def test_unknown_command_suggests(shell, capfd):
    shell.run_cell("%hepl")
    assert "did you mean %help" in capfd.readouterr().out


def test_help(shell, capfd):
    shell.run_cell("%help")
    out = capfd.readouterr().out
    for name in ("%open", "%trace", "%sandbox", "%expect", "%save-test", "%save"):
        assert name in out
    shell.run_cell("%help expect")
    assert "stdout|stderr|output" in capfd.readouterr().out


# ── scripts ───────────────────────────────────────────────────────────────


def test_open_next_step_run(shell, tmp_path, capfd):
    (tmp_path / "build.sh").write_text(SCRIPT)
    shell.run_cell("%open build.sh prod")
    assert "3 commands" not in capfd.readouterr().out
    script = shell.script
    assert [c.lines for c in script.chunks] == ["2", "4-7", "9", "10"]
    shell.run_cell("%next 2")
    assert script.pos == 2
    shell.run_cell("%step")
    assert shell.next_input == "build" and script.pos == 2
    shell.run_cell(shell.next_input)
    assert script.pos == 3
    shell.run_cell("%run")
    assert script.done
    assert shell.cells[-1].result.stdout == "done: built for prod\n"
    assert "reached the end of build.sh" in capfd.readouterr().out


def test_run_stops_on_failure(shell, tmp_path, capfd):
    (tmp_path / "f.sh").write_text("echo one\nfalse\necho three\n")
    shell.run_cell("%run f.sh")
    assert shell.script.pos == 2
    assert "stopped" in capfd.readouterr().out
    shell.run_cell("%run -k --all")
    assert shell.script.done


def test_goto_and_edit_reload(shell, tmp_path, capfd, monkeypatch):
    (tmp_path / "g.sh").write_text("echo a\necho b\necho c\n")
    shell.run_cell("%open g.sh")
    shell.run_cell("%goto 3")
    assert shell.script.pos == 2
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f"{sys.executable} -c \"import sys; open(sys.argv[1], 'a').write('echo d\\n')\"")
    shell.run_cell("%edit")
    assert len(shell.script.chunks) == 4 and shell.script.pos == 2


# ── trace ─────────────────────────────────────────────────────────────────


def test_trace_lists_commands_separately(any_shell, capfd):
    cell = run(any_shell, "f() { echo \"in f $1\"; }", "%trace for i in 1 2; do f $i; done")
    out = capfd.readouterr().out
    assert cell.result.stdout == "in f 1\nin f 2\n"
    commands = [t.command for t in cell.trace]
    assert any("in f 1" in c for c in commands) and any("in f 2" in c for c in commands)
    assert "trace ·" in out
    assert "shtick" not in cell.result.stderr  # the markers never leak into the output
    assert run(any_shell, "echo after").result.stderr == ""  # tracing is off again


def test_trace_under_set_u(shell, capfd):
    cell = run(shell, "set -u", "%trace echo hi")
    assert cell.result.stderr == "" and cell.result.stdout == "hi\n"
    assert [t.command for t in cell.trace] == ["echo hi"]


def test_trace_script_file_uses_its_lines(shell, tmp_path, capfd):
    (tmp_path / "build.sh").write_text(SCRIPT)
    cell = run(shell, "%trace build.sh demo")
    locations = [t.location(cell.result.number) for t in cell.trace]
    assert "build.sh:5" in locations and "build.sh:9" in locations


def test_trace_next_maps_functions_to_script_lines(shell, tmp_path, capfd):
    (tmp_path / "build.sh").write_text(SCRIPT)
    run(shell, "%open build.sh", "%next 2", "%trace --next")
    out = capfd.readouterr().out
    assert "build.sh:5" in out and "build.sh:6" in out


# ── sandbox ───────────────────────────────────────────────────────────────


def test_sandbox_reports_changes_and_cleans_up(shell, tmp_path, capfd):
    (tmp_path / "keep.txt").write_text("original")
    shell.run_cell("%sandbox on --copy")
    sandbox = shell.sandbox
    assert sandbox is not None and (sandbox.root / "keep.txt").exists()
    cell = run(shell, "echo new > a.txt; echo changed > keep.txt; mkdir -p d/e; touch d/e/f")
    assert cell.changes.added == ["a.txt", "d/"] and cell.changes.modified == ["keep.txt"]
    cell = run(shell, "rm keep.txt")
    assert cell.changes.deleted == ["keep.txt"]
    out = capfd.readouterr().out
    assert "+ a.txt" in out and "~ keep.txt" in out and "− keep.txt" in out
    shell.run_cell("%sandbox off")
    assert not sandbox.root.exists()
    assert (tmp_path / "keep.txt").read_text() == "original" and not (tmp_path / "a.txt").exists()
    assert os.path.realpath(shell.session.cwd) == os.path.realpath(tmp_path)


def test_sandbox_warns_when_leaving(shell, capfd):
    shell.run_cell("%sandbox on")
    run(shell, "cd /")
    assert "outside the sandbox" in capfd.readouterr().out


def test_sandbox_diff_modes():
    import tempfile

    with tempfile.TemporaryDirectory() as src:
        sb = Sandbox(src)
        before = sb.snapshot()
        (sb.root / "x").write_text("1")
        mid = sb.snapshot()
        assert sb.diff(before, mid).added == ["x"]
        (sb.root / "x").write_text("2")
        assert sb.diff(mid, sb.snapshot()).modified == ["x"]
        sb.close()


# ── expectations & tests ──────────────────────────────────────────────────


@pytest.mark.parametrize("text, passed", [
    ("exit 0", True), ("exit 1", False), ("exit != 1", True), ("ok", True), ("fails", False),
    ('stdout contains "hello"', True), ("stdout equals 'hello world'", True), ("stdout matches '^hel+o'", True),
    ("stdout lines 1", True), ("stderr empty", False), ('stderr contains "warn"', True), ("output not-empty", True),
    ("stdout not-contains bye", True), ("duration < 5s", True), ("duration > 1m", False),
])
def test_expectations(text, passed, bash):
    result = bash.run("echo hello world; echo warn >&2")
    assert testing.parse(text).evaluate(testing.Context(result)).passed is passed


def test_file_expectations(bash, tmp_path):
    result = bash.run("mkdir -p d; echo content > d/f.txt")
    ctx = testing.Context(result)
    assert testing.parse("file exists d/f.txt").evaluate(ctx).passed
    assert testing.parse("dir exists d").evaluate(ctx).passed
    assert testing.parse("file missing nope").evaluate(ctx).passed
    assert testing.parse("file contains d/f.txt content").evaluate(ctx).passed
    assert not testing.parse("file contains d/f.txt other").evaluate(ctx).passed


@pytest.mark.parametrize("text", ["", "exit", "exit x", "stdout", "stdout contains", "file", "wat", "duration 5s", "stdout matches ("])
def test_bad_expectations(text):
    with pytest.raises(testing.ExpectationError):
        testing.parse(text)


def test_expect_test_and_save_test_roundtrip(shell, tmp_path, capfd):
    shell.run_cell("%sandbox on")
    run(shell, "greeting=hi", 'echo "$greeting there" > note.txt; cat note.txt')
    shell.run_cell('%expect stdout equals "hi there"')
    shell.run_cell("%expect file exists note.txt")
    shell.run_cell("%expect exit 3")
    out = capfd.readouterr().out
    assert "✓ [3] stdout equals 'hi there'" in out and "✗ [3] exit 3" in out
    shell.run_cell("%test")
    out = capfd.readouterr().out
    assert "1 failed · 2 passed" in out
    assert not (tmp_path / "note.txt").exists()  # the test ran in its own sandbox
    shell.cells[-1].expectations.pop()  # drop `exit 3`
    shell.run_cell("%save-test t.shtick")
    text = (tmp_path / "t.shtick").read_text()
    assert "sandbox: empty" in text and "#% expect file exists note.txt" in text
    tf = testing.load(text)
    assert [c.code for c in tf.cells] == ["greeting=hi", 'echo "$greeting there" > note.txt; cat note.txt']
    shell.run_cell("%save-test t.shtick")
    assert "exists" in capfd.readouterr().out
    # `shtick test` exits 0 when everything passes, 1 when something fails
    cli = [sys.executable, "-m", "shtick", "test"]
    assert subprocess.run([*cli, str(tmp_path / "t.shtick")], capture_output=True).returncode == 0
    (tmp_path / "bad.shtick").write_text(text.replace("hi there", "bye"))
    proc = subprocess.run([*cli, str(tmp_path / "bad.shtick")], capture_output=True, text=True)
    assert proc.returncode == 1 and "1 failed" in proc.stdout


def test_test_file_runs_with_bash_too(tmp_path):
    tf = testing.TestFile([testing.TestCell("x=1", []), testing.TestCell('echo "x=$x"', ["stdout equals x=1"])])
    path = tmp_path / "t.shtick"
    path.write_text(tf.dump())
    assert subprocess.run(["bash", str(path)], capture_output=True, text=True).stdout == "x=1\n"


# ── history & save ────────────────────────────────────────────────────────


def test_save_writes_successful_cells(shell, tmp_path, capfd):
    run(shell, "a=1", "false", "echo $a", "%help")
    shell.run_cell("%save out.sh")
    text = (tmp_path / "out.sh").read_text()
    assert text == "#!/usr/bin/env bash\n\na=1\n\necho $a\n"
    assert os.access(tmp_path / "out.sh", os.X_OK)
    assert "left out 1 failed" in capfd.readouterr().out
    shell.run_cell("%save out.sh 1-2 -a -f")
    assert (tmp_path / "out.sh").read_text() == "#!/usr/bin/env bash\n\na=1\n\nfalse\n"


def test_history_and_rerun(shell, capfd):
    run(shell, "echo one", "echo two")
    shell.run_cell("%history -n")
    out = capfd.readouterr().out
    assert "1  " in out and "echo two" in out
    shell.run_cell("%rerun 1")
    assert shell.cells[-1].result.stdout == "one\n"
    shell.run_cell("%recall 2")
    assert shell.next_input == "echo two"


# ── settings & shells ─────────────────────────────────────────────────────


def test_switch_shell(shell, capfd):
    if not shutil.which("dash"):
        pytest.skip("dash not installed")
    shell.run_cell("%shell dash")
    assert shell.session.kind in ("dash", "sh")
    assert run(shell, 'echo "${BASH_VERSION:-none}"').result.stdout == "none\n"


def test_tty_mode(shell, capfd):
    shell.run_cell("%tty on")
    assert run(shell, "[ -t 1 ] && echo tty").result.stdout == "tty\n"
    shell.run_cell("%tty off")
    assert run(shell, "[ -t 1 ] || echo pipe").result.stdout == "pipe\n"


def test_config(shell, capfd):
    shell.run_cell("%config theme=nebula")
    assert shell.theme.name == "nebula"
    shell.run_cell("%config nope=1")
    assert "unknown setting" in capfd.readouterr().out


@needs_shellcheck
def test_lint_report(shell, capfd):
    run(shell, "x='a b'; echo $x")
    shell.run_cell("%lint")
    out = capfd.readouterr().out
    assert "SC2086" in out and "^" in out


@needs_shellcheck
def test_lint_findings_are_sorted_and_filtered():
    findings = lint.check("cd $dir\nls $x", "bash", exclude=["SC2154"])
    assert findings and all(f.code != 2154 for f in findings)
    assert lint.check("print hi", "zsh") == []  # shellcheck can't check zsh


# ── completion & flag hints ───────────────────────────────────────────────


def test_completion(shell, tmp_path):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from shtick.completer import ShtickCompleter

    (tmp_path / "notes.txt").write_text("")
    run(shell, "myfunc() { :; }; MYVAR=1")
    c = ShtickCompleter(shell)

    def complete(text):
        return [x.display_text for x in c.get_completions(Document(text, len(text)), CompleteEvent(completion_requested=True))]

    assert "myfunc" in complete("myf")
    assert "MYVAR" in complete("echo $MYV")
    assert "notes.txt" in complete("cat no")
    assert "%sandbox" in complete("%sand")


def test_flag_parsing():
    man = """
     -c      Create a new archive containing the specified items.
     -f file, --file file
             Read the archive from or write the archive to the specified file.
     -C directory, --cd directory, --directory directory
             In c and r mode, this changes the directory.
  -e CODE1,CODE2..    --exclude=CODE1,CODE2..    Exclude types of warnings
"""
    flags = parse_options(man)
    assert flags["-c"].short == "create a new archive"
    assert flags["-f"].arg == "file" and flags["--file"] is flags["-f"]
    assert flags["-C"].arg == "directory"
    assert "-e" in flags


def test_flag_hints_only_while_on_a_flag():
    from prompt_toolkit.document import Document

    from shtick.completer import Flag, FlagHinter

    hinter = FlagHinter(lambda: None)
    hinter.cache["rm"] = {"-r": Flag(("-r",), "", "Recursive."), "-f": Flag(("-f",), "", "Force.")}

    def hint(text):
        rendered = hinter.render(Document(text, len(text)), 100)
        return "".join(t for _, t, *_ in rendered) if rendered else None

    assert hint("rm -rf") == "rm  -r recursive · -f Force."
    assert hint("rm -rf ") is not None
    assert hint("rm -rf $dir/") is None


def test_command_words():
    from prompt_toolkit.document import Document

    def words(text):
        return command_words(Document(text, len(text)))

    assert words("tar -xzf ") == ("tar", ["-xzf"], "")
    assert words("sudo LC_ALL=C grep -r -") == ("grep", ["-r"], "-")
    assert words("ls | wc -l") == ("wc", [], "-l")
    assert words("./run.sh -x") is None  # never look up local scripts


# ── %vars & %compare ──────────────────────────────────────────────────────


def test_vars_shows_changes(any_shell, capfd):
    run(any_shell, 'x=41; multi="a\nb"; f() { echo hi; }; unset SHTICK_TEST_VAR')
    capfd.readouterr()
    any_shell.run_cell("%vars")
    out = capfd.readouterr().out
    assert "+ x=" in out and "+ multi=" in out and "− SHTICK_TEST_VAR" in out
    assert "RANDOM" not in out and "__shtick" not in out
    if any_shell.session.kind in ("bash", "zsh"):
        assert "+ f()" in out
    else:
        assert "can't list functions" in out


def test_vars_redefined_function(tmp_path, monkeypatch, capfd):
    from shtick.shell import Shell

    monkeypatch.chdir(tmp_path)
    sh = Shell(Settings(startup=["greet() { echo hi; }"]), cwd=str(tmp_path))
    try:
        run(sh, "greet() { echo hello; }")
        capfd.readouterr()
        sh.run_cell("%vars")
        assert "~ greet()  redefined" in capfd.readouterr().out
    finally:
        sh.close()


def test_vars_baseline_resets_with_the_session(shell, capfd):
    run(shell, "x=1", "exit 1")
    capfd.readouterr()
    shell.run_cell("%vars")
    assert "nothing defined or changed" in capfd.readouterr().out


def test_compare(shell, capfd):
    if not shutil.which("dash"):
        pytest.skip("dash not installed")
    shell.run_cell('%compare bash dash -- [[ 1 == 1 ]] && echo double')
    out = capfd.readouterr().out
    assert "differs in status, stdout, stderr" in out
    assert "double" in out and "cell-" not in out
    shell.run_cell("%compare bash dash -- echo same")
    assert "same exit status and output" in capfd.readouterr().out


def test_compare_previous_cell_with_body(shell, capfd):
    if not shutil.which("dash"):
        pytest.skip("dash not installed")
    run(shell, "echo from-cell")
    shell.run_cell("%compare bash dash")
    assert "from-cell" in capfd.readouterr().out
    shell.run_cell("%compare bash dash\necho body-line")
    assert "body-line" in capfd.readouterr().out


def test_command_line_followed_by_code(shell, capfd):
    shell.run_cell("%tty on\n[ -t 1 ] && echo tty")
    assert shell.settings.tty and shell.cells[-1].result.stdout == "tty\n"
