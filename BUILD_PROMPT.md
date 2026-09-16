# Build shtick — a playground for writing and testing shell scripts

Paste this into a new Claude Code session started in `~/projects/shtick`, or say:
"Read BUILD_PROMPT.md and build it."

---

## The goal

Build **shtick**: a terminal app for **writing and running shell scripts to test them**. You type shell code into a nice input bar, run it in a persistent shell session, and each run becomes a readable block with its output, exit code, duration and working directory. It helps you find out whether a script does what you think — step through scripts, trace them, lint them, check them against different shells, run them in a sandbox and assert on results.

**Not in scope:** replacing zsh/bash as a login shell, job control (`fg`/`bg`), or full-screen interactive programs inside cells (vim, htop, ssh sessions). Say so in the docs.

The name is a pun: **sh** + **tick** (✓, a passing check), and a "shtick" is a routine you rehearse until it works.

## Decisions already made

| | |
| --- | --- |
| Name | `shtick` — command `shtick`, PyPI package `shtick` (free as of 2026-09-16), repo `arthurdaquinosilva/shtick` |
| Default shell | **bash**; sessions can switch to `sh`, `dash`, `zsh` |
| Project | New, standalone project in `~/projects/shtick` (not a mode of ember, no dependency on ember) |
| Language / stack | Python ≥ 3.10, **prompt_toolkit** (input, layout, keys), **Rich** (output), **Pygments** (`BashLexer`) |
| License | MIT, © Arthur D'Aquino <arthurdaquinosilva@gmail.com> |
| Platforms | macOS first (the author's machine), Linux supported |

## The sibling project: ember — study it first

shtick is ember's sibling and should look and feel like it. ember is an interactive Python shell built in the same stack:

- Code: `~/projects/modern-python-repl` · GitHub: https://github.com/arthurdaquinosilva/ember · PyPI: `ember-shell`
- Cover image of its UI: `~/projects/modern-python-repl/docs/assets/cover.png`

**Read these ember files before designing anything, then copy and adapt what fits** (copy, don't import — shtick must not depend on ember):

| ember file | What to reuse |
| --- | --- |
| `src/ember/ui.py` | The **block layout**: full-width filled input bar with placeholder and counter, context-aware `LABEL: Key` key bar, `[INSERT]/[NORMAL]` mode line, completion menu, vi/emacs modes, Shift+Enter support via xterm modifyOtherKeys (+ the escape-sequence registration), `patch_stdout` so background output appears above the prompt, smart Enter |
| `src/ember/banner.py` | Pixel wordmark from half-block characters. Draw `SHTICK_` — you'll need 5×7 glyphs for S, H, T, I, C, K |
| `src/ember/theme.py` | Palettes shared by prompt_toolkit, Rich and Pygments; the three themes (void, nebula, matrix) |
| `src/ember/output.py` | The output **rail** (`│`) writer and the spinner for slow cells |
| `src/ember/shell.py` | Cell echo and footer chrome (`╰─ ✓ 2.8ms · …`), spacing between blocks |
| `src/ember/history.py` | SQLite history by session + prompt_toolkit history adapter |
| `src/ember/transform.py` | Stripping pasted prompts (adapt to `$ ` / `# ` / `% ` shell prompts) |
| `src/ember/config.py` | `config.toml`, profiles, settings validation, `%config` |
| `src/ember/magics/__init__.py` | Magic registry and `parse_args` option parser |
| `scripts/screenshot.py`, `scripts/gen_magic_docs.py` | Recorded SVG screenshot from a real pty session; docs generated from the registry |
| `.github/workflows/*.yml`, `docs/releasing.md` | CI matrix and PyPI trusted-publishing release |
| `tests/` | Patterns for testing a shell engine and the terminal UI |

## Architecture

### The session engine (the hard, important part)

Cells run in **one long-lived shell process**, so variables, functions, aliases, `cd`, `export` and `set` options persist between cells. For each cell the engine must:

1. Send the code without it being echoed or mangled (multi-line code, heredocs, quotes).
2. Stream stdout and stderr **separately** as they arrive, so stderr can be shown differently.
3. Detect reliably when the cell finished, and capture its **exit code** and resulting **`$PWD`** — without that bookkeeping appearing in the output.
4. Support `read` / stdin from the user while a cell runs.
5. Ctrl+C interrupts the running cell (signal the cell's process group), not shtick itself.
6. Survive the script calling `exit`, `exec`, or crashing the shell: report it clearly, restart the session, tell the user state was lost.

A reasonable design to start from (validate it, change it if you find better): write each cell to a temp file and run it with `source` in the persistent shell; communicate completion over a **separate file descriptor or FIFO** (e.g. `printf '%s\0%s\0' "$?" "$PWD" >&3`) using a unique per-cell token; stdout/stderr on pipes. Offer a `%tty on` option that runs cells attached to a pty for programs that check `[ -t 1 ]` or print colors only on terminals (merging the streams). Think through `set -e` and `trap` interactions with `source`.

Write engine tests first for: state persistence, exit codes, `cd`, stderr separation, multi-line/heredoc cells, `exit` inside a cell (session restarts), long-running output streaming, Ctrl+C, and each supported shell.

### Commands

Use ember's magic style with a `%` prefix (a line starting with `%` is essentially never valid shell). `%help` lists everything; `%help name` explains one.

## Features, in priority order

**MVP (build these first, end to end, polished):**

1. **Blocks UI.** Input bar with bash syntax highlighting, the key bar, the mode line (shell name + version, cwd, last exit status and time). Each cell:
   ```
   > for f in *.log; do grep -c ERROR "$f"; done
   │
   │ 3
   │ 0
   │
   ╰─ ✓ exit 0 · 12ms · ~/project
   ```
   stderr lines get a visually distinct rail/color; a non-zero exit shows `✗ exit 2`. Two blank lines between blocks (ember's spacing).
2. **Smart Enter.** Run when the code is complete; insert a newline for unfinished constructs (`if`/`then` without `fi`, open quotes, heredocs, trailing `\`, `|`, `&&`). Use the real shell's parser where possible (e.g. `bash -n` on the text) rather than regex guesses. Shift+Enter / Alt+Enter / Ctrl+J always insert a newline.
3. **Live lint.** If `shellcheck` is installed, lint the input in the background as you type and show the most important finding in the key bar (`SC2086: Double quote to prevent globbing`), with `%lint` for the full report. Degrade gracefully when it's missing.
4. **Open and step through scripts.** `%open script.sh` loads a file and splits it into top-level commands (functions, `if`/`for` blocks and heredocs stay whole). Run it all, or step with `%next` / `%step` showing the current position; `%edit` opens the script in `$EDITOR` and reloads it.
5. **Trace view.** `%trace` runs a cell or script with `set -x` and a custom `PS4` (file, line, function), rendering each executed command — with variables expanded — as a readable, indented list separate from the output.
6. **Sandbox.** `%sandbox on` runs cells in a temporary directory (optionally a copy of the current one). After each cell, show what changed on disk: files created, modified, deleted. `%sandbox off` cleans it up.
7. **Expectations.** Lightweight checks on the last cell or a script: `%expect exit 0`, `%expect stdout contains "done"`, `%expect stderr empty`, `%expect file exists out.txt`. `%test` re-runs the recorded cells and expectations and prints a pass/fail summary. Save a test file (`%save-test`) that shtick can run non-interactively (`shtick test file`) with a non-zero exit on failure — useful in CI.
8. **Save your work.** `%save script.sh [cells]` writes the successful cells (or a range) into a clean script with a shebang; `%history` with ember-style ranges.

**Next:**

9. **Portability check.** `%shell dash` switches the session's shell; `%compare bash dash zsh` runs the same cell in fresh sessions of each and shows exit codes and output side by side, highlighting differences. On macOS `/bin/bash` is bash 3.2 while Homebrew's is 5.x — let users compare specific binaries (`%compare /bin/bash bash`).
10. **Flag hints** — ember's signature hints, for commands: while typing `tar -x`, show the flags from `--help`/`man` in the key bar with the current flag highlighted (`-x extract · -z gzip · -f FILE`). Parse lazily, cache per command, never block typing.
11. **Variables view.** `%vars` shows variables and functions defined or changed in this session (diff of `declare -p` / `declare -F` against the session start).
12. **Completion** of commands on `PATH`, files, variables (`$HO…`), and `%` commands.
13. **Paste handling.** Strip `$ ` / `# ` prompts (and drop their output lines) when pasting commands copied from docs.

## Quality bar and process

These lessons come from building ember; follow them.

- **Verify everything in a real terminal, not just unit tests.** Drive the app in a pseudo-terminal with `pexpect` and render the screen with `pyte` to check layout; send real keys (Shift+Enter, Esc in vi mode) through an isolated tmux server (`tmux -L <name> -f /dev/null` with `extended-keys on`, `extended-keys-format csi-u`). The author uses **iTerm2 + tmux 3.8** with extended keys enabled.
- **Test with `capfd`, not `capsys`**, when output is written to `sys.__stdout__`.
- **Rich style gotcha:** a theme style name can't be combined with modifiers (`"bold ember.accent"` is invalid) — define dedicated styles like `shtick.accent.bold`.
- **prompt_toolkit gotchas:** vi users need short `timeoutlen`/`ttimeoutlen` or Esc feels broken; Esc must also leave insert mode when it closes the completion menu; register the keybinding for "Enter in vi normal mode" so it isn't shadowed; use `erase_when_done=True` and print the finished cell yourself.
- **Don't let the UI lie.** No placeholder features, no fake screenshots — `docs/assets/demo.svg` must be recorded from a real session (adapt ember's `scripts/screenshot.py`).
- **Clean code:** match ember's style, run `pyflakes`, keep tests fast. CI runs on ubuntu-latest and macos-latest × Python 3.10–3.13 — remember CI machines lack clipboard tools, shellcheck, dash and zsh unless installed in the workflow (install shellcheck/dash/zsh there, and skip tests cleanly when a tool is missing).
- **Commit in small, meaningful steps** as milestones land.
- **Ask before anything outward-facing:** creating the GitHub repository, pushing, creating releases, publishing to PyPI.

## Deliverables

1. Working MVP (features 1–8) with tests for the engine and the UI.
2. `README.md` in ember's style: cover/screenshot, why, install (`pipx install shtick`), quick tour, key table, docs index, limits, license.
3. `docs/`: features, commands reference (generated from the registry), configuration, releasing.
4. `LICENSE` (MIT), `CHANGELOG.md`, `pyproject.toml` with full metadata, CI + release workflows.
5. When the MVP is done, summarize what works, what was verified and how, known limitations, and propose the next features — then ask whether to create the repository and publish.

Start by reading the ember files listed above and exploring the session-engine design with a small spike (a script that runs a few cells in a persistent bash and reports exit codes, cwd and separated stdout/stderr). Share the design you settle on, including the trade-offs you found, before building the full UI.
