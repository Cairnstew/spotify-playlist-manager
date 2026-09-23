"""Live integration test: pull every playlist plus its tracks into JSON.

This test hits the real Spotify Web API, so it only runs when:

* credentials are present (either in the environment or a local ``.env``), AND
* ``RUN_LIVE=1`` is set in the environment.

Without either it is skipped cleanly, keeping the default ``pytest`` run
offline and fast.

Run it like this:

    RUN_LIVE=1 pytest tests/test_export_integration.py -v
"""

from __future__ import annotations

import json
import os

import pytest
from dotenv import load_dotenv

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.export import export_playlists, export_playlists_to_file

load_dotenv()  # make a local .env visible to the skip check below

pytestmark = pytest.mark.integration

LIVE_ENABLED = (
    os.environ.get("RUN_LIVE") == "1"
    and bool(os.environ.get("SPOTIFY_CLIENT_ID"))
)

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason=(
        "live integration test: set SPOTIFY_CLIENT_ID in .env (or environment) "
        "and RUN_LIVE=1 to run"
    ),
)


def _playlist_keys() -> set[str]:
    return {"id", "name", "uri", "description", "owner", "public", "snapshot_id", "tracks"}


def _track_keys() -> set[str]:
    return {"id", "name", "artists", "album", "uri", "duration_ms"}


def test_export_playlists_returns_json_ready_dicts() -> None:
    manager = PlaylistManager.from_env()
    playlists = export_playlists(manager)

    # Every entry is a plain dict (the dataclass -> JSON conversion works).
    for playlist in playlists:
        assert isinstance(playlist, dict)
        assert _playlist_keys() <= set(playlist)
        assert isinstance(playlist["tracks"], list)
        for track in playlist["tracks"]:
            assert isinstance(track, dict)
            assert _track_keys() <= set(track)


def test_export_writes_self_describing_json_file(tmp_path: pytest.TempPathFactory) -> None:
    manager = PlaylistManager.from_env()
    path = export_playlists_to_file(manager, tmp_path / "playlists.json")

    document = json.loads(path.read_text())
    assert document["playlist_count"] == len(document["playlists"])
    assert "exported_at" in document
    for playlist in document["playlists"]:
        assert _playlist_keys() <= set(playlist)


def test_export_metadata_only_without_tracks() -> None:
    manager = PlaylistManager.from_env()
    playlists = export_playlists(manager, include_tracks=False)
    for playlist in playlists:
        # Without include_tracks the tracks list is still present (a valid
        # dict serializes), but empty — no extra API calls were made.
        assert playlist["tracks"] == []