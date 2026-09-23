"""Offline unit tests for export.py using a fake Spotify client.

The live API is exercised by test_export_integration.py; these tests verify
the export pipeline (playlists -> per-playlist tracks -> JSON document)
without any network or credentials.
"""

from __future__ import annotations

import json
import types

import pytest

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.export import (
    export_playlists,
    export_playlists_to_file,
    write_export,
)


class _FakeClient:
    """Minimal stand-in for spotipy.Spotify with playback of paginated data."""

    def __init__(self, playlists: list[dict], tracks_by_id: dict[str, list[dict]]) -> None:
        self._playlists = playlists
        self._tracks = tracks_by_id

    def current_user_playlists(self, limit=50, offset=0):
        return {"items": self._playlists[offset : offset + limit]}

    def playlist_tracks(self, playlist_id, limit=50, offset=0):
        items = self._tracks.get(playlist_id, [])
        # Mirror the real playlist-items wrapper shape: track under "item".
        wrapped = [{"item": t} for t in items[offset : offset + limit]]
        return {"items": wrapped}


def _sample_track(track_id: str, name: str) -> dict:
    return {
        "type": "track",
        "id": track_id,
        "name": name,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "uri": f"spotify:track:{track_id}",
        "duration_ms": 180000,
    }


@pytest.fixture
def manager() -> PlaylistManager:
    # Real Spotify ids are 22 base62 chars; the ref validators enforce that.
    p1 = "aaaaaaaaaaaaaaaaaaaaaa"
    p2 = "bbbbbbbbbbbbbbbbbbbbbb"
    playlists = [
        {"id": p1, "name": "Alpha", "owner": {"display_name": "me", "id": "me"}, "public": True},
        {"id": p2, "name": "Beta", "owner": {"display_name": "me", "id": "me"}, "public": False},
    ]
    tracks = {
        p1: [
            _sample_track("t1", "One"),
            _sample_track("t2", "Two"),
        ],
        p2: [],
    }
    fake = _FakeClient(playlists, tracks)
    return PlaylistManager(fake)  # type: ignore[arg-type]


def test_export_playlists_returns_dicts_with_loaded_tracks(manager: PlaylistManager) -> None:
    exported = export_playlists(manager)
    assert [p["name"] for p in exported] == ["Alpha", "Beta"]

    alpha = exported[0]
    assert [t["name"] for t in alpha["tracks"]] == ["One", "Two"]
    assert alpha["tracks"][0]["uri"] == "spotify:track:t1"

    beta = exported[1]
    assert beta["tracks"] == []


def test_export_playlists_metadata_only(manager: PlaylistManager) -> None:
    exported = export_playlists(manager, include_tracks=False)
    for playlist in exported:
        assert playlist["tracks"] == []
        assert "name" in playlist


def test_write_export_round_trips_as_json(tmp_path, manager: PlaylistManager) -> None:
    exported = export_playlists(manager)
    path = write_export(exported, tmp_path / "out.json")

    document = json.loads(path.read_text())
    assert document["playlist_count"] == 2
    assert len(document["playlists"]) == 2
    assert document["playlists"][0]["tracks"][0]["name"] == "One"


def test_export_playlists_to_file(tmp_path, manager: PlaylistManager) -> None:
    path = export_playlists_to_file(manager, tmp_path / "playlists.json")
    document = json.loads(path.read_text())
    assert document["exported_at"]
    assert document["playlist_count"] == 2