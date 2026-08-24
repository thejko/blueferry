"""Desktop popup policy tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from blueferry.sinks import libnotify as libnotify_mod
from blueferry.sinks.libnotify import (
    _ANCS_EXPIRE_MS,
    _MESSAGE_EXPIRE_MS,
    LibnotifySink,
)


class _FakeNotifications:
    def __init__(self) -> None:
        self.calls = []

    def Notify(self, *args):
        self.calls.append(args)
        return 1

    def CloseNotification(self, notification_id):
        self.calls.append(("close", int(notification_id)))


def test_ancs_popup_is_transient_and_expires(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "blueferry.sinks.libnotify.config.SHOW_NOTIFICATION_CONTENT", True
    )
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notification_policy = lambda: "all"
    sink._notif = _FakeNotifications()
    event = SimpleNamespace(
        app_name="Settings",
        app_id="com.apple.Preferences",
        title="System message",
        body="Something happened",
    )

    sink.handle_ancs(event)

    assert len(sink._notif.calls) == 1
    assert bool(sink._notif.calls[0][-2]["transient"]) is True
    assert int(sink._notif.calls[0][-1]) == _ANCS_EXPIRE_MS


def test_sms_and_imessage_popup_also_expires(monkeypatch) -> None:
    monkeypatch.setattr(
        "blueferry.sinks.libnotify.config.SHOW_NOTIFICATION_CONTENT", True
    )
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notif = _FakeNotifications()
    sink._pending = {}
    sink._msg_subs = {}
    event = SimpleNamespace(
        kind="sms_received",
        display_sender="Alice",
        body="Hello",
        message_path=None,
    )

    sink.handle(event)

    assert len(sink._notif.calls) == 1
    assert list(sink._notif.calls[0][5]) == []
    assert int(sink._notif.calls[0][-1]) == _MESSAGE_EXPIRE_MS


def test_clicking_message_popup_requests_opaque_message_handle(monkeypatch) -> None:
    monkeypatch.setattr(
        "blueferry.sinks.libnotify.config.SHOW_NOTIFICATION_CONTENT", True
    )
    opened = []
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notif = _FakeNotifications()
    sink._pending = {}
    sink._open_messages = {}
    sink._msg_subs = {}
    sink._on_open_message = opened.append
    event = SimpleNamespace(
        kind="sms_received",
        handle="message-opaque-42",
        display_sender="Alice",
        body="Hello",
        message_path=None,
    )

    sink.handle(event)

    assert list(sink._notif.calls[0][5]) == ["default", "Open conversation"]
    sink._on_action(1, "default")
    assert opened == ["message-opaque-42"]

    sink._on_closed(1, 1)
    sink._on_action(1, "default")
    assert opened == ["message-opaque-42"]


def test_remote_markup_is_escaped_before_notification(monkeypatch) -> None:
    monkeypatch.setattr(
        "blueferry.sinks.libnotify.config.SHOW_NOTIFICATION_CONTENT", True
    )
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notif = _FakeNotifications()
    sink._pending = {}
    sink._msg_subs = {}
    event = SimpleNamespace(
        kind="sms_received",
        display_sender="Alice & Bob",
        body="<b>not markup</b> & text",
        message_path=None,
    )

    sink.handle(event)

    call = sink._notif.calls[0]
    assert call[3] == "💬 Alice &amp; Bob"
    assert call[4] == "&lt;b&gt;not markup&lt;/b&gt; &amp; text"


def test_remote_notification_title_cannot_embed_controls_or_newlines(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "blueferry.sinks.libnotify.config.SHOW_NOTIFICATION_CONTENT", True
    )
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notif = _FakeNotifications()
    sink._pending = {}
    sink._msg_subs = {}
    event = SimpleNamespace(
        kind="sms_received",
        display_sender="Alice\nFake app\u202egnp",
        body="Hello",
        message_path=None,
    )

    sink.handle(event)

    assert sink._notif.calls[0][3] == "💬 Alice Fake app�gnp"


def test_messages_ancs_duplicate_is_suppressed_by_default(monkeypatch) -> None:
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notification_policy = lambda: "all"
    sink._notif = _FakeNotifications()
    event = SimpleNamespace(
        app_name="Messages",
        app_id="com.apple.MobileSMS",
        title="Alice",
        body="hello",
    )

    sink.handle_ancs(event)

    assert sink._notif.calls == []


def test_messages_only_suppresses_non_message_ancs_popup() -> None:
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notification_policy = lambda: "messages"
    sink._notif = _FakeNotifications()
    event = SimpleNamespace(
        app_name="Settings",
        app_id="com.apple.Preferences",
        title="System message",
        body="Something happened",
    )

    sink.handle_ancs(event)

    assert sink._notif.calls == []


def test_none_suppresses_message_popup() -> None:
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notification_policy = lambda: "none"
    sink._notif = _FakeNotifications()
    event = SimpleNamespace(
        kind="sms_received",
        display_sender="Alice",
        body="Hello",
        message_path=None,
    )

    sink.handle(event)

    assert sink._notif.calls == []


def test_read_state_trackers_are_bounded(monkeypatch) -> None:
    removed = []
    subscription = SimpleNamespace(remove=lambda: removed.append(True))
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._notif = _FakeNotifications()
    sink._pending = {1: "/message/1", 2: "/message/2"}
    sink._msg_subs = {1: subscription, 2: subscription}
    monkeypatch.setattr(libnotify_mod, "MAX_DESKTOP_MESSAGE_TRACKERS", 1)

    sink._prune_trackers()

    assert sink._pending == {2: "/message/2"}
    assert set(sink._msg_subs) == {2}
    assert removed == [True]
    assert sink._notif.calls == [("close", 1)]


def test_deliberate_dismissal_queues_the_correct_mark_read(monkeypatch) -> None:
    queued = []
    marked = []
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._pending = {7: "/session/message1"}
    sink._msg_subs = {}
    sink._submit_obex = lambda operation, **callbacks: queued.append(
        (operation, callbacks)
    )
    monkeypatch.setattr(
        libnotify_mod, "_mark_message_read", marked.append
    )

    sink._on_closed(7, 2)

    assert sink._pending == {}
    assert len(queued) == 1
    queued[0][0]()
    assert marked == ["/session/message1"]


@pytest.mark.parametrize("reason", [1, 3])
def test_expiry_and_phone_read_do_not_write_read_state(reason) -> None:
    queued = []
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._pending = {7: "/session/message1"}
    sink._msg_subs = {}
    sink._submit_obex = lambda operation, **callbacks: queued.append(
        (operation, callbacks)
    )

    sink._on_closed(7, reason)

    assert queued == []


class _FakeSessionBus:
    """Records what the sink asks of the session bus."""

    def __init__(self) -> None:
        self.get_object_calls = []
        self.signal_receivers = []

    def get_object(self, bus_name, path, **kwargs):
        self.get_object_calls.append((bus_name, path, kwargs))
        return _FakeProxy()

    def add_signal_receiver(self, handler, **kwargs):
        self.signal_receivers.append((handler, kwargs))
        return SimpleNamespace(remove=lambda: None)


class _FakeProxy:
    def connect_to_signal(self, *args, **kwargs):
        return SimpleNamespace(remove=lambda: None)


def test_sink_addresses_the_notification_server_by_well_known_name(
    monkeypatch,
) -> None:
    """A restarted server must not strand the sink on a dead unique name."""
    bus = _FakeSessionBus()
    monkeypatch.setattr(libnotify_mod, "get_session_bus", lambda: bus)

    LibnotifySink(submit_obex=lambda *a, **k: None)

    name, path, kwargs = bus.get_object_calls[0]
    assert name == "org.freedesktop.Notifications"
    assert path == "/org/freedesktop/Notifications"
    assert kwargs["follow_name_owner_changes"] is True

    watched = [
        kw for _handler, kw in bus.signal_receivers
        if kw.get("signal_name") == "NameOwnerChanged"
    ]
    assert watched and watched[0]["arg0"] == "org.freedesktop.Notifications"


def test_replacing_the_server_drops_stale_popup_trackers() -> None:
    removed = []
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._pending = {1: "/msg/one"}
    sink._open_messages = {1: "handle-one"}
    sink._msg_subs = {1: SimpleNamespace(remove=lambda: removed.append(1))}

    sink._on_server_replaced("org.freedesktop.Notifications", ":1.7", ":1.9")

    assert sink._pending == {}
    assert sink._open_messages == {}
    assert sink._msg_subs == {}
    assert removed == [1]


def test_first_appearance_of_the_server_keeps_trackers() -> None:
    sink = LibnotifySink.__new__(LibnotifySink)
    sink._pending = {1: "/msg/one"}
    sink._open_messages = {1: "handle-one"}
    sink._msg_subs = {}

    sink._on_server_replaced("org.freedesktop.Notifications", "", ":1.9")

    assert sink._pending == {1: "/msg/one"}
    assert sink._open_messages == {1: "handle-one"}
