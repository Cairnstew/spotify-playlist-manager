"""Vote-based playlist ordering.

Tracks votes in a SQLite database keyed by ``(playlist_id, track_id)``.
The :func:`apply_votes` function reorders a Spotify playlist so that the
most-upvoted tracks appear first, using :meth:`PlaylistManager.move_to_top`.

Database location: ``~/.local/share/spotify-playlist-manager/votes.db``
(overridable via ``SPM_VOTES_DB`` env var or the ``db_path`` parameter).

Usage from Python::

    from spotify_playlist_manager import PlaylistManager
    from spotify_playlist_manager.votes import VoteStore, apply_votes

    mgr = PlaylistManager.from_env()
    store = VoteStore()

    # Upvote the current song
    np = mgr.now_playing()
    if np:
        # Try to find which playlist it belongs to
        playlist_id = _find_playlist_for_track(mgr, np.track.id)
        store.upvote(playlist_id, np.track.id, np.track)

    # Reorder playlist by votes
    apply_votes(mgr, store, playlist_id)

Usage from CLI::

    python cli.py votes up              # upvote current song
    python cli.py votes list            # show top-voted songs
    python cli.py votes apply <playlist>  # reorder playlist by votes
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .client import PlaylistManager
from .models import NowPlaying, Track

_DEFAULT_DB_DIR = Path("~/.local/share/spotify-playlist-manager").expanduser()
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "votes.db"


@dataclass(slots=True)
class VoteEntry:
    """A single vote record."""
    playlist: str
    track_id: str
    artist: str
    track_name: str
    votes: int
    voted_at: str


class VoteStore:
    """SQLite-backed vote database.

    Parameters
    ----------
    db_path : str | Path, optional
        Override the default database location.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(
            db_path
            or os.environ.get("SPM_VOTES_DB", "")
            or _DEFAULT_DB_PATH
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                playlist   TEXT    NOT NULL DEFAULT '',
                track_id   TEXT    NOT NULL,
                artist     TEXT    NOT NULL DEFAULT '',
                track_name TEXT    NOT NULL DEFAULT '',
                votes      INTEGER NOT NULL DEFAULT 1,
                voted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (playlist, track_id)
            )
        """)
        self._conn.commit()

    def get_votes(self, playlist: str, track_id: str) -> int:
        """Return the vote count for a track in a playlist, or 0."""
        row = self._conn.execute(
            "SELECT votes FROM votes WHERE playlist = ? AND track_id = ?",
            (playlist, track_id),
        ).fetchone()
        return row[0] if row else 0

    def upvote(
        self,
        playlist: str,
        track_id: str,
        track: Track | None = None,
    ) -> int:
        """Add a vote. Returns the new total.

        If *track* is provided, artist/track_name are stored for display.
        """
        current = self.get_votes(playlist, track_id)
        new_count = current + 1
        artist = track.artists[0] if track and track.artists else ""
        track_name = track.name if track else ""
        self._conn.execute(
            """INSERT INTO votes (playlist, track_id, artist, track_name, votes)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (playlist, track_id) DO UPDATE SET
                   votes = excluded.votes,
                   artist = CASE WHEN excluded.artist != '' THEN excluded.artist ELSE votes.artist END,
                   track_name = CASE WHEN excluded.track_name != '' THEN excluded.track_name ELSE votes.track_name END,
                   voted_at = datetime('now')
            """,
            (playlist, track_id, artist, track_name, new_count),
        )
        self._conn.commit()
        return new_count

    def top_tracks(self, playlist: str = "", limit: int = 20) -> list[VoteEntry]:
        """Return the top-voted tracks, optionally filtered by playlist."""
        if playlist:
            rows = self._conn.execute(
                "SELECT playlist, track_id, artist, track_name, votes, voted_at "
                "FROM votes WHERE playlist = ? ORDER BY votes DESC LIMIT ?",
                (playlist, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT '' as playlist, track_id, artist, track_name, SUM(votes) as total, MAX(voted_at) "
                "FROM votes GROUP BY track_id ORDER BY total DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            VoteEntry(
                playlist=r[0], track_id=r[1], artist=r[2],
                track_name=r[3], votes=r[4], voted_at=r[5],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


def apply_votes(
    mgr: PlaylistManager,
    store: VoteStore,
    playlist_id: str,
    dry_run: bool = False,
) -> list[tuple[str, str, int]]:
    """Reorder a Spotify playlist so the most-upvoted tracks are first.

    Uses :meth:`PlaylistManager.move_to_top` to promote each track in
    descending vote order. Tracks with equal votes keep their relative
    order (stable sort).

    Returns a list of ``(track_name, artist, votes)`` in the new top-first
    order. When *dry_run* is True, no Spotify calls are made.

    Only tracks that exist in both the vote DB **and** the playlist are
    moved — orphan votes are ignored.
    """
    entries = store.top_tracks(playlist_id, limit=500)
    if not entries:
        return []

    # Build a map of track_id -> current playlist position
    playlist_tracks = mgr.tracks(playlist_id)
    track_positions = {}
    for i, t in enumerate(playlist_tracks):
        track_positions[t.id] = i

    # Filter to tracks that actually exist in the playlist
    applicable = [e for e in entries if e.track_id in track_positions]
    if not applicable:
        return []

    results: list[tuple[str, str, int]] = []
    if dry_run:
        for e in applicable:
            results.append((e.track_name, e.artist, e.votes))
        return results

    # Move tracks to top in vote order (highest first).
    # move_to_top shifts everything else down, so processing highest-vote
    # first and then lower ones naturally produces the right final order.
    for entry in applicable:
        if track_positions.get(entry.track_id, -1) != 0:
            mgr.move_to_top(playlist_id, entry.track_id)
        results.append((entry.track_name, entry.artist, entry.votes))

    return results
