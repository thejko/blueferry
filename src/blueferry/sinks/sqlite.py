"""Persistent event sink backed by the private SQLite history store."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from blueferry.ancs.constants import MESSAGES_APP_ID
from blueferry.events import SmsEvent
from blueferry.history import append_event, minimize_ancs_history, prune_events

if TYPE_CHECKING:
    from blueferry.storage_security import StorageSecurity

log = logging.getLogger(__name__)

# A keyring locked at daemon start is the common case on an autologin desktop,
# where no password is ever typed for PAM to unlock it with. Retry quietly
# rather than giving up for the life of the process.
RETRY_SECONDS = 60.0

# Messages that arrive while storage is locked are held here. The cap bounds
# the memory a long outage can cost; the oldest go first, and loudly.
MAX_BUFFERED_EVENTS = 500


class SqliteSink:
    name = "sqlite"

    # Class-level defaults so an instance built with ``__new__`` in a test
    # still has every attribute the write path reads.
    path: Path | None = None
    storage: StorageSecurity | None = None
    _writes_since_prune = 0
    _prepared = False
    _buffer: list[dict] | None = None
    _next_retry = 0.0
    _warned_unavailable = False

    def __init__(
        self,
        path: Path | None = None,
        *,
        storage: StorageSecurity | None = None,
    ) -> None:
        self.path = path
        self.storage = storage
        self._writes_since_prune = 0
        self._prepared = False
        self._buffer = []
        self._next_retry = 0.0
        self._warned_unavailable = False
        if storage is not None and not storage.status.can_write:
            log.warning(
                "SQLite history sink idle, messages are NOT being retained: %s",
                storage.status.detail,
            )
            self._next_retry = monotonic() + RETRY_SECONDS
            return
        self._prepare()

    def _prepare(self) -> bool:
        """Run the one-time maintenance pass. Return whether the store opened."""
        try:
            discarded, minimized = minimize_ancs_history(
                path=self.path, storage=self.storage
            )
            if discarded or minimized:
                log.info(
                    "minimized ANCS history (discarded=%d, compacted=%d)",
                    discarded,
                    minimized,
                )
            removed = prune_events(path=self.path, storage=self.storage)
            if removed:
                log.info("pruned %d expired history events", removed)
        except (OSError, TypeError, ValueError, sqlite3.Error):
            log.exception("SQLite history maintenance failed")
            return False
        self._prepared = True
        log.info("SQLite history sink ready")
        return True

    def handle(self, event: SmsEvent) -> None:
        self._append(event.to_dict())

    def handle_ancs(self, event) -> None:
        if event.app_id != MESSAGES_APP_ID:
            return
        self._append(event.correlation_dict())

    def _writable(self) -> bool:
        """Report whether a write can proceed, re-checking a locked keyring.

        ``StorageSecurity`` caches the key it acquired at startup, so a keyring
        unlocked afterwards is invisible until something calls ``refresh``.
        Nothing else does on the write path, which is why a single locked
        moment at login used to disable retention until the daemon restarted.
        """
        if self.storage is None or self.storage.status.can_write:
            return True
        now = monotonic()
        if now < self._next_retry:
            return False
        self._next_retry = now + RETRY_SECONDS
        if not self.storage.refresh(allow_prompt=False).can_write:
            return False
        log.info("encrypted storage recovered: %s", self.storage.status.detail)
        self._warned_unavailable = False
        return True

    def _hold(self, payload: dict) -> None:
        """Buffer an event so a later unlock still retains it."""
        if self._buffer is None:
            self._buffer = []
        self._buffer.append(payload)
        overflow = len(self._buffer) - MAX_BUFFERED_EVENTS
        if overflow > 0:
            del self._buffer[:overflow]
            log.warning(
                "history buffer full; %d unretained event(s) discarded", overflow
            )
        if not self._warned_unavailable:
            detail = self.storage.status.detail if self.storage is not None else ""
            log.warning(
                "not retaining history, holding messages in memory: %s", detail
            )
            self._warned_unavailable = True

    def _append(self, payload: dict) -> None:
        if not self._writable():
            self._hold(payload)
            return
        if not self._prepared and not self._prepare():
            self._hold(payload)
            return
        held = self._buffer or []
        self._buffer = []
        if held:
            log.info("flushing %d buffered history event(s)", len(held))
        for event in held:
            self._write(event)
        self._write(payload)

    def _write(self, payload: dict) -> None:
        try:
            append_event(payload, path=self.path, storage=self.storage)
            self._writes_since_prune += 1
            if self._writes_since_prune >= 10:
                self._writes_since_prune = 0
                removed = prune_events(path=self.path, storage=self.storage)
                if removed:
                    log.info("pruned %d expired history events", removed)
        except (OSError, TypeError, ValueError, sqlite3.Error):
            log.exception("SQLite history write failed")
