"""Standard Python logging, optionally sent to the fd-log-run collector."""

try:
    import fcntl
except ImportError:
    fcntl = None
import logging
import os
import stat
import struct
import threading

__all__ = ["Handler"]

_transport_lock = threading.Lock()
_broken_pipes: set[tuple[int, int]] = set()


class Handler(logging.StreamHandler):
    """Use the inherited collector pipe, or ordinary StreamHandler output.

    Install with logging.basicConfig(handlers=[Handler()], level=logging.INFO).
    The descriptor is borrowed: close() never closes it. Configure handlers
    before launching threads; unrelated exec'd children do not inherit the pipe.
    """

    def __init__(self, stream=None):
        super().__init__(stream)
        self._fd = None
        self._pipe = None
        self._pid = os.getpid()
        if fcntl is None:
            return
        value = os.environ.get("ZIG_LOG_FD", "")
        if not value or not value.isascii() or not value.isdecimal():
            return
        try:
            fd = int(value)
            if fd <= 2:
                return
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            if (flags & os.O_ACCMODE) == os.O_RDONLY or flags & os.O_NONBLOCK:
                return
            st = os.fstat(fd)
            if not stat.S_ISFIFO(st.st_mode):
                return
            os.set_inheritable(fd, False)
            self._fd = fd
            self._pipe = (st.st_dev, st.st_ino)
        except (OSError, ValueError, OverflowError):
            pass

    def emit(self, record):
        if self._fd is None or os.getpid() != self._pid:
            return super().emit(record)
        try:
            message = (self.format(record) + self.terminator).encode("utf-8", "backslashreplace")
            scope = ("" if record.name == "root" else record.name).encode("utf-8", "backslashreplace")
            level = (0 if record.levelno >= logging.ERROR else
                     1 if record.levelno >= logging.WARNING else
                     2 if record.levelno >= logging.INFO else 3)
            frame = memoryview(struct.pack("<BIQ", level, len(scope), len(message))
                               + scope + message)
            with _transport_lock:
                if self._fd is not None and self._pipe not in _broken_pipes:
                    written = 0
                    try:
                        while frame:
                            count = os.write(self._fd, frame)
                            if count <= 0:
                                raise OSError("short write")
                            written += count
                            frame = frame[count:]
                        return
                    except OSError:
                        _broken_pipes.add(self._pipe)
                        self._fd = None
                    except BaseException:
                        if written:
                            _broken_pipes.add(self._pipe)
                            self._fd = None
                        raise
                else:
                    self._fd = None
        except Exception:
            self.handleError(record)
            return
        super().emit(record)
