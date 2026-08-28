"""A keyring locked at startup must not disable retention for good."""
from __future__ import annotations

import pytest

from blueferry import config
from blueferry.history import read_events
from blueferry.sinks import sqlite as sqlite_sink
from blueferry.sinks.sqlite import SqliteSink
from blueferry.storage_security import PLAINTEXT_STORAGE, StorageStatus


class FakeStorage:
    """Stand in for StorageSecurity with a keyring the test can unlock."""

    def __init__(self, *, locked: bool) -> None:
        self.locked = locked
        self.refreshes = 0

    @property
    def status(self) -> StorageStatus:
        if self.locked:
            return StorageStatus(
                PLAINTEXT_STORAGE, "locked", "the desktop keyring is locked"
            )
        return StorageStatus(PLAINTEXT_STORAGE, "ready", "retained without encryption")

    def refresh(self, *, allow_prompt: bool) -> StorageStatus:
        self.refreshes += 1
        return self.status

    # History helpers serialize through the storage object whatever the
    # policy, so mirror the real plaintext passthrough.
    def encrypt(self, plaintext: str, *, purpose: str) -> str:
        return plaintext

    def decrypt(self, payload: str, *, purpose: str) -> str:
        return payload


@pytest.fixture
def events_db(tmp_path, monkeypatch):
    state_dir = tmp_path / "blueferry"
    events = state_dir / "events.sqlite"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "EVENTS_DB", events)
    monkeypatch.setattr(config, "CONTACTS_DB", state_dir / "contacts.sqlite")
    return events


def test_locked_keyring_holds_messages_then_flushes_on_unlock(events_db) -> None:
    storage = FakeStorage(locked=True)
    sink = SqliteSink(path=events_db, storage=storage)

    sink._append({"kind": "sms_received", "body": "arrived while locked"})
    assert read_events(path=events_db, storage=storage) == []

    storage.locked = False
    sink._next_retry = 0.0
    sink._append({"kind": "sms_received", "body": "arrived after unlock"})

    bodies = [event["body"] for event in read_events(path=events_db, storage=storage)]
    assert bodies == ["arrived while locked", "arrived after unlock"]


def test_locked_keyring_is_rechecked_rather_than_latched(events_db) -> None:
    storage = FakeStorage(locked=True)
    sink = SqliteSink(path=events_db, storage=storage)

    sink._append({"kind": "sms_received", "body": "first"})
    assert storage.refreshes == 0, "the retry window has not elapsed yet"

    sink._next_retry = 0.0
    sink._append({"kind": "sms_received", "body": "second"})
    assert storage.refreshes == 1


def test_buffer_discards_oldest_and_keeps_the_cap(events_db, monkeypatch) -> None:
    monkeypatch.setattr(sqlite_sink, "MAX_BUFFERED_EVENTS", 2)
    storage = FakeStorage(locked=True)
    sink = SqliteSink(path=events_db, storage=storage)

    for index in range(4):
        sink._next_retry = float("inf")
        sink._append({"kind": "sms_received", "body": str(index)})

    assert [event["body"] for event in sink._buffer] == ["2", "3"]
