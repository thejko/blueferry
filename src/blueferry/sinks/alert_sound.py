"""Audible alert for an incoming message.

The notification spec makes sound optional, and Quickshell's server does not
advertise the `sound` capability, so `sound-name` and `sound-file` hints are
dropped without a word. The daemon therefore plays the alert itself.

Playback is fire-and-forget: a detached child, never waited on, and never
allowed to raise into the event loop. A missing player or a bad path means
silence, not a traceback.
"""
from __future__ import annotations

import logging
import shutil

# commands.run_command is the usual argv-only boundary, but it waits for
# the child. Playback must not block the event loop, so this module spawns
# directly. The argv is built here from a shutil.which path and a config
# value, never from message content, and no shell is involved.
import subprocess  # nosec B404
import time
from pathlib import Path

log = logging.getLogger(__name__)

# A burst of messages should announce itself once, not once per message.
_MIN_INTERVAL_SECONDS = 2.0

# Where to look for a theme sound when libcanberra is not installed.
_SOUND_THEME_DIRS = ("/usr/share/sounds/freedesktop/stereo",)

# Players that take a path. canberra-gtk-play is preferred for theme names
# because it honors the user's sound theme.
_FILE_PLAYERS = ("paplay", "pw-play", "aplay")


class AlertSound:
    """Plays one short sound, rate-limited, without blocking the caller."""

    def __init__(self, sound: str, *, min_interval: float = _MIN_INTERVAL_SECONDS) -> None:
        self._command = _build_command(sound)
        self._min_interval = min_interval
        self._last_played = 0.0
        self._child: subprocess.Popen[bytes] | None = None
        if sound and self._command is None:
            log.info("no way to play notification sound %r; staying silent", sound)

    @property
    def enabled(self) -> bool:
        return self._command is not None

    def play(self) -> None:
        if self._command is None:
            return

        now = time.monotonic()
        if now - self._last_played < self._min_interval:
            return
        self._last_played = now

        # Reap the previous child before spawning another. Throttling means at
        # most one can be outstanding, so this keeps zombies from collecting.
        if self._child is not None:
            self._child.poll()

        try:
            self._child = subprocess.Popen(  # nosec B603
                self._command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            log.debug("could not play notification sound", exc_info=True)


def _build_command(sound: str) -> list[str] | None:
    """Resolve a theme name or file path to an argv, or None if unplayable."""
    if not sound:
        return None

    if sound.startswith("/"):
        return _file_command(Path(sound))

    canberra = shutil.which("canberra-gtk-play")
    if canberra is not None:
        return [canberra, "-i", sound]

    for directory in _SOUND_THEME_DIRS:
        candidate = Path(directory) / f"{sound}.oga"
        if candidate.is_file():
            return _file_command(candidate)

    return None


def _file_command(path: Path) -> list[str] | None:
    if not path.is_file():
        log.info("notification sound %s does not exist", path)
        return None
    for player in _FILE_PLAYERS:
        binary = shutil.which(player)
        if binary is not None:
            return [binary, str(path)]
    return None
