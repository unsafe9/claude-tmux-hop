"""PTY proxy that answers Claude's OSC 7501 probe before tmux can.

Claude writes the probe and CSI c in one burst. tmux answers CSI c with DA1
immediately, and Claude treats that DA1 as lack of program-status support
unless the OSC reply is already queued on input. A pane pipe observes the
probe only after that DA1, so the reply is written on the inner PTY first.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty
from collections.abc import Callable

from .log import log_debug, log_error, log_info
from .psp import FullReset, Ledger, Parser, Query, Report, ShellPrompt, Snapshot, capability_reply

_REPLY_GAP = 0.05
_SELECT_TIMEOUT = 0.2


def _write_all(fd: int, data: bytes) -> bool:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        except OSError:
            return False
        if written <= 0:
            return False
        view = view[written:]
    return True


def _copy_winsize(src: int, dst: int) -> None:
    blank = struct.pack("HHHH", 0, 0, 0, 0)
    try:
        size = fcntl.ioctl(src, termios.TIOCGWINSZ, blank)
        fcntl.ioctl(dst, termios.TIOCSWINSZ, size)
    except OSError:
        return


def run_proxy(argv: list[str], on_status: Callable[[Snapshot], None]) -> int:
    """Run argv on a new PTY. Outside tmux, or without a tty, exec it directly."""
    if not argv:
        return 2
    if not os.environ.get("TMUX") or not sys.stdin.isatty() or not sys.stdout.isatty():
        os.execvp(argv[0], argv)

    pid, child = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError:
            os._exit(127)

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old = termios.tcgetattr(stdin_fd)
    winch = False

    def _on_winch(_signum: int, _frame: object) -> None:
        nonlocal winch
        winch = True

    signal.signal(signal.SIGWINCH, _on_winch)
    parser = Parser()
    ledger = Ledger()
    published: Snapshot | None = None
    last_reply = 0.0

    def publish(event: Report | ShellPrompt | FullReset) -> None:
        nonlocal published
        ledger.apply(event)
        current = ledger.snapshot()
        if current == published:
            return
        if published is None and current.state == "":
            published = current
            return
        published = current
        if isinstance(event, Report):
            log_info(
                f"psp report state={event.state} id={event.id or '-'} kind={event.kind or '-'}"
            )
        try:
            on_status(current)
        except Exception as exc:
            log_error(f"psp: status update failed: {exc}")

    try:
        tty.setraw(stdin_fd)
        _copy_winsize(stdout_fd, child)
        try:
            os.kill(pid, signal.SIGWINCH)
        except OSError:
            pass
        while True:
            if winch:
                winch = False
                _copy_winsize(stdout_fd, child)
                try:
                    os.kill(pid, signal.SIGWINCH)
                except OSError:
                    pass
            try:
                readable, _, _ = select.select([stdin_fd, child], [], [], _SELECT_TIMEOUT)
            except InterruptedError:
                continue
            if child in readable:
                try:
                    data = os.read(child, 65536)
                except OSError:
                    break
                if not data:
                    break
                events = parser.feed(data)
                query = next((event for event in events if isinstance(event, Query)), None)
                now = time.monotonic()
                if query is not None and now - last_reply >= _REPLY_GAP:
                    if _write_all(child, capability_reply(query.terminator)):
                        last_reply = now
                        log_debug("psp: answered capability probe")
                if not _write_all(stdout_fd, data):
                    break
                for event in events:
                    if isinstance(event, (Report, ShellPrompt, FullReset)):
                        publish(event)
            if stdin_fd in readable:
                try:
                    data = os.read(stdin_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                if not _write_all(child, data):
                    break
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        try:
            os.close(child)
        except OSError:
            pass

    try:
        _pid, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return 0
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 0
