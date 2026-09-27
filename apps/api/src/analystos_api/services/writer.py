"""Background writer for records that must persist independently of the request transaction.

Diagnostic events, failed/rejected query runs, AI usage rows and failed-login audit
entries must survive even when the surrounding request rolls back. Writing them from the
request thread in a second transaction would deadlock on SQLite while the request holds
its write lock, so they are queued to a single writer thread per database which commits
them in order (retrying briefly on lock contention). ``flush()`` waits until everything
queued so far is durable (used by read endpoints such as diagnostics, and by tests).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..logs import get_logger

log = get_logger("analystos.writer")

WriteFn = Callable[[Session], None]


class BackgroundWriter:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._queue: queue.Queue[WriteFn | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="aos-writer", daemon=True)
        self._thread.start()

    def submit(self, fn: WriteFn) -> None:
        self._queue.put(fn)

    def flush(self, timeout: float = 10.0) -> bool:
        """Block until all queued writes are committed (or ``timeout`` elapses)."""
        done = threading.Event()
        self._queue.put(lambda _s: done.set())
        return done.wait(timeout)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is None:
                self._queue.task_done()
                return
            for attempt in range(20):
                session = self._factory()
                try:
                    fn(session)
                    session.commit()
                    break
                except OperationalError:
                    session.rollback()
                    time.sleep(min(0.05 * (attempt + 1), 0.5))
                except Exception:  # noqa: BLE001 - a bad record must not stop the writer
                    session.rollback()
                    log.exception("background_write_failed")
                    break
                finally:
                    session.close()
            self._queue.task_done()


_writers: dict[int, BackgroundWriter] = {}
_guard = threading.Lock()


def writer_for(factory: sessionmaker[Session]) -> BackgroundWriter:
    with _guard:
        w = _writers.get(id(factory))
        if w is None:
            w = BackgroundWriter(factory)
            _writers[id(factory)] = w
        return w


def close_writer(factory: sessionmaker[Session]) -> None:
    with _guard:
        w = _writers.pop(id(factory), None)
    if w is not None:
        w.close()
