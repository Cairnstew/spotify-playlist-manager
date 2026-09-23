"""Unit tests for the model conversions — no network required."""

from __future__ import annotations

from spotify_playlist_manager.models import Playlist, Track


def test_track_from_flat_item() -> None:
    track = Track.from_spotify(
        {
            "type": "track",
            "id": "abc123",
            "name": "Instant Crush",
            "artists": [{"name": "Daft Punk"}],
            "album": {"name": "Random Access Memories"},
            "uri": "spotify:track:abc123",
            "duration_ms": 337000,
        }
    )
    assert track.id == "abc123"
    assert track.name == "Instant Crush"
    assert track.artists == ["Daft Punk"]
    assert track.album == "Random Access Memories"
    assert track.uri == "spotify:track:abc123"
    assert track.duration_ms == 337000


def test_track_from_wrapped_item_like_playlist_tracks() -> None:
    wrapped = {
        "track": {
            "type": "track",
            "id": "xyz789",
            "name": "Get Lucky",
            "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}],
            "album": {"name": "Random Access Memories"},
            "uri": "spotify:track:xyz789",
        }
    }
    track = Track.from_spotify(wrapped)
    assert track.id == "xyz789"
    assert track.artists == ["Daft Punk", "Pharrell Williams"]


def test_track_unknown_when_not_a_track() -> None:
    track = Track.from_spotify({"type": "episode", "id": "e1", "name": "Pod"})
    assert track.id == ""
    assert track.name == "(unknown)"


def test_track_from_new_item_wrapper() -> None:
    # Current playlist-items endpoint wraps under "item" and keeps "track" /
    # "episode" as booleans — the old "track"-envelope logic must NOT pick
    # up the boolean as a payload.
    wrapped = {
        "added_at": "2026-01-01T00:00:00Z",
        "is_local": False,
        "item": {
            "type": "track",
            "id": "abc789",
            "name": "One More Time",
            "artists": [{"name": "Daft Punk"}],
            "album": {"name": "Discovery"},
            "uri": "spotify:track:abc789",
            "track": True,
            "episode": False,
        },
    }
    track = Track.from_spotify(wrapped)
    assert track.id == "abc789"
    assert track.name == "One More Time"
    assert track.uri == "spotify:track:abc789"


def test_playlist_from_spotify() -> None:
    playlist = Playlist.from_spotify(
        {
            "id": "p-1",
            "name": "Test",
            "uri": "spotify:playlist:p-1",
            "description": "A playlist",
            "owner": {"display_name": "seanc", "id": "seanc"},
            "public": False,
            "snapshot_id": "snap-1",
        }
    )
    assert playlist.id == "p-1"
    assert playlist.name == "Test"
    assert playlist.owner == "seanc"
    assert playlist.public is False
    assert playlist.snapshot_id == "snap-1"


def test_playlist_str() -> None:
    assert str(Playlist(id="p", name="X")) == "X (p)"


def test_track_str() -> None:
    track = Track(id="t", name="Song", artists=["A", "B"])
    assert str(track) == "Song — A, B"


class TestSerialization:
    def test_track_to_dict_is_json_ready(self) -> None:
        track = Track(id="abc123", name="Instant Crush", artists=["Daft Punk"], uri="spotify:track:abc123")
        assert track.to_dict() == {
            "id": "abc123",
            "name": "Instant Crush",
            "artists": ["Daft Punk"],
            "album": "",
            "uri": "spotify:track:abc123",
            "duration_ms": 0,
        }

    def test_playlist_to_dict_nests_tracks(self) -> None:
        playlist = Playlist(id="p-1", name="Test", tracks=[Track(id="t1", name="A", artists=["X"])])
        data = playlist.to_dict()
        assert data["id"] == "p-1"
        assert data["name"] == "Test"
        assert data["tracks"] == [Track(id="t1", name="A", artists=["X"]).to_dict()]
        assert data["public"] is True