<div align="center">

<img src="https://raw.githubusercontent.com/arthurdaquinosilva/shtick/main/docs/assets/cover.svg?sanitize=true" alt="shtick's start screen: the pixel $HTICK wordmark, version, shell and directory, the input bar and the key bar" width="900">

# ✓ shtick

**A playground for writing and testing shell scripts.**
Run shell code cell by cell in a persistent session, step through scripts, trace them, lint them, compare shells, sandbox them and assert on the results.

[![PyPI](https://img.shields.io/pypi/v/shtick)](https://pypi.org/project/shtick/)
[![tests](https://github.com/arthurdaquinosilva/shtick/actions/workflows/tests.yml/badge.svg)](https://github.com/arthurdaquinosilva/shtick/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/arthurdaquinosilva/shtick/blob/main/pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](https://github.com/arthurdaquinosilva/shtick/blob/main/LICENSE)

</div>

## See it in action

<p align="center">
<img src="https://raw.githubusercontent.com/arthurdaquinosilva/shtick/main/docs/assets/demo.svg?sanitize=true" alt="shtick running in a terminal: a loop with stdout and a red stderr line, %trace listing the commands a function ran, and a shellcheck warning in the key bar while typing" width="820">
</p>

The name: **sh** + **tick** (✓, a passing check) — and a *shtick* is a routine you rehearse until it works.

## Why shtick

- **Readable runs.** Every cell is a block: your code, its output on a rail (stderr in red), and a footer with the exit status, time and directory. Variables, functions, `cd` and `set` options carry over between cells.
- **Know what a script does.** Open a script and step through it command by command (`%open`, `%next`, `%step`), or `%trace` it to see every command that ran — with variables expanded — apart from the output.
- **Catch mistakes while typing.** shellcheck lints the input as you type and puts the most important finding in the key bar. Typing `tar -x` shows what each flag means.
- **Try things without fear.** `%sandbox on` runs cells in a throwaway directory and lists the files each cell created, changed or deleted.
- **Turn experiments into tests.** `%expect exit 0`, `%expect stdout contains "done"`, `%test` to re-run everything in a fresh session, `%save-test` to get a file that `shtick test` checks in CI.
- **Tight loops.** `%watch build.sh` re-runs a script on every save; `%break 42` stops `%run` at a line so you can look around.
- **Portable scripts.** `%compare bash dash zsh` runs the same code in each shell side by side; on macOS, `%compare /bin/bash bash` shows what bash 3.2 does differently.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/arthurdaquinosilva/shtick/main/install.sh | sh
```

The script puts shtick in its own virtual environment (`~/.local/share/shtick/venv`) and links the `shtick` command into `~/.local/bin`, telling you if that isn't on your `PATH`. Run it again to upgrade; add `-s -- 0.1.1` after `sh` for a specific version, or `-s -- --uninstall` to remove it (your config and history stay). [Read it first](https://github.com/arthurdaquinosilva/shtick/blob/main/install.sh) if you like — it's short.

Or with a Python package manager:

```sh
pipx install shtick     # the `shtick` command everywhere, isolated from your projects
pip install shtick      # or into the current environment
```

To try the latest unreleased code: `pipx install git+https://github.com/arthurdaquinosilva/shtick.git`.

Then run `shtick`. Requires Python 3.10+ on macOS or Linux, and bash. [shellcheck](https://www.shellcheck.net) is optional but recommended (`brew install shellcheck`, `apt install shellcheck`); dash and zsh are used when installed.

## Quick tour

```sh
> for f in *.log; do grep -c ERROR "$f"; done    # a cell: output, exit status, time, directory
> %open deploy.sh staging    # load a script ($1=staging) and list its commands
> %next                      # run the next one · %step: edit it first · %run: the rest
> %trace --next              # run the next command with every executed step listed
> %sandbox on --copy         # work in a temporary copy of this directory
> ./build.sh
> %expect exit 0             # check the last cell and record the check
> %expect file exists dist/app.tar.gz
> %test                      # re-run all cells in a fresh session, check expectations
> %save-test build.shtick    # later, in CI: shtick test build.shtick
> %watch build.sh            # run it again on every save · q stops
> %compare bash dash         # the previous cell in both shells, side by side
> %save build-steps.sh       # the cells that worked, as a script
> %help                      # every key and command
```

| | |
| --- | --- |
| **Enter** | run the cell (adds a newline while the code is unfinished — decided by the shell's own parser) |
| **Shift+Enter** · Alt+Enter · Ctrl+J | insert a newline |
| **Tab** | complete commands, files, `$variables` and `%commands` |
| **→** · **↑ ↓** · Ctrl+R | accept the grey suggestion · history · search history |
| **Ctrl+O** | edit the cell in `$EDITOR` |
| **Ctrl+C** | clear the input · interrupt a running cell (again: kill the session) |
| **Ctrl+D** | exit · end a running cell's stdin |

**vi mode**: `shtick --vi`, or `%vi --save` to keep it. Esc for normal mode, Enter runs from normal mode, `j`/`k` history, `/` search, `v` opens `$EDITOR`.

Shift+Enter needs a terminal that reports modified keys (iTerm2, WezTerm, Ghostty, kitty, xterm; inside tmux set `extended-keys on`). Alt+Enter and Ctrl+J work everywhere.

## Documentation

| Guide | What's inside |
| --- | --- |
| [Features](https://github.com/arthurdaquinosilva/shtick/blob/main/docs/features.md) | Cells and the session, typing, scripts, tracing, linting, the sandbox, expectations and tests, portability |
| [Command reference](https://github.com/arthurdaquinosilva/shtick/blob/main/docs/commands.md) | Every `%command` with its options (generated from the code) |
| [Configuration](https://github.com/arthurdaquinosilva/shtick/blob/main/docs/configuration.md) | `config.toml`, themes, profiles, command-line options, test files |
| [How it works](https://github.com/arthurdaquinosilva/shtick/blob/main/docs/how-it-works.md) | The session engine: how cells run, finish, read input and get interrupted |
| [Releasing](https://github.com/arthurdaquinosilva/shtick/blob/main/docs/releasing.md) | How versions are published to PyPI |

## Command line

```sh
shtick                          # interactive, in bash
shtick --shell dash             # or sh, zsh, a path like /bin/bash
shtick deploy.sh staging        # open a script to step through
shtick -c 'echo hi; false'      # run code as a cell, print the block, exit with its status
shtick test *.shtick            # run saved tests; exit status 1 if any check fails (--lint: shellcheck too)
shtick --vi --theme phosphor --profile work
```

## Development

```sh
git clone https://github.com/arthurdaquinosilva/shtick.git && cd shtick
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                     # engine, commands, and the UI in a pseudo-terminal
.venv/bin/python scripts/gen_command_docs.py   # regenerate docs/commands.md
.venv/bin/python scripts/screenshot.py         # re-record docs/assets/*.svg from a real session
```

shtick is built on [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) (input and layout), [Rich](https://github.com/Textualize/rich) (output) and [Pygments](https://pygments.org/) (highlighting). It's the sibling of [ember](https://github.com/arthurdaquinosilva/ember), an interactive Python shell.

## Limits

- **Not a login shell.** shtick is for writing and testing scripts, not for replacing zsh or bash day to day. There's no job control (`fg`, `bg`, Ctrl+Z).
- **No full-screen programs in cells.** vim, htop, less or an ssh session need a real terminal; run them outside shtick. `%tty on` helps programs that only check whether they're on a terminal (colors, `[ -t 1 ]`), at the cost of merging stderr into stdout.
- **Cells run non-interactively**, like scripts: `read -p` prompts aren't printed unless `%tty on`, aliases are expanded (bash), no `.bashrc` is read.
- **`exit`, `exec` and a failing command under `set -e` end the session.** shtick says so and starts a new one in the same directory, but variables and functions are gone.
- **The sandbox is a working directory**, not a security boundary: absolute paths, `cd ..` and `$HOME` reach the real filesystem (the footer warns when a cell leaves it).
- **Linting** needs shellcheck, which doesn't support zsh.

## License

[MIT](https://github.com/arthurdaquinosilva/shtick/blob/main/LICENSE) © Arthur D'Aquino
