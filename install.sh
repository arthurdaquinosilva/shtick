#!/bin/sh
# Install shtick into its own virtual environment and put the `shtick` command on your PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/arthurdaquinosilva/shtick/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- 0.1.1         # a specific version
#   curl -fsSL .../install.sh | sh -s -- --uninstall   # remove it again
#
# Running it again upgrades to the latest release. Settings (environment variables):
#   SHTICK_VENV     where the virtual environment lives   (default: ~/.local/share/shtick/venv)
#   SHTICK_BIN_DIR  where the `shtick` link goes           (default: ~/.local/bin)
#   SHTICK_PYTHON   the Python to use                      (default: the newest python3 >= 3.10 found)
#   SHTICK_PACKAGE  what pip installs, e.g. a git URL or a local checkout (default: shtick from PyPI)
#
# Everything is inside main(), so a download cut short runs nothing.

main() {
    set -eu

    # Next to shtick's history, but only this folder belongs to the installer.
    venv=${SHTICK_VENV:-${XDG_DATA_HOME:-$HOME/.local/share}/shtick/venv}
    bin_dir=${SHTICK_BIN_DIR:-$HOME/.local/bin}
    version=""
    uninstall=0

    for arg in "$@"; do
        case $arg in
            --uninstall) uninstall=1 ;;
            -h|--help) usage; return 0 ;;
            latest) version="" ;;
            -*) die "unknown option: $arg (see --help)" ;;
            *) version=${arg#v} ;;
        esac
    done

    if [ "$uninstall" = 1 ]; then
        do_uninstall
        return 0
    fi

    case $(uname -s) in
        Darwin|Linux) ;;
        *) die "shtick runs on macOS and Linux; this is $(uname -s)." ;;
    esac

    python=$(find_python) || die "shtick needs Python 3.10 or newer, and none was found.
  macOS:          brew install python
  Debian/Ubuntu:  sudo apt install python3 python3-venv
  Fedora:         sudo dnf install python3
Or point SHTICK_PYTHON at one and run this again."
    say "Using $python ($("$python" -c 'import sys; print(sys.version.split()[0])'))"

    if [ -n "${SHTICK_PACKAGE:-}" ]; then
        package=$SHTICK_PACKAGE
    elif [ -n "$version" ]; then
        package="shtick==$version"
    else
        package="shtick"
    fi

    if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        say "Creating a virtual environment in $venv"
        rm -rf "$venv"
        mkdir -p "$(dirname "$venv")"
        "$python" -m venv "$venv" 2>/dev/null || die "could not create a virtual environment with $python.
  On Debian/Ubuntu: sudo apt install python3-venv"
    fi

    say "Installing $package"
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$package" \
        || die "pip could not install $package."

    mkdir -p "$bin_dir"
    ln -sf "$venv/bin/shtick" "$bin_dir/shtick"
    installed=$("$bin_dir/shtick" --version) || die "shtick was installed but does not run: $bin_dir/shtick --version failed."
    say "Installed $installed to $bin_dir/shtick"

    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) path_hint ;;
    esac

    if ! command -v shellcheck >/dev/null 2>&1; then
        say "Optional: install shellcheck for live linting (brew install shellcheck, apt install shellcheck)."
    fi
    say "Run: shtick"
}

usage() {
    cat <<'EOF'
Install shtick, a playground for writing and testing shell scripts.

usage: install.sh [VERSION | latest] [--uninstall]

  VERSION        install this release (e.g. 0.1.1); default: the latest
  --uninstall    remove shtick's virtual environment and the `shtick` link

Environment: SHTICK_VENV, SHTICK_BIN_DIR, SHTICK_PYTHON, SHTICK_PACKAGE (see the top of this script).
EOF
}

# Prints the first Python 3.10+ it finds, preferring SHTICK_PYTHON and then the newest version.
find_python() {
    for candidate in ${SHTICK_PYTHON:-} python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

do_uninstall() {
    link="$bin_dir/shtick"
    # Only remove the link if it is ours, so a shtick installed another way is left alone.
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$venv/bin/shtick" ]; then
        rm -f "$link"
        say "Removed $link"
    fi
    if [ -d "$venv" ]; then
        rm -rf "$venv"
        say "Removed $venv"
    else
        say "Nothing to remove in $venv"
    fi
    say "Your config and history were kept (~/.config/shtick, ~/.local/share/shtick)."
}

# The ~ in the file names is shown to the user, not expanded.
# shellcheck disable=SC2088
path_hint() {
    case ${SHELL:-} in
        */zsh) rc='~/.zshrc' line="export PATH=\"$bin_dir:\$PATH\"" ;;
        */bash) rc='~/.bashrc' line="export PATH=\"$bin_dir:\$PATH\"" ;;
        */fish) rc='~/.config/fish/config.fish' line="fish_add_path $bin_dir" ;;
        *) rc="your shell's startup file" line="export PATH=\"$bin_dir:\$PATH\"" ;;
    esac
    say ""
    say "$bin_dir is not on your PATH. Add this line to $rc and open a new terminal:"
    say "  $line"
    say ""
}

say() {
    printf '%s\n' "$*"
}

die() {
    printf 'shtick install: %s\n' "$*" >&2
    exit 1
}

main "$@"
