"""Spell-checked message entry.

libspelling attaches to a GtkSourceView buffer rather than a plain
GtkTextBuffer, so composers build a GtkSource.View. It subclasses
Gtk.TextView, so callers keep the same buffer, controller, and margin API.

Everything here degrades: without libspelling, its typelib, or an installed
dictionary the caller still gets a working editor, just an unchecked one.
"""
from __future__ import annotations

import logging
from typing import Any

from gi.repository import Gtk

log = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("GtkSource", "5")
    gi.require_version("Spelling", "1")
    from gi.repository import GtkSource, Spelling
except (ImportError, ValueError):
    GtkSource = None
    Spelling = None


def build_message_view(**properties: Any) -> Gtk.TextView:
    """Return a message editor, spell-checked where libspelling is present."""
    if GtkSource is None or Spelling is None:
        log.debug("libspelling unavailable; composing without spell check")
        return Gtk.TextView(**properties)

    view = GtkSource.View(**properties)
    _attach_spelling(view)
    return view


def _attach_spelling(view: Gtk.TextView) -> None:
    """Wire the buffer to the default checker and expose its context menu."""
    try:
        adapter = Spelling.TextBufferAdapter.new(
            view.get_buffer(), Spelling.Checker.get_default()
        )
        adapter.set_enabled(True)
        # The menu carries the suggestions and the add-to-dictionary entries;
        # the action group is what those menu items actually invoke.
        view.set_extra_menu(adapter.get_menu_model())
        view.insert_action_group("spelling", adapter)
    except Exception:
        log.debug("could not attach spell checking", exc_info=True)
