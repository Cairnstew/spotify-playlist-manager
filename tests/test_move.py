"""Offline unit tests for playlist item moving (reorder math).

The live API is exercised by test_export_integration.py-style runs; these
tests verify the *parameters* sent to the reorder endpoint for up/down moves
— the part that is easy to get wrong — using a fake recording client.
"""

from __future__ import annotations

import pytest

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.errors import TrackNotFoundError

PLAYLIST = "aaaaaaaaaaaaaaaaaaaaaa"  # 22-char realistic id
# 22-char base62-looking ids: track0000..track0004 only have 12 chars, which
# the ref validators (correctly) reject — use full-length ones instead.
TRACK_IDS = [
    "t000000000000000000000",
    "t000000000000000000001",
    "t000000000000000000002",
    "t000000000000000000003",
    "t000000000000000000004",
]


def _uris(ids: list[str]) -> list[str]:
    return [f"spotify:track:{tid}" for tid in ids]


class _FakeClient:
    def __init__(self, track_ids: list[str]) -> None:
        self._track_ids = list(track_ids)
        self.reorder_calls: list[tuple[int, int, int]] = []

    def playlist_tracks(self, playlist_id, limit=50, offset=0):
        items = []
        for tid in self._track_ids[offset : offset + limit]:
            items.append(
                {
                    "added_at": "2026-01-01T00:00:00Z",
                    "is_local": False,
                    # Real playlist items wrap the track under an "item" key,
                    # with track/episode as booleans alongside it.
                    "item": {
                        "type": "track",
                        "id": tid,
                        "uri": f"spotify:track:{tid}",
                        "name": tid,
                        "track": True,
                        "episode": False,
                    },
                }
            )
        return {"items": items}

    def playlist_reorder_items(self, playlist_id, range_start, insert_before, range_length=1, snapshot_id=None):
        self.reorder_calls.append((range_start, insert_before, range_length))


@pytest.fixture
def mgr() -> PlaylistManager:
    return PlaylistManager(_FakeClient(TRACK_IDS))  # type: ignore[arg-type]


class TestMoveTrack:
    def test_move_down_uses_new_plus_one(self, mgr: PlaylistManager) -> None:
        # Tracks: [0,1,2,3,4]. Move index 1 to index 3.
        mgr.move_track(PLAYLIST, 1, 3)
        # Removing index 1 first, insert-before must target the slot AFTER the
        # destination (new_index + 1 when moving down).
        assert mgr.client.reorder_calls == [(1, 4, 1)]  # type: ignore[attr-defined]

    def test_move_up_uses_new_index(self, mgr: PlaylistManager) -> None:
        # Move index 3 to index 1.
        mgr.move_track(PLAYLIST, 3, 1)
        assert mgr.client.reorder_calls == [(3, 1, 1)]  # type: ignore[attr-defined]

    def test_move_by_track_reference(self, mgr: PlaylistManager) -> None:
        # Find spotify:track:t000000000000000000003 (index 3), move to 1.
        mgr.move_track(PLAYLIST, "spotify:track:t000000000000000000003", 1)
        assert mgr.client.reorder_calls == [(3, 1, 1)]  # type: ignore[attr-defined]

    def test_noop_when_same_index(self, mgr: PlaylistManager) -> None:
        mgr.move_track(PLAYLIST, 2, 2)
        assert mgr.client.reorder_calls == []  # type: ignore[attr-defined]

    def test_returns_target_index(self, mgr: PlaylistManager) -> None:
        assert mgr.move_track(PLAYLIST, 0, 4) == 4


class TestMoveUpDown:
    def test_up(self, mgr: PlaylistManager) -> None:
        assert mgr.move_up(PLAYLIST, 3) == 2
        assert mgr.client.reorder_calls == [(3, 2, 1)]  # type: ignore[attr-defined]

    def test_down(self, mgr: PlaylistManager) -> None:
        assert mgr.move_down(PLAYLIST, 1) == 2
        assert mgr.client.reorder_calls == [(1, 3, 1)]  # type: ignore[attr-defined]

    def test_up_by_amount(self, mgr: PlaylistManager) -> None:
        assert mgr.move_up(PLAYLIST, 4, amount=2) == 2
        assert mgr.client.reorder_calls == [(4, 2, 1)]  # type: ignore[attr-defined]

    def test_down_by_track_uri(self, mgr: PlaylistManager) -> None:
        assert mgr.move_down(PLAYLIST, "spotify:track:t000000000000000000000") == 1
        assert mgr.client.reorder_calls == [(0, 2, 1)]  # type: ignore[attr-defined]

    def test_clamps_at_boundaries(self, mgr: PlaylistManager) -> None:
        assert mgr.move_up(PLAYLIST, 0) == 0
        assert mgr.move_down(PLAYLIST, 4) == 4
        assert mgr.client.reorder_calls == []  # type: ignore[attr-defined]


class TestMoveToEnds:
    def test_to_top(self, mgr: PlaylistManager) -> None:
        assert mgr.move_to_top(PLAYLIST, 3) == 0
        assert mgr.client.reorder_calls == [(3, 0, 1)]  # type: ignore[attr-defined]

    def test_to_bottom(self, mgr: PlaylistManager) -> None:
        assert mgr.move_to_bottom(PLAYLIST, 0) == 4
        assert mgr.client.reorder_calls == [(0, 5, 1)]  # type: ignore[attr-defined]


class TestTrackPosition:
    def test_finds_index(self, mgr: PlaylistManager) -> None:
        assert mgr.track_position(PLAYLIST, "t000000000000000000002") == 2

    def test_raises_when_absent(self, mgr: PlaylistManager) -> None:
        with pytest.raises(TrackNotFoundError):
            mgr.track_position(PLAYLIST, "spotify:track:nomatch0000")