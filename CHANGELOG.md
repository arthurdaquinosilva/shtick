# Changelog

## 0.1.1

- **New color themes**, no longer shared with ember: `tide` (blue-black with a teal accent, the new default), `phosphor` (amber, like an old CRT) and `chalk` (for light terminal backgrounds). `void`, `nebula` and `matrix` are gone; a config that names one gets a warning and the default theme.

## 0.1.0

First release.

- **Session engine**: cells run in one persistent bash, sh, dash or zsh process — variables, functions, aliases, `cd` and options carry over. Separate stdout and stderr, exit status and working directory per cell, input to running cells, Ctrl+C that interrupts the cell and keeps the session, and a clear report when `exit`, `exec` or `set -e` end the shell.
- **Blocks UI**: pixel `$HTICK` banner (a shell prompt's `$` for the S), full-width input bar with bash highlighting, a key bar that follows what you're doing, a mode line, output rails with red stderr, footers with status, time and directory.
- **Smart Enter** decided by the shell's own parser; Shift+Enter for new lines; block indentation; vi or emacs keys.
- **Live lint** with shellcheck in the key bar, and `%lint` reports.
- **Flag hints** from man pages and `--help` while typing options.
- **Scripts**: `%open`, `%next`, `%step`, `%run`, `%goto`, `%script`, `%edit`, `%close`.
- **Tracing**: `%trace` lists every executed command with location, nesting and function, separately from the output.
- **Sandbox**: `%sandbox on [--copy]` shows files created, modified and deleted by each cell.
- **Expectations and tests**: `%expect`, `%test`, `%save-test`, and `shtick test` for CI.
- **Saving**: `%save` writes the successful cells as a script; SQLite `%history` with ranges, `%rerun`, `%recall`.
- **Portability**: `%shell`, `%compare` side by side across shells, `%vars` for what a session defined or changed.
- Completion of commands, files, variables and `%commands`; pasted `$ ` prompts are stripped; three themes; profiles and `config.toml`.
- **vi mode**: `v` in normal mode opens the cell in `$EDITOR`, `[VISUAL]` and `[REPLACE]` in the mode line, normal-mode hints in the key bar, `%vi --save`.
- `%config name=value --save` writes settings to `config.toml`, keeping the rest of the file.
- `%watch FILE` re-runs a script on every save, with a shellcheck summary per run.
- `%break LINE` breakpoints for `%run`.
- `%env` shows environment changes since the session started.
- Input typed into a cell is recorded and replayed by `%test` and saved tests (`#% stdin`).
- A failing `equals` expectation shows a diff; `\n` and `\t` work in expectation text.
- `shtick test --lint` shows shellcheck findings per cell.
