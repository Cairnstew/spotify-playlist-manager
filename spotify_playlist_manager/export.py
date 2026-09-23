"""Export every playlist (and its tracks) into a JSON-ready structure.

The ``PlaylistManager`` returns typed dataclasses; this module converts them
into plain nested dicts and (optionally) writes a compact JSON document that
can be grepped, diffed, or fed into downstream tooling:

    export = export_playlists(mgr)            # list of playlist dicts
    write_export(export, "export.json")       # {"exported_at": ..., "playlists": [...]}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import PlaylistManager


def export_playlists(manager: PlaylistManager, include_tracks: bool = True) -> list[dict[str, Any]]:
    """Fetch every playlist of the authenticated user and return it as dicts.

    Each dict is a :class:`~spotify_playlist_manager.models.Playlist`'s
    ``to_dict()`` output with its ``tracks`` list populated (unless
    ``include_tracks`` is False, which only fetches playlist metadata).

    This calls :meth:`PlaylistManager.playlists` (one paginated walk) and then
    one paginated track fetch per playlist.
    """
    playlists = manager.playlists()
    exports: list[dict[str, Any]] = []
    for playlist in playlists:
        data = playlist.to_dict()
        if include_tracks:
            data["tracks"] = [track.to_dict() for track in manager.tracks(playlist.id)]
        exports.append(data)
    return exports


def write_export(playlists: list[dict[str, Any]], path: str | Path) -> Path:
    """Write an export document to ``path``.

    The document wraps the playlist list with metadata so a single file is
    self-describing::

        {
          "exported_at": "2026-09-21T06:58:00+00:00",
          "playlist_count": 3,
          "playlists": [ ... ]
        }
    """
    path = Path(path)
    document = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "playlist_count": len(playlists),
        "playlists": playlists,
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    return path


def export_playlists_to_file(
    manager: PlaylistManager, path: str | Path, include_tracks: bool = True
) -> Path:
    """One-shot helper: pull everything and write it to ``path``.

    Equivalent to ``write_export(export_playlists(manager, include_tracks), path)``.
    """
    return write_export(export_playlists(manager, include_tracks=include_tracks), path)