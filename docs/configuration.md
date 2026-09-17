# Configuration

## Where things live

| Path | Contents |
| --- | --- |
| `~/.config/shtick/config.toml` | Settings (this page) |
| `~/.local/share/shtick/history.sqlite` | Cell history with exit statuses, grouped by session |

`XDG_CONFIG_HOME` and `XDG_DATA_HOME` are respected. Nothing else is written outside your working directory: sessions, cells and sandboxes live in the system's temporary directory and are removed when shtick exits.

## config.toml

Every setting is optional. This example shows each one with its default:

```toml
theme = "tide"                          # tide · phosphor · chalk (for light terminals)
editing_mode = "emacs"                  # emacs · vi
shell = "bash"                          # bash · sh · dash · zsh · a path like "/bin/bash"
lint = true                             # live shellcheck hint in the key bar (when shellcheck is installed)
lint_exclude = ["SC2034", "SC2154"]     # checks skipped for cells (they see variables from other cells)
tty = false                             # run cells attached to a pseudo-terminal (%tty)
save_shebang = ""                       # %save's first line; "" picks #!/usr/bin/env <shell>
startup = []                            # shell lines run in every new session, e.g. ["set -o pipefail", "export LC_ALL=C"]
```

Inside shtick, `%config` lists the current values and `%config name=value` changes one for the session (`%config lint=off`, `%config shell=dash`, `%config theme=matrix`). Add `--save` to also write it to `config.toml` — other lines and comments in the file are kept. `%vi --save` does the same for vi mode. Settings with a problem are reported as warnings at startup instead of stopping shtick.

`startup` lines also run after `%restart`, `%shell` and when a session had to be restarted because the shell exited.

Sessions start **non-interactively** and without startup files: bash with `--noprofile --norc`, zsh with `-f`, and `BASH_ENV`/`ENV` removed from the environment — cells behave like a script, not like your login shell. Put what your scripts expect in `startup`. In bash, aliases are enabled (`shopt -s expand_aliases`).

## Environment variables

| Variable | Effect |
| --- | --- |
| `SHTICK_THEME` | theme, overriding `config.toml` |
| `SHTICK_SHELL` | shell, overriding `config.toml` |
| `SHTICK_PROFILE` | profile to use |
| `VISUAL` / `EDITOR` | editor for Ctrl+O and `%edit` |

## Profiles

`shtick --profile work` (or `SHTICK_PROFILE=work`) keeps a separate `config.toml` and history in `~/.config/shtick/profiles/work/` and `~/.local/share/shtick/profiles/work/`.

## Command-line options

```text
shtick [SCRIPT [ARGS…]]      interactive; with SCRIPT, open it to step through (%open)
  -c CODE                    run CODE as a cell, print the block, exit with the cell's status
  -s, --shell SHELL          bash (default), sh, dash, zsh or a path
  --profile NAME             separate config and history
  --theme tide|phosphor|chalk
  --vi                       vi key bindings
  --version

shtick test FILE…            run test files; exit status 1 if any check fails
  -s, --shell SHELL          override the shell recorded in the files
  -q, --quiet                only print failing cells and the summary
  --lint                     show shellcheck findings for each cell (reported, not failures)
```

Piping code into shtick (`echo 'ls' | shtick`) runs it like `-c`.

## Test files

`%save-test` writes a file like this:

```sh
#!/usr/bin/env -S shtick test
# shtick test · shell: bash · sandbox: copy
#%% [1] %open deploy.sh
set -- 'staging'
#%% [2] deploy.sh:4  (2/7)
target=${1:-staging}
#%% [3]
./build.sh
#% expect exit 0
#% expect file exists dist/app.tar.gz
```

- The header's `shell:` is the shell to run with; `sandbox:` is `off` (run in the file's directory), `empty` (a new empty temporary directory) or `copy` (a temporary copy of the file's directory).
- Each `#%%` line starts a cell (the rest of the line is a label); the lines until the next `#%%` are its code.
- `#% stdin "…"` (a JSON string) is given to the cell as its input — shtick records what you typed while the cell ran.
- `#% expect …` lines are checked after the cell, with the same syntax as [`%expect`](features.md#expectations-and-tests).

Since cells and checks are comments, the file is also a valid script, and it's easy to write or edit by hand. Being executable with that shebang, `./deploy.shtick` runs the tests too.

## Themes

| Theme | Look |
| --- | --- |
| `tide` | blue-black bar, teal accent, lime and amber highlights (default) |
| `phosphor` | amber on dark, like an old CRT terminal |
| `chalk` | for terminals with a light background |

The same palette colors the input, cell output, the key bar and syntax highlighting.
