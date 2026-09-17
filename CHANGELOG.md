# Changelog

## 0.1.0

First release.

- **Session engine**: cells run in one persistent bash, sh, dash or zsh process — variables, functions, aliases, `cd` and options carry over. Separate stdout and stderr, exit status and working directory per cell, input to running cells, Ctrl+C that interrupts the cell and keeps the session, and a clear report when `exit`, `exec` or `set -e` end the shell.
- **Blocks UI**: pixel `SHTICK_` banner, full-width input bar with bash highlighting, a key bar that follows what you're doing, a mode line, output rails with red stderr, footers with status, time and directory.
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
