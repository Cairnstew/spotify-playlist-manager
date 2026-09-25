"""Offline unit tests for the votes module.

Tests the VoteStore database operations and the apply_votes reorder logic
using an in-memory SQLite database and a fake PlaylistManager.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spotify_playlist_manager.models import Track
from spotify_playlist_manager.votes import VoteStore, apply_votes


def _make_track(track_id: str, name: str, artist: str = "Test Artist") -> Track:
    return Track(
        id=track_id,
        name=name,
        artists=[artist],
        album="Test Album",
        uri=f"spotify:track:{track_id}",
        duration_ms=180_000,
    )


@pytest.fixture
def tmp_store(tmp_path: Path) -> VoteStore:
    return VoteStore(db_path=tmp_path / "test.db")


class TestVoteStore:
    def test_upvote_increments(self, tmp_store: VoteStore) -> None:
        track = _make_track("track_a", "Song A")
        assert tmp_store.upvote("pl1", "track_a", track) == 1
        assert tmp_store.upvote("pl1", "track_a", track) == 2
        assert tmp_store.upvote("pl1", "track_a", track) == 3

    def test_get_votes(self, tmp_store: VoteStore) -> None:
        assert tmp_store.get_votes("pl1", "nonexistent") == 0
        tmp_store.upvote("pl1", "track_a")
        assert tmp_store.get_votes("pl1", "track_a") == 1

    def test_votes_scoped_by_playlist(self, tmp_store: VoteStore) -> None:
        tmp_store.upvote("pl1", "track_a")
        tmp_store.upvote("pl2", "track_a")
        assert tmp_store.get_votes("pl1", "track_a") == 1
        assert tmp_store.get_votes("pl2", "track_a") == 1

    def test_top_tracks_ordering(self, tmp_store: VoteStore) -> None:
        tmp_store.upvote("pl1", "track_a", _make_track("track_a", "Song A"))
        tmp_store.upvote("pl1", "track_b", _make_track("track_b", "Song B"))
        tmp_store.upvote("pl1", "track_b", _make_track("track_b", "Song B"))
        tmp_store.upvote("pl1", "track_c", _make_track("track_c", "Song C"))

        top = tmp_store.top_tracks("pl1")
        assert len(top) == 3
        assert top[0].track_id == "track_b"  # 2 votes
        assert top[1].track_id == "track_a"  # 1 vote
        assert top[2].track_id == "track_c"  # 1 vote

    def test_top_tracks_all_playlists(self, tmp_store: VoteStore) -> None:
        tmp_store.upvote("pl1", "track_a", _make_track("track_a", "Song A"))
        tmp_store.upvote("pl2", "track_a", _make_track("track_a", "Song A"))
        # track_a has 2 total votes across playlists
        top = tmp_store.top_tracks()
        assert len(top) == 1
        assert top[0].track_id == "track_a"
        assert top[0].votes == 2

    def test_top_tracks_limit(self, tmp_store: VoteStore) -> None:
        for i in range(5):
            tmp_store.upvote("pl1", f"track_{i}", _make_track(f"track_{i}", f"Song {i}"))
        top = tmp_store.top_tracks("pl1", limit=3)
        assert len(top) == 3

    def test_stores_artist_and_name(self, tmp_store: VoteStore) -> None:
        track = _make_track("track_x", "My Song", "Cool Artist")
        tmp_store.upvote("pl1", "track_x", track)
        top = tmp_store.top_tracks("pl1")
        assert top[0].artist == "Cool Artist"
        assert top[0].track_name == "My Song"

    def test_upvote_without_track_metadata(self, tmp_store: VoteStore) -> None:
        # upvote with track=None should still work
        count = tmp_store.upvote("pl1", "track_y")
        assert count == 1
        top = tmp_store.top_tracks("pl1")
        assert top[0].track_id == "track_y"
        assert top[0].artist == ""
        assert top[0].track_name == ""


def _make_fake_manager(track_ids: list[str]) -> MagicMock:
    """Build a mock PlaylistManager with tracks() returning the given IDs."""
    mgr = MagicMock()
    tracks = [_make_track(tid, f"Song {tid}") for tid in track_ids]
    mgr.tracks.return_value = tracks
    mgr.move_to_top.return_value = 0
    return mgr


class TestApplyVotes:
    def test_apply_moves_highest_to_top(self, tmp_store: VoteStore) -> None:
        # Playlist: [A, B, C]  Votes: C=3, A=1
        tmp_store.upvote("pl1", "track_c", _make_track("track_c", "Song C"))
        tmp_store.upvote("pl1", "track_c", _make_track("track_c", "Song C"))
        tmp_store.upvote("pl1", "track_c", _make_track("track_c", "Song C"))
        tmp_store.upvote("pl1", "track_a", _make_track("track_a", "Song A"))

        mgr = _make_fake_manager(["track_a", "track_b", "track_c"])
        results = apply_votes(mgr, store=tmp_store, playlist_id="pl1")

        assert len(results) == 2  # only tracks with votes
        assert results[0][0] == "Song C"  # highest first
        assert results[0][2] == 3
        assert results[1][0] == "Song A"
        assert results[1][2] == 1

        # move_to_top should have been called for C (at index 2) and A (at index 0, no move needed)
        mgr.move_to_top.assert_called_once_with("pl1", "track_c")

    def test_apply_dry_run_no_spotify_calls(self, tmp_store: VoteStore) -> None:
        tmp_store.upvote("pl1", "track_a", _make_track("track_a", "Song A"))
        mgr = _make_fake_manager(["track_a"])
        results = apply_votes(mgr, store=tmp_store, playlist_id="pl1", dry_run=True)

        assert len(results) == 1
        mgr.move_to_top.assert_not_called()

    def test_apply_empty_votes(self, tmp_store: VoteStore) -> None:
        mgr = _make_fake_manager(["track_a"])
        results = apply_votes(mgr, store=tmp_store, playlist_id="pl1")
        assert results == []
        mgr.move_to_top.assert_not_called()

    def test_apply_ignores_orphan_votes(self, tmp_store: VoteStore) -> None:
        # Vote for a track not in the playlist
        tmp_store.upvote("pl1", "track_z", _make_track("track_z", "Ghost Track"))
        mgr = _make_fake_manager(["track_a", "track_b"])
        results = apply_votes(mgr, store=tmp_store, playlist_id="pl1")
        assert results == []
        mgr.move_to_top.assert_not_called()

    def test_apply_skips_already_at_top(self, tmp_store: VoteStore) -> None:
        # Track A is already at index 0 and has votes
        tmp_store.upvote("pl1", "track_a", _make_track("track_a", "Song A"))
        mgr = _make_fake_manager(["track_a", "track_b"])
        results = apply_votes(mgr, store=tmp_store, playlist_id="pl1")

        assert len(results) == 1
        # move_to_top should NOT be called since it's already at 0
        mgr.move_to_top.assert_not_called()
