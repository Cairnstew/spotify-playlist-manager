"""Waybar custom-module support.

Emits `waybar JSON`_ (``{text, tooltip, class}``) for two widgets:

* **now** — the currently playing track, polled by waybar's ``interval``.
* **upvote** — a button showing the current track's vote count; clicking it
  (via ``on-click``) upvotes the song.

The functions in this module return plain dicts that ``json.dumps`` can
serialise directly; they never print. The CLI (``waybar now --json`` /
``waybar upvote``) is what talks to stdout, so an ``exec`` module can be
glued together without any shell quoting.

Waybar config sketch::

    "custom/spotify": {
        "exec": "spotify-playlist-manager waybar now --json",
        "interval": 5,
        "return-type": "json"
    },
    "custom/spotify-upvote": {
        "exec": "spotify-playlist-manager waybar upvote --json",
        "on-click": "spotify-playlist-manager waybar upvote",
        "interval": 5,
        "return-type": "json"
    }

CSS classes: ``playing`` / ``paused`` / ``stopped`` for the now widget, and
``upvoted`` / ``not-upvoted`` for the vote button.

.. _waybar JSON: https://github.com/Alexays/Waybar/wiki/Module:-Custom
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import PlaylistManager
from .models import NowPlaying, Track
from .votes import VoteStore

# Icons used by both widgets. ♥ doubles as "voted"; ♡ is the empty heart.
_PLAY_ICON = "▶"
_PAUSE_ICON = "⏸"
_SILENT_ICON = "♪"
_UPVOTED_ICON = "♥"
_NOT_UPVOTED_ICON = "♡"

# Upvote a button and a now-playing widget can share one poll by passing the
# same VoteStore instance; each gets its own output below.


def now_playing_output(now: NowPlaying | None) -> dict:
    """Waybar JSON for the currently playing track.

    ``text`` is a one-line ``▶/⏸ name — artists``; ``tooltip`` carries album,
    device and progress detail; ``class`` is ``playing``, ``paused`` or
    ``stopped`` (when nothing is playing).
    """
    if now is None:
        return {
            "text": _SILENT_ICON,
            "tooltip": "Nothing playing",
            "class": "stopped",
        }

    icon = _PLAY_ICON if now.is_playing else _PAUSE_ICON
    state = "playing" if now.is_playing else "paused"
    artists = ", ".join(now.track.artists)
    label = f"{now.track.name} — {artists}" if artists else now.track.name
    minutes, seconds = divmod(now.progress_ms // 1000, 60)
    tooltip_lines = [
        f"{state.title()} {label}",
        f"Artists: {artists}" if artists else "Artists: —",
        f"Album: {now.track.album}" if now.track.album else "Album: —",
        f"Device: {now.device_name or 'unknown'}",
        f"Progress: {minutes:02d}:{seconds:02d}",
    ]
    return {
        "text": f"{icon} {label}",
        "tooltip": "\n".join(tooltip_lines),
        "class": state,
    }


def upvote_output(votes: int, track: Track | None = None, playlist: str = "") -> dict:
    """Waybar JSON for the vote button.

    ``text`` is ``♥ N`` when the track has votes, ``♡`` otherwise. ``class``
    is ``upvoted`` / ``not-upvoted`` so the button can be styled by vote
    state. *track* / *playlist* only enrich the tooltip.
    """
    voted = votes > 0
    text = f"{_UPVOTED_ICON} {votes}" if voted else _NOT_UPVOTED_ICON
    tooltip_lines = []
    if track and track.name:
        artists = ", ".join(track.artists)
        label = f"{track.name} — {artists}" if artists else track.name
        tooltip_lines.append(f"Upvote: {label}")
    else:
        tooltip_lines.append("Upvote current track")
    tooltip_lines.append(f"{votes} vote{'s' if votes != 1 else ''}")
    if playlist:
        tooltip_lines.append(f"Playlist: {playlist}")
    return {
        "text": text,
        "tooltip": "\n".join(tooltip_lines),
        "class": "upvoted" if voted else "not-upvoted",
    }


def find_playlist_for_track(mgr: PlaylistManager, track_id: str) -> str:
    """Scan all playlists for the one containing *track_id*.

    Returns the playlist id, or ``""`` when the track is not in any of the
    user's playlists (a liked song, an album, a radio feed...). This is
    intentionally best-effort: each playlist is fetched in turn and failures
    are tolerated so a single odd playlist cannot break the button.
    """
    for pl in mgr.playlists():
        try:
            if any(t.id == track_id for t in mgr.tracks(pl.id)):
                return pl.id
        except Exception:
            continue
    return ""


@dataclass(slots=True)
class UpvoteResult:
    """Outcome of :func:`upvote_current_track`."""
    playlist: str
    track: Track
    votes: int


def current_vote_output(
    mgr: PlaylistManager,
    store: VoteStore,
    playlist: str = "",
) -> dict | None:
    """**Read-only** waybar JSON for the vote button.

    This is the ``exec``/poll path: it displays the current vote count
    without incrementing it. When *playlist* is empty the containing
    playlist is auto-detected from the now-playing snapshot (see
    :func:`find_playlist_for_track`). Returns ``None`` when nothing is
    playing — the CLI turns that into a zero-count button.
    """
    now = mgr.now_playing()
    if now is None:
        return None
    track = now.track
    if not playlist:
        playlist = find_playlist_for_track(mgr, track.id)
    votes = store.get_votes(playlist, track.id)
    return upvote_output(votes, track=track, playlist=playlist)


def upvote_current_track(
    mgr: PlaylistManager,
    store: VoteStore,
    playlist: str = "",
) -> UpvoteResult | None:
    """Upvote the currently playing track.

    This is the ``on-click`` path: it increments the vote count via
    :meth:`VoteStore.upvote`. When *playlist* is empty the containing
    playlist is auto-detected from the now-playing snapshot (see
    :func:`find_playlist_for_track`). Returns ``None`` when nothing is
    playing, otherwise an :class:`UpvoteResult`.
    """
    now = mgr.now_playing()
    if now is None:
        return None
    track = now.track
    if not playlist:
        playlist = find_playlist_for_track(mgr, track.id)
    votes = store.upvote(playlist, track.id, track)
    return UpvoteResult(playlist=playlist, track=track, votes=votes)