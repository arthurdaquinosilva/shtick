"""The session engine: one long-lived shell process that runs cells and reports how they ended.

How a cell runs
---------------
The shell is started non-interactively (`<shell> -s`) in its own process group and reads
commands from its stdin. Each cell is written to a file and the engine sends one driver line:

    { printf 'TOKEN\\0started\\0' >STATUS; __shtick_status N; . CELL; } <STDIN; printf 'TOKEN\\0%s\\0%s\\0' "$?" "$PWD" >STATUS

* `__shtick_status N` makes `$?` inside the cell equal the previous cell's exit status.
* stdout and stderr are pipes, read as they arrive; the exit status and `$PWD` come back on a
  named FIFO tagged with a per-cell random token, so no bookkeeping shows up in the output.
* The cell's stdin is a FIFO of its own. The engine keeps it open until the shell reports
  "started" (it then holds the read end), so closing it delivers EOF to the cell only.
* Ctrl+C sends SIGINT to the process group; `trap 'return 130' INT` returns from the sourced
  cell, so the rest of the cell is skipped and the session survives. bash can be left unable to
  run the trap from builtin-only loops after a trap returned while it waited for a child; a
  fork (bash 5) and re-setting the trap (bash 3.2) before the next cell reset that. A second interrupt of the same
  cell kills the session and restarts it.
* The code is checked with `<shell> -n` first: dash exits when a sourced file has a syntax error.
* If the shell exits (`exit`, `exec`, `set -e`, a crash), the cell reports `died` and the session
  restarts in the last known directory — with the loss of state made explicit.
"""

from __future__ import annotations

import codecs
import errno
import os
import pty
import secrets
import selectors
import shutil
import signal
import subprocess
import tempfile
import termios
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

SHELLS = ("bash", "sh", "dash", "zsh")
INTERRUPT_STATUS = 130

OutputCallback = Callable[[str, str], None]  # (stream: "out" | "err", text)
# Terminal bytes → (bytes to give the cell, end its stdin?). Lets the UI do its own line editing.
InputCallback = Callable[[bytes], "tuple[bytes, bool]"]


class ShellNotFound(Exception):
    pass


def quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def resolve_shell(name: str) -> str:
    """A shell name (`bash`) or path (`/bin/bash`) → absolute path to the executable."""
    path = shutil.which(os.path.expanduser(name))
    if path is None:
        raise ShellNotFound(f"shell not found: {name}")
    return os.path.abspath(path)


def shell_kind(path: str) -> str:
    """bash · zsh · dash · sh — the dialect, judged by the running shell rather than the file name."""
    return shell_info(path)[0]


@lru_cache(maxsize=None)
def shell_info(path: str) -> tuple[str, str]:
    """(kind, version) for a shell executable."""
    probe = 'if [ -n "$BASH_VERSION" ]; then echo "bash $BASH_VERSION"; elif [ -n "$ZSH_VERSION" ]; then echo "zsh $ZSH_VERSION"; else echo other; fi'
    try:
        out = subprocess.run([path, "-c", probe], capture_output=True, text=True, timeout=5, env=clean_env()).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        out = []
    if len(out) >= 2:
        kind, version = out[0], out[1]
        if kind == "bash":
            version = version.split("(")[0]
        return kind, version
    base = os.path.basename(path)
    return ("dash" if "dash" in base else "sh"), ""


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if env is None else env)
    # Startup files for non-interactive shells would run inside the session.
    for name in ("BASH_ENV", "ENV"):
        env.pop(name, None)
    return env


def shell_argv(path: str) -> list[str]:
    kind = shell_kind(path)
    if kind == "bash":
        return [path, "--noprofile", "--norc", "-s"]
    if kind == "zsh":
        return [path, "-f", "-s"]
    return [path, "-s"]


INT_TRAP = "trap 'return 130 2>/dev/null # shtick' INT"
BOOT = {
    "common": f"{INT_TRAP}\n__shtick_status() {{ return \"$1\"; }}\n",
    # After a trap returned from an interrupted cell, bash 5 won't run the trap from builtin-only
    # loops until it forks again, and bash 3.2 until the trap is set again. Reinstall ours only.
    "bash": (
        "shopt -s expand_aliases\n"
        f"__shtick_reset_int() {{ case \"$(trap -p INT)\" in *'# shtick'*) trap - INT; {INT_TRAP};; esac; }}\n"
    ),
    "zsh": "",
    "dash": "",
    "sh": "",
}


@dataclass
class CellResult:
    code: str
    exit: int | None = None  # None when the cell didn't finish normally (died / syntax error)
    cwd: str = ""
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    interrupted: bool = False
    died: bool = False  # the shell process ended; the session was restarted
    died_status: int | None = None  # the shell's exit status (negative: killed by that signal)
    syntax_error: str | None = None
    number: int = 0
    events: list[tuple[str, str]] = field(default_factory=list)  # (stream, text) in arrival order

    @property
    def ok(self) -> bool:
        return self.exit == 0

    @property
    def status(self) -> int:
        """A single number for the cell's outcome, like `$?` after it would be."""
        if self.exit is not None:
            return self.exit
        if self.syntax_error is not None:
            return 2
        if self.died_status is not None:
            return self.died_status if self.died_status >= 0 else 128 - self.died_status
        return 1


class Session:
    """A persistent shell. Not thread-safe except `interrupt`, `send_input` and `close_input`."""

    def __init__(self, shell: str = "bash", cwd: str | None = None, env: dict[str, str] | None = None, tty: bool = False):
        self.path = resolve_shell(shell)
        self.name = shell
        self.kind, self.version = shell_info(self.path)
        self.tty = tty
        self.env = clean_env(env)
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.dir = Path(tempfile.mkdtemp(prefix="shtick-"))
        self.status_path = self.dir / "status"
        self.stdin_path = self.dir / "stdin"
        os.mkfifo(self.status_path, 0o600)
        os.mkfifo(self.stdin_path, 0o600)
        self.count = 0  # cells run, including internal ones
        self.last_status = 0
        self.restarts = 0
        self.proc: subprocess.Popen | None = None
        self._status_fd = -1
        self._status_keep = -1
        self._stdin_fd: int | None = None
        self._pty_master: int | None = None
        self._pty_slave_name = ""
        self._pty_slave: int | None = None
        self._running = False
        self._interrupts = 0
        self._after_interrupt = False
        self._lock = threading.RLock()  # one cell at a time (completion queries run from another thread)
        self._start()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def _start(self) -> None:
        if self._status_fd < 0:
            self._status_fd = os.open(self.status_path, os.O_RDONLY | os.O_NONBLOCK)
            self._status_keep = os.open(self.status_path, os.O_WRONLY)  # the FIFO never reports EOF
        cwd = self.cwd if os.path.isdir(self.cwd) else os.path.expanduser("~")
        self.proc = subprocess.Popen(
            shell_argv(self.path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=self.env,
            start_new_session=True,
            bufsize=0,
        )
        self._write(BOOT["common"] + BOOT.get(self.kind, ""))
        self._after_interrupt = False

    def restart(self) -> None:
        self._kill()
        self.restarts += 1
        self.last_status = 0
        self._start()

    def _kill(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for f in (proc.stdin, proc.stdout, proc.stderr):
            try:
                f.close()  # type: ignore[union-attr]
            except OSError:
                pass
        self.proc = None

    def close(self) -> None:
        self._kill()
        for fd in (self._status_fd, self._status_keep, self._pty_master, self._pty_slave):
            if fd is not None and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._status_fd = self._status_keep = -1
        self._pty_master = self._pty_slave = None
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def label(self) -> str:
        return f"{self.kind} {self.version}".strip()

    def _write(self, text: str) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(text.encode())

    # ── tty mode ──────────────────────────────────────────────────────────

    def set_tty(self, on: bool) -> None:
        self.tty = on

    def _ensure_pty(self) -> tuple[int, str]:
        if self._pty_master is None:
            master, slave = pty.openpty()
            name = os.ttyname(slave)
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.ONLCR  # keep \n as \n
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self._pty_master, self._pty_slave_name = master, name
            self._pty_slave = slave  # stays open so the device survives between cells
        return self._pty_master, self._pty_slave_name

    # ── running cells ─────────────────────────────────────────────────────

    def check_syntax(self, code: str) -> str | None:
        """The shell's own `-n` verdict: None when the code parses, else the error message."""
        path = self.dir / "check.sh"
        path.write_text(code if code.endswith("\n") else code + "\n")
        env = {**self.env, "LC_ALL": "C"}
        try:
            proc = subprocess.run([self.path, "-n", str(path)], capture_output=True, text=True, timeout=5, env=env)
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode == 0:
            return None
        return proc.stderr.strip().replace(str(path), "cell") or f"syntax error (exit {proc.returncode})"

    def cell_path(self, n: int) -> Path:
        return self.dir / f"cell-{n}.sh"

    def run(
        self,
        code: str,
        on_output: OutputCallback | None = None,
        stdin: str | bytes | None = None,
        input_fd: int | None = None,
        check: bool = True,
        internal: bool = False,
        on_input: InputCallback | None = None,
    ) -> CellResult:
        """Run `code` in the session.

        stdin: data given to the cell up front, followed by EOF. input_fd: a file descriptor
        (the terminal) whose data is forwarded to the cell while it runs; EOF on it closes the
        cell's stdin. With neither, the cell's stdin is empty. internal: don't let this cell
        change the `$?` seen by the next user cell."""
        with self._lock:
            return self._run(code, on_output, stdin, input_fd, check, internal, on_input)

    def _run(self, code, on_output, stdin, input_fd, check, internal, on_input) -> CellResult:
        self.count += 1
        n = self.count
        result = CellResult(code=code, number=n)
        if check and (error := self.check_syntax(code)) is not None:
            result.syntax_error = error
            return result
        if not self.alive:
            self.restart()
        path = self.cell_path(n)
        path.write_text(code if code.endswith("\n") else code + "\n")

        token = secrets.token_hex(8)
        tty = self.tty
        # O_RDWR: opening doesn't wait for a reader, and data written before the shell opens the
        # FIFO is kept. Closing it after "started" is what delivers EOF to the cell.
        cell_in = os.open(self.stdin_path, os.O_RDWR) if not tty else -1
        if tty:
            master, slave_name = self._ensure_pty()
            redirect = f"<{quote(slave_name)} >{quote(slave_name)} 2>&1"
        else:
            redirect = f"<{quote(str(self.stdin_path))}"
        status = quote(str(self.status_path))
        prefix = "__shtick_reset_int; " if self._after_interrupt and self.kind == "bash" else ""
        line = (
            f"{prefix}{{ command printf '{token}\\0started\\0' >{status}; __shtick_status {self.last_status}; . {quote(str(path))}; }} {redirect}; "
            f"command printf '{token}\\0%s\\0%s\\0' \"$?\" \"$PWD\" >{status}\n"
        )
        self._after_interrupt = False
        self._interrupts = 0

        start = time.perf_counter()
        try:
            self._write(line)
        except BrokenPipeError:
            self.restart()
            self._write(line)
        pending_input = stdin.encode() if isinstance(stdin, str) else stdin
        eof_after_started = stdin is not None or input_fd is None
        if pending_input:
            if tty:
                os.write(master, pending_input)
            else:
                _write_all(cell_in, pending_input)

        self._running = True
        try:
            self._pump(result, token, on_output, cell_in, input_fd, eof_after_started, on_input)
        finally:
            self._running = False
            result.interrupted = self._interrupts > 0
            if cell_in >= 0:
                try:
                    os.close(cell_in)
                except OSError:
                    pass
        result.duration = time.perf_counter() - start
        result.stdout = "".join(t for s, t in result.events if s == "out")
        result.stderr = "".join(t for s, t in result.events if s == "err")

        if result.died:
            self.restart()
        else:
            self.cwd = result.cwd or self.cwd
            if result.interrupted and result.exit == 0:
                result.exit = INTERRUPT_STATUS  # bash 3.2 ignores the trap's return value in loops
            if not internal:
                self.last_status = result.exit if result.exit is not None else 1
        path.unlink(missing_ok=True)
        return result

    def _pump(
        self,
        result: CellResult,
        token: str,
        on_output: OutputCallback | None,
        cell_in: int,
        input_fd: int | None,
        eof_after_started: bool,
        on_input: InputCallback | None,
    ) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None and proc.stderr is not None
        decoders = {"out": codecs.getincrementaldecoder("utf-8")("replace"), "err": codecs.getincrementaldecoder("utf-8")("replace")}
        rewrite = (str(self.dir) + "/", "")

        def emit(stream: str, data: bytes, final: bool = False) -> None:
            text = decoders[stream].decode(data, final)
            if self.tty:
                text = text.replace("\r\n", "\n")
            if rewrite[0] in text:
                text = text.replace(rewrite[0], rewrite[1])
            if text:
                result.events.append((stream, text))
                if on_output is not None:
                    on_output(stream, text)

        sel = selectors.DefaultSelector()
        streams = {proc.stdout.fileno(): "out", proc.stderr.fileno(): "err"}
        for fd in streams:
            sel.register(fd, selectors.EVENT_READ, "stream")
        if self.tty and self._pty_master is not None:
            streams[self._pty_master] = "out"
            sel.register(self._pty_master, selectors.EVENT_READ, "stream")
        sel.register(self._status_fd, selectors.EVENT_READ, "status")
        if input_fd is not None:
            sel.register(input_fd, selectors.EVENT_READ, "input")

        buf = b""
        started = False
        stdin_open = cell_in >= 0
        prefix = token.encode() + b"\0"
        try:
            while True:
                try:
                    ready = sel.select(timeout=0.1)
                except KeyboardInterrupt:
                    self.interrupt()
                    continue
                for key, _ in ready:
                    fd = key.fd
                    if key.data == "status":
                        try:
                            buf += os.read(fd, 4096)
                        except BlockingIOError:
                            continue
                        if not started and buf.startswith(prefix + b"started\0"):
                            started = True
                            buf = buf[len(prefix) + 8:]
                            if eof_after_started and stdin_open:
                                os.close(cell_in)
                                stdin_open = False
                        parts = buf.split(b"\0")
                        if started and len(parts) >= 4 and parts[0] == token.encode():
                            result.exit = int(parts[1] or 0)
                            result.cwd = parts[2].decode(errors="replace")
                            self._drain(streams, emit)
                            return
                        if not buf.startswith(prefix[: len(buf)]):
                            buf = b""  # a stale report (e.g. from an interrupted cell): drop it
                    elif key.data == "stream":
                        try:
                            data = os.read(fd, 65536)
                        except OSError as e:
                            if e.errno == errno.EIO:  # pty with no open slave
                                data = b""
                            else:
                                raise
                        if data:
                            emit(streams[fd], data)
                        else:
                            sel.unregister(fd)
                    elif key.data == "input":
                        try:
                            data = os.read(fd, 4096)
                        except OSError:
                            data = b""
                        if not data:
                            sel.unregister(fd)
                            forward, eof = b"", True
                        elif on_input is not None:
                            forward, eof = on_input(data)
                        else:
                            forward, eof = data, False
                        if self.tty and self._pty_master is not None:
                            if forward:
                                os.write(self._pty_master, forward)
                            if eof and not data:
                                os.write(self._pty_master, b"\x04")
                            continue
                        if forward and stdin_open:
                            _write_all(cell_in, forward)
                        if eof and stdin_open:
                            if started:
                                os.close(cell_in)
                                stdin_open = False
                            else:
                                eof_after_started = True
                if proc.poll() is not None:
                    self._drain(streams, emit)
                    result.died = True
                    result.died_status = proc.returncode
                    return
        finally:
            sel.close()
            for stream in ("out", "err"):
                emit(stream, b"", final=True)

    def _drain(self, streams: dict[int, str], emit: Callable[[str, bytes], None]) -> None:
        """Read what's already in the pipes: everything written before the status report."""
        for fd, stream in streams.items():
            blocking = os.get_blocking(fd)
            os.set_blocking(fd, False)
            try:
                while True:
                    data = os.read(fd, 65536)
                    if not data:
                        break
                    emit(stream, data)
            except OSError:
                pass
            finally:
                os.set_blocking(fd, blocking)

    def interrupt(self) -> None:
        """Ctrl+C: SIGINT the cell. Interrupting the same cell again kills and restarts the session."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self._interrupts += 1
        self._after_interrupt = True
        sig = signal.SIGINT if self._interrupts == 1 else signal.SIGKILL
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass

    # ── helpers for magics ────────────────────────────────────────────────

    def query(self, code: str) -> CellResult:
        """Run bookkeeping code (listing variables, …) without touching `$?` or showing output."""
        return self.run(code, check=False, internal=True)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except BlockingIOError:
            time.sleep(0.01)
            continue
        view = view[n:]


def available_shells() -> list[str]:
    return [s for s in SHELLS if shutil.which(s)]
