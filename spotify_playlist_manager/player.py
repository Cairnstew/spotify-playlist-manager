"""Live playback watcher helpers.

The Spotify Web API has no push/webhook for playback state, so "the currently
played song" is always a poll. :func:`watch_now_playing` is a tiny polling loop
that re-checks the current playback every ``interval`` seconds and yields a
:class:`~spotify_playlist_manager.models.NowPlaying` snapshot only when the
track changes (or when playback starts/stops). Good enough for a CLI status
line, a dashboard, or a "now playing" badge on a wall display.

    from spotify_playlist_manager import PlaylistManager
    from spotify_playlist_manager.player import watch_now_playing

    mgr = PlaylistManager.from_env()
    for now in watch_now_playing(mgr, interval=3):
        print(now.track)
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

from .client import PlaylistManager
from .logging_config import log_event
from .models import NowPlaying

_PLAYBACK_LOG = logging.getLogger("spotify_playlist_manager.playback")


def _playback_key(now: NowPlaying | None) -> dict:
    """Extract a state dict for change detection."""
    if now is None:
        return {
            "track_uri": None,
            "is_playing": False,
            "device_name": None,
            "shuffle": False,
            "repeat": "off",
        }
    return {
        "track_uri": now.track.uri or now.track.id or None,
        "is_playing": now.is_playing,
        "device_name": now.device_name,
        "shuffle": now.shuffle,
        "repeat": now.repeat,
    }


def _log_playback_change(prev: dict, curr: dict) -> None:
    """Log what changed between two playback state dicts."""
    if prev["track_uri"] is None and curr["track_uri"] is not None:
        log_event(
            _PLAYBACK_LOG,
            "playback.started",
            track_uri=curr["track_uri"],
            device_name=curr["device_name"],
        )
    elif prev["track_uri"] is not None and curr["track_uri"] is None:
        log_event(
            _PLAYBACK_LOG,
            "playback.stopped",
            last_track_uri=prev["track_uri"],
        )
    elif prev["track_uri"] != curr["track_uri"]:
        log_event(
            _PLAYBACK_LOG,
            "playback.track_changed",
            track_uri=curr["track_uri"],
            previous_track_uri=prev["track_uri"],
            device_name=curr["device_name"],
        )

    if prev["is_playing"] != curr["is_playing"]:
        event = "playback.resumed" if curr["is_playing"] else "playback.paused"
        log_event(
            _PLAYBACK_LOG,
            event,
            track_uri=curr["track_uri"],
            device_name=curr["device_name"],
        )

    if prev.get("device_name") != curr.get("device_name") and curr["track_uri"]:
        log_event(
            _PLAYBACK_LOG,
            "playback.device_changed",
            device_name=curr["device_name"],
            previous_device=prev.get("device_name"),
            track_uri=curr["track_uri"],
        )

    if prev.get("shuffle") != curr.get("shuffle"):
        log_event(
            _PLAYBACK_LOG,
            "playback.shuffle_changed",
            shuffle=curr["shuffle"],
            track_uri=curr["track_uri"],
        )

    if prev.get("repeat") != curr.get("repeat"):
        log_event(
            _PLAYBACK_LOG,
            "playback.repeat_changed",
            repeat=curr["repeat"],
            previous_repeat=prev.get("repeat"),
            track_uri=curr["track_uri"],
        )


def watch_now_playing(
    manager: PlaylistManager,
    interval: float = 5.0,
    emit_unchanged: bool = False,
) -> Iterator[NowPlaying | None]:
    """Poll playback state and yield every *change*.

    ``None`` is yielded when playback stops (nothing playing on any device).

    Parameters
    ----------
    interval:
        Seconds between API polls.
    emit_unchanged:
        If True, yield a snapshot on every poll rather than only on change.
        Useful to drive a progress indicator, at the cost of an API call and an
        output line per interval.
    """
    last_key: dict | None = None
    while True:
        now = manager.now_playing()
        key = _playback_key(now)

        if last_key is not None:
            _log_playback_change(last_key, key)

        if emit_unchanged or key != last_key:
            yield now
        last_key = key
        time.sleep(interval)


def blocks_until_change(
    manager: PlaylistManager,
    interval: float = 5.0,
    timeout: float | None = None,
) -> NowPlaying | None:
    """Block until the now-playing track changes, or ``timeout`` seconds pass.

    Returns the new snapshot (or ``None`` if playback stopped). Mainly useful
    for scripted tools that want to act on the *next* track.
    """
    start = time.monotonic()
    first = manager.now_playing()
    prev_key = _playback_key(first)

    while True:
        if timeout is not None and time.monotonic() - start >= timeout:
            return first
        time.sleep(interval)
        now = manager.now_playing()
        curr_key = _playback_key(now)
        if curr_key != prev_key:
            _log_playback_change(prev_key, curr_key)
            return now
