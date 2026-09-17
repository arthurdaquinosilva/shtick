# How it works

The hard part of shtick is the **session engine** (`src/shtick/engine.py`): running cells in one long-lived shell so state persists, while still knowing exactly when each cell finished, how, and what it printed where. This page describes the design and the trade-offs behind it.

## Running a cell

The shell is started **non-interactively** — `bash --noprofile --norc -s`, `zsh -f -s`, `dash -s` — in its own process group, reading commands from its stdin. That makes cells behave like a script, and it keeps shtick's own keyboard and signals separate from the shell.

For each cell, shtick writes the code to a file and sends the shell one **driver line**:

```sh
{ printf 'TOKEN\0started\0' >STATUS; . CELL; } <STDIN
__shtick_rc=$?; __shtick_in=; __shtick_pending=; printf 'TOKEN\0%s\0%s\0' "$__shtick_rc" "$PWD" >STATUS
```

and the cell file's first line — the same line as your code's first line, so line numbers don't move — starts with `__shtick_in=1; …; __shtick_status N;`.

- **Sourcing** (`.`) runs the cell in the current shell, so variables, functions, `cd` and options stay. `return` inside a cell ends the cell, like in a sourced script.
- **stdout and stderr are separate pipes**, read as data arrives, which is how stderr gets its own rail.
- **Completion is reported on a named FIFO**, tagged with a random per-cell token: the exit status and `$PWD`, NUL-separated. Nothing extra appears in the output, and a stale report can't be mistaken for the current cell's. When the report arrives, whatever is already in the output pipes is read; output from background jobs that arrives later is shown above the prompt.
- `__shtick_status N` (a function that `return`s N) makes `$?` at the start of the cell the previous cell's status.
- `__shtick_in` marks that a cell is running (see Ctrl+C below). Variables starting with `__shtick` are hidden from `%vars` and completion.
- Before sending the cell, shtick runs the shell's own `-n` syntax check. That matters for dash, which **exits** when a sourced file has a syntax error.

## Input

Each cell's stdin is a FIFO of its own. shtick opens it read-write before sending the cell, so data written early is kept, and closes it only after the shell reported `started` — meaning the shell holds the read end, so closing delivers end-of-file to *that cell*. Without that handshake, a cell with no input could block forever opening the FIFO.

While a cell runs, the terminal is in non-canonical mode and shtick forwards what you type: printable keys are echoed on the rail, Enter sends the line, Ctrl+D closes the cell's stdin. In `%tty` mode the cell's stdin, stdout and stderr are a pseudo-terminal instead, which does its own echo and line editing.

## Ctrl+C

Ctrl+C reaches shtick (the shell has its own session), and shtick sends SIGINT to the shell's process group. A non-interactive shell would normally exit when its child dies of SIGINT, so the session sets:

```sh
trap 'if [ -n "${__shtick_in-}" ]; then return 130; else __shtick_pending=1; fi # shtick' INT
```

`return` from a trap handler returns from the *sourced cell*, so the rest of the cell is skipped and the session survives with its state.

The signal can also land a few milliseconds too early — while the shell is still reading the driver line, before the cell file is sourced. There, `return` would fail in bash (the interrupt is lost and the cell runs to the end) and would *exit* zsh. So outside a cell the trap only records `__shtick_pending`, and the cell's first line sets `__shtick_in=1` and then returns 130 if an interrupt is pending. Marking the cell as running *before* checking leaves no gap: a trap that runs after the mark returns directly. The pending flag is cleared when a cell ends, so a stray interrupt can't cancel the next cell. Two more timing details, found by interrupting cells 1–8ms after they were sent, hundreds of times per shell:

- An interrupt while the shell is still **reading** the driver line makes zsh discard the line, so the cell never starts. Nothing of the cell is running yet at that point, so shtick holds the interrupt until the shell reports `started`.
- An interrupt can hit a command **between fork and exec**: the child catches it with the inherited trap, then execs and runs to the end, and the shell's own trap fires only afterwards. When an interrupt came within 50ms of the cell starting and the cell is still running 200ms later, shtick sends it once more.

A test interrupts cells the instant they start, in every shell.

Two bash quirks turned up while testing this on bash 5.3 and macOS's bash 3.2:

- After a trap returned while bash was **waiting for a child** (an interrupted `sleep`), bash stops running traps from loops made only of builtins (`while :; do :; done`) — the next Ctrl+C is ignored. bash 5 recovers after a fork; bash 3.2 only when the trap is set again. So after an interrupted cell, the next driver line starts with a small function that does both — and only re-sets the trap if it's still shtick's (the `# shtick` marker), leaving a trap the user set alone.
- bash 3.2 ignores the trap's `return 130` value inside such loops; shtick reports 130 anyway when it sent the interrupt.

If a cell ignores SIGINT, a second Ctrl+C sends SIGKILL to the process group and the session restarts.

## When the shell dies

`exit`, `exec`, `set -e` with a failing command, or a crash end the shell process. The engine notices (the process exited instead of reporting on the FIFO), reports the shell's exit status, and starts a new session in the last known directory. Keeping the old state isn't possible, so the UI says it was lost. For `set -e` this is exactly what would happen in a script; working around it (for example by running cells in a context where `errexit` is suppressed) would change what `set -e` means.

## Tracing

`%trace` prepends `PS4='+<US>shtick<US>${BASH_SOURCE[0]-}<US>${LINENO-}<US>${FUNCNAME[0]-}<US><US> '; set -x;` to the cell's first line (so line numbers don't move). `<US>` is the ASCII unit separator: `\x01` would be a more obvious choice, but bash 3.2 uses it internally and drops it from `PS4`. The `-` defaults keep `set -u` cells working. The output is split line by line: lines with the marker become trace entries, everything else is passed through as output. bash repeats the leading `+` for nesting; zsh reports its depth with `%e` and file/line with `%x`/`%I`. Entries from the engine's own driver line are dropped, and tracing is turned off afterwards with the previous `PS4` restored.

## Smart Enter

Whether Enter runs the input is decided by `bash -n` (or `zsh -n` in zsh sessions) on the text, never by guessing: only the parser's messages about running out of input (`unexpected end of file`, `unexpected EOF while looking for`, the here-document warning) count as unfinished. Other syntax errors count as complete, so running shows the error. Results are cached per text, so redrawing the key bar doesn't fork a shell each time.

## Trade-offs

- **Pipes, not a terminal.** Separate stdout/stderr need pipes, so programs see a non-terminal. `%tty on` gives them one and merges the streams. Exact interleaving of stdout and stderr lines written within microseconds can differ from a terminal.
- **Scripts run in the session.** Stepping through a script sources its commands in the session, which is what makes state inspectable between steps — and means `exit` in the script ends the session.
- **A cell's `$0`** is the shell, and `BASH_SOURCE` is a temporary file name. Error messages are rewritten to show cell numbers.
- **zsh** is a little slower per cell (a few milliseconds) than bash and dash.
