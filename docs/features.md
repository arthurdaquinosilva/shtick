# Features

## Cells and the session

Everything you type runs in **one long-lived shell process** (bash by default), so variables, functions, aliases, `cd`, `export` and `set` options carry over from cell to cell — like typing into a shell, but each run is kept as a readable block:

```text
> for f in *.log; do grep -c ERROR "$f"; done
│
│ 3
│ 0
│
╰─ ✓ exit 0 · 12ms · ~/project
```

- Output appears on the rail as it's printed. **stderr** gets a red rail and text, so you can tell the streams apart.
- The footer shows the **exit status** (`✓ exit 0`, `✗ exit 2`, `✗ interrupted · exit 130`), how long the cell took and the **directory** the shell is in afterwards.
- Cells slower than a quarter second show a spinner until they print or ask for input.
- `$?` at the start of a cell is the previous cell's exit status.
- Error messages name cells by number: `[4]: line 2: foo: command not found`.

### When the shell ends

`exit`, `exec cmd`, a failing command under `set -e`, or a crash end the shell process. shtick reports it —

```text
╰─ ✗ shell exited · exit 1 · 3ms · session restarted, state lost
```

— and starts a new session in the same directory. Variables, functions and options from before are gone. `%restart` does the same on purpose.

### Input while a cell runs

A running cell can read from you: type a line and press Enter (`read`, `cat`, a script asking for confirmation). What you type shows on the rail in the accent color; Backspace and Ctrl+U edit the line; **Ctrl+D** ends the cell's input (`cat` finishes). Cells that don't read never see what you type. What you typed is kept with the cell, so `%test` and saved tests give the cell the same input again.

A `%command` on its own line after shell code runs as its own step, so `shtick -c` and pasted snippets can mix both (lines inside a heredoc are left alone).

**Ctrl+C** interrupts the running cell — the rest of the cell is skipped and the session survives. If the cell ignores it (`trap '' INT`), press Ctrl+C again to kill the session.

### Terminal programs: `%tty`

Cells run with stdout and stderr on pipes, like a script in a pipeline. Some programs behave differently when they're not writing to a terminal (no colors, different formatting, `[ -t 1 ]`). `%tty on` runs cells attached to a pseudo-terminal instead; stderr is then merged into stdout, and `read -p` prompts show. Full-screen programs (vim, htop, less) still need a real terminal.

## Typing

- **Enter** runs the cell when it's complete. Whether it's complete is decided by the shell's own parser (`bash -n`, or `zsh -n` in zsh sessions), so an open `if`/`for`/`case`/`{`, an unclosed quote, a heredoc without its end, or a line ending in `\`, `|`, `&&`, `||` get a new line instead. Two blank lines run it anyway (to see the syntax error).
- **Shift+Enter**, Alt+Enter or Ctrl+J always insert a newline.
- **Indentation** follows blocks: one level deeper after `then`, `do`, `else`, `in`, `{`, `(`; `done`, `fi`, `esac`, `}` and `else` line up with their opener when you press Enter.
- **Syntax highlighting** in the input and in each cell's echo.
- **Completion** (Tab): commands on `PATH`, builtins and keywords, the session's functions and aliases, files (relative to the session's directory), `$VARIABLES`, and `%commands`.
- **Grey suggestions** from history; → accepts them. ↑/↓ go through history by what you've typed so far; Ctrl+R searches it.
- **Pasting** commands from docs works: `$ `, `% ` and `# ` prompts (including `user@host:~$ `) are removed and the output lines between commands are dropped. `> ` continuation lines are kept. (`# ` counts as a prompt only when a command follows, so pasted comments survive.)
- **Ctrl+O** opens the cell in `$EDITOR`.

### vi mode

Start with `shtick --vi`, switch with `%vi` (`%emacs` goes back), or keep it with `%vi --save` (writes `editing_mode = "vi"` to `config.toml`).

- The mode line shows `[INSERT]`, `[NORMAL]`, `[VISUAL]` or `[REPLACE]`, and the cursor changes shape where the terminal supports it.
- Esc leaves insert mode (quickly — shtick doesn't wait for more of an escape sequence), also when it closes the completion menu.
- In normal mode the usual motions and operators work (`w b e 0 $ dd cw x p u` …), **Enter runs the cell**, `j`/`k` move through history, `/` and `?` search it, and **`v` opens the cell in `$EDITOR`** like in bash and zsh. With an empty input the key bar reminds you: `HISTORY: k j /  |  EDITOR: v`.
- Each new prompt starts in insert mode.

### The key bar

The line under the input changes with what you're doing:

- the keys that matter now (`RUN: Enter`, or `NEWLINE: Enter` inside an unfinished block);
- with a script open and an empty input: `NEXT: %next | STEP: %step | REST: %run`;
- **flag hints** while you type an option — `tar -xzf` shows `tar  -x extract to disk · -z compress… · -f file`, with the flag at the cursor highlighted and described in full. Flags come from the command's man page, or `--help` for programs in system directories (a script from the current directory is never run to get its help). They're parsed in the background and cached per command;
- otherwise, the most important **shellcheck** finding for the input (see [Linting](#linting)).

The **mode line** below it shows the shell and version, the directory (or `sandbox`), the last exit status and time, the open script and position, and `tty` when on.

## Scripts

```sh
> %open deploy.sh staging
deploy.sh  7 commands  args: staging
▸ 1     2 set -euo pipefail
  2     4 target=${1:-staging}
  3  7-11 package() { …
  …
```

`%open FILE [args…]` splits a script into its **top-level commands**: functions, `if`/`for`/`while`/`case` blocks, heredocs and continued lines stay whole, and comments stick to the command below them. The arguments become `$1`, `$2`, … in the session. Then:

| Command | Does |
| --- | --- |
| `%next [N]` | run the next command (or N), each as its own block labelled `deploy.sh:7-11  (3/7)` |
| `%step` | put the next command into the input, to change it before pressing Enter |
| `%run [-k] [--all]` | run the rest, stopping at the first failure (`-k` keeps going, `--all` starts over) |
| `%run FILE [args…]` | open and run a file |
| `%goto N` · `%goto +2` | move the position |
| `%break 12 30` | breakpoints: `%run` stops before the commands containing those lines (`%break` lists, `-d 12` removes, `--clear`) |
| `%script` | the outline with the current position |
| `%edit` | open the script in `$EDITOR`, reload it and keep the position at the same line |
| `%close` | close it |

At a breakpoint, the session holds the script's state so far: look at it with `%vars`, `%env` or any shell code, then `%next` or `%run` to go on. Breakpoints show as `●` in `%script`.

`shtick deploy.sh staging` opens a script right away. Remember that the script runs *in* the session: an `exit` in it ends the session, and `set -e` stays on afterwards.

### Watching a script

`%watch build.sh [args…]` runs a script and runs it again every time you save it — keep shtick open next to your editor. Each run is a **new process** of the session's shell (`bash build.sh args`), so runs start clean and an `exit` in the script doesn't end your session. After each run, shellcheck's findings for the file are counted (`%lint build.sh` for details). Press `q` or Ctrl+C to stop.

## Tracing

`%trace` runs code with `set -x` and shows each executed command — with variables and command substitutions already expanded — in a separate list after the output:

```text
> %trace --next

  ▸ deploy.sh:18  (6/7)  · traced
> package
│
│ trace · 3 commands
│              1  package
│    deploy.sh:9    mkdir -p build  (package)
│   deploy.sh:10    printf 'app for %s\n' staging  (package)
│
╰─ ✓ exit 0 · 5ms · ~/project
```

Each line has its **location** (a line in this cell, `[2]:3` for a function defined in cell 2, `deploy.sh:9` for code from a script), **nesting** (subshells, command substitutions and function calls are indented) and the **function** it ran in. Tracing is switched off afterwards and your `PS4` is restored.

| | |
| --- | --- |
| `%trace` | the previous cell again |
| `%trace CODE` | CODE (or the code on the following lines) |
| `%trace --next` | the open script's next command |
| `%trace FILE [args…]` | a whole script file, with its own line numbers |

dash doesn't report files or nesting, and doesn't trace `for` headers; zsh reports its own depth.

## Linting

With [shellcheck](https://www.shellcheck.net) installed, the input is linted in the background as you type (only when typing pauses, never blocking the prompt). The most severe finding appears in the key bar:

```text
⚠ SC2115  Use "${var:?}" to ensure this never expands to / .  |  DETAILS: %lint
```

`%lint` prints the full report for the previous cell — each finding with the source line, a marker under the problem and a link to the shellcheck wiki. `%lint N` checks cell N, `%lint FILE` a file (using its shebang's dialect), `%lint script` the open script.

Cells often use variables set in earlier cells, so `SC2034` (assigned but unused) and `SC2154` (referenced but not assigned) are skipped for cells — change `lint_exclude` in the [configuration](configuration.md). Parse errors aren't shown while a block is still unfinished. shellcheck doesn't support zsh, and `%config lint=off` hides the key-bar hint.

## The sandbox

```text
> %sandbox on --copy
sandbox on  a copy of ~/project
in /var/folders/…/shtick-sandbox-k2x9 · deleted by %sandbox off

> ./build.sh && rm -rf tmp
│
│ sandbox  + dist/  ~ VERSION  − tmp/
│
╰─ ✓ exit 0 · 212ms · sandbox
```

`%sandbox on` moves the session into an empty temporary directory; `--copy` makes it a copy of the current one. After every cell, shtick lists what changed: **+** created, **~** modified, **−** deleted (new directories are listed once, not file by file). `%sandbox off` goes back to the original directory and deletes the sandbox. `%sandbox status` shows where it is.

It's a *working-directory* sandbox, meant for relative paths: it isn't a security boundary. Absolute paths, `cd ..` and `$HOME` reach the real filesystem, and the footer warns `outside the sandbox` when a cell leaves it.

## Expectations and tests

Turn what you just checked by eye into checks that can run again:

```text
> ./build.sh
…
> %expect exit 0
✓ [4] exit 0
> %expect stdout contains "built"
✓ [4] stdout contains built
> %expect file exists dist/app.tar.gz
✗ [4] file exists dist/app.tar.gz  got no file dist/app.tar.gz
```

`%expect` checks the **previous cell** right away and records the check with it:

| Expectation | Passes when |
| --- | --- |
| `exit N` · `exit != N` · `exit nonzero` · `ok` · `fails` | the exit status matches |
| `stdout contains TEXT` · `not-contains TEXT` | the output has (or lacks) TEXT |
| `stdout equals TEXT` | the output is TEXT (ignoring a trailing newline) |
| `stdout matches REGEX` | a Python regex matches (multi-line) |
| `stdout empty` · `not-empty` · `lines N` | |
| `stderr …` · `output …` | the same checks on stderr, or stdout and stderr together |
| `file exists PATH` · `file missing PATH` · `dir exists PATH` | relative to the cell's directory |
| `file contains PATH TEXT` | |
| `duration < 2s` · `duration > 100ms` | |
| `sandbox changed PATH` · `sandbox unchanged` | the cell's sandbox changes |

Quote arguments with spaces; `\n` and `\t` describe multi-line text (`stdout equals "one\ntwo"`). When `equals` fails, a line-by-line diff of expected and actual output is shown. `%expect` alone lists the previous cell's checks; `%expect --clear` removes them.

**`%test`** runs every cell since the session started (or since `%test reset`) again in a **fresh session** of the same shell, and checks each cell's expectations:

```text
· [1] set -- staging
· [2] target=${1:-staging}
✓ [3] ./build.sh
    ✓ exit 0
    ✓ stdout contains built
✗ [4] tar -tzf dist/app.tar.gz
    ✗ stdout contains VERSION  got stdout was 'app/…'

✗ 1 failed · 2 passed · 4 cells · 340ms
```

Cells without expectations still run, as setup. If any recorded cell ran in the sandbox, the whole test runs in a new sandbox (a copy of the original directory if the sandbox was a copy), so re-running never touches your files. Otherwise it runs in the directory shtick started in. Setup that shtick did for you, like `%open`'s arguments, is recorded too.

**`%save-test FILE`** writes the recorded cells and expectations to a file, and **`shtick test FILE…`** runs it without the UI — exit status 0 when everything passes, 1 otherwise, so it fits CI:

```sh
shtick test tests/*.shtick          # every cell and check
shtick test -q tests/*.shtick       # only failures and the summary
shtick test --shell dash t.shtick   # against another shell
shtick test --lint tests/*.shtick   # also show shellcheck findings per cell (they don't fail the run)
```

The file format is described in the [configuration guide](configuration.md#test-files). It's still a shell script: `bash build.shtick` runs the code and ignores the checks.

## Saving your work

- **`%save FILE`** writes the cells of this session that **succeeded** into a script, with a shebang for the session's shell (`#!/usr/bin/env bash`, or the exact path when you picked one like `/bin/bash`), and makes it executable. `%commands` are never included. `%save FILE 3-7 10` takes a range; `-a` includes failed cells; `-f` overwrites. In the sandbox, relative paths are saved in the original directory, not the throwaway one.
- **`%history`** shows this session's cells; `-n` numbers, `-s` exit statuses (✓/✗), `-l N` the last N across sessions, `-g PATTERN` searches every session. Ranges: `4`, `4-6`, `4:7` (end excluded), `~1/` (the previous session), `~1/2-3`, `12/1-4` (session 12).
- **`%rerun [range]`** runs cells again; **`%recall [range]`** puts them into the input to edit.

History is kept in SQLite (see [configuration](configuration.md#where-things-live)).

## Portability

### `%shell`

`%shell dash` switches the session to another shell (a fresh session in the same directory). Any name on `PATH` or a path works: `sh`, `dash`, `zsh`, `/bin/bash`, `/opt/homebrew/bin/bash`. The mode line shows which one is running.

### `%compare`

```text
> %compare /bin/bash bash dash -- [[ $v == y* ]] && echo match; echo "${BASH_VERSINFO[0]:-none}"
         bash 3.2.57      bash 5.3.15               dash
         /bin/bash        /opt/homebrew/bin/bash
──────────────────────────────────────────────────────────────────
status   exit 0           exit 0                    shell exited 2
stdout   3                5                         —
stderr   —                —                         cell: 1: Bad substitution
✗ differs in status, stdout, stderr
```

`%compare` runs the same code in a **fresh session of each shell** and puts exit status, stdout and stderr side by side, highlighting the rows that differ. Each shell runs in its own empty temporary directory (`--here` uses the current directory instead).

- `%compare bash dash zsh -- CODE`, or the code on the following lines;
- with no code, the previous cell;
- with no shells, every installed shell (plus `/bin/bash` on macOS, which is bash 3.2 — Homebrew's is 5.x).

When two shells are the same kind, the headers show their paths.

### `%env`

`%env` shows how the **environment** differs from when the session started — what a program started from the session would see: `+ FOO=bar` exported, `~ PATH  +/opt/tools/bin` (for `…PATH` variables, the entries added and removed), `− LOGNAME` removed. `%env NAME` shows values, `-a` the whole environment.

### `%vars`

`%vars` lists what the session has **defined or changed since it started**: `+` new variables and functions, `~` changed values and redefined functions, `−` unset ones. Shell-managed variables (`RANDOM`, `LINENO`, `PWD`, …) are left out. `%vars NAME` shows a full declaration (or a function's body), `%vars -a` every variable. dash can't list functions, so only variables are shown there.

## Themes

Three palettes, shared by the input, the output and syntax highlighting: `void` (near-black with a warm accent, the default), `nebula` (violet) and `matrix` (green). `%theme` lists them, `%theme nebula` switches.
