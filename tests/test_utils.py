"""Unit tests for reference parsing and chunking — no network required."""

from __future__ import annotations

import pytest

from spotify_playlist_manager.errors import TrackReferenceError
from spotify_playlist_manager.utils import (
    chunk,
    playlist_id_from_ref,
    track_id_from_ref,
    track_uri_from_ref,
)

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


class TestTrackId:
    def test_bare_id(self) -> None:
        assert track_id_from_ref(TRACK_ID) == TRACK_ID

    def test_uri(self) -> None:
        assert track_id_from_ref(f"spotify:track:{TRACK_ID}") == TRACK_ID

    def test_url(self) -> None:
        assert track_id_from_ref(f"https://open.spotify.com/track/{TRACK_ID}") == TRACK_ID

    def test_url_with_query(self) -> None:
        assert (
            track_id_from_ref(f"https://open.spotify.com/track/{TRACK_ID}?si=abc123")
            == TRACK_ID
        )

    def test_rejects_unsupported_kind(self) -> None:
        with pytest.raises(TrackReferenceError):
            track_id_from_ref(f"https://open.spotify.com/playlist/{PLAYLIST_ID}")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(TrackReferenceError):
            track_id_from_ref("some random text")

    def test_whitespace_stripped(self) -> None:
        assert track_id_from_ref(f"  {TRACK_ID}  ") == TRACK_ID


class TestTrackUri:
    def test_uri_roundtrip(self) -> None:
        assert track_uri_from_ref(f"spotify:track:{TRACK_ID}") == f"spotify:track:{TRACK_ID}"

    def test_url_to_uri(self) -> None:
        assert (
            track_uri_from_ref(f"https://open.spotify.com/track/{TRACK_ID}?si=x")
            == f"spotify:track:{TRACK_ID}"
        )


class TestPlaylistId:
    def test_url(self) -> None:
        assert (
            playlist_id_from_ref(f"https://open.spotify.com/playlist/{PLAYLIST_ID}")
            == PLAYLIST_ID
        )

    def test_uri(self) -> None:
        assert playlist_id_from_ref(f"spotify:playlist:{PLAYLIST_ID}") == PLAYLIST_ID


class TestChunk:
    def test_empty(self) -> None:
        assert list(chunk([])) == []

    def test_under_size(self) -> None:
        assert list(chunk(["a", "b"], size=100)) == [["a", "b"]]

    def test_exact_multiple(self) -> None:
        assert list(chunk(["a", "b", "c", "d"], size=2)) == [["a", "b"], ["c", "d"]]

    def test_remainder(self) -> None:
        assert list(chunk(["a", "b", "c"], size=2)) == [["a", "b"], ["c"]]