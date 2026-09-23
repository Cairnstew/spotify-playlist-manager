"""Offline unit tests for playback ("now playing") infra using a fake client.

The live API is exercised by test_export_integration.py-style runs; here we
verify the response mapping, the None-for-no-playback convention, and the
watch helper's change detection without any network.
"""

from __future__ import annotations

from itertools import islice

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.models import NowPlaying
from spotify_playlist_manager.player import blocks_until_change, watch_now_playing


class _FakeClient:
    """Playback stand-in: feeds scripts a list of responses, then None (EOF)."""

    def __init__(self, playback_responses: list[dict | None]) -> None:
        self._queue = list(playback_responses)
        self.calls = 0

    def current_playback(self):
        self.calls += 1
        if not self._queue:
            return None
        return self._queue.pop(0)


def _sample_track(track_id: str = "t1", name: str = "Instant Crush") -> dict:
    return {
        "type": "track",
        "id": track_id,
        "name": name,
        "artists": [{"name": "Daft Punk"}],
        "uri": f"spotify:track:{track_id}",
    }


def _playback(uri: str = "spotify:track:t1", track_id: str = "t1", name: str = "Song", is_playing: bool = True) -> dict:
    return {
        "device": {"name": "Kitchen", "type": "Computer", "volume_percent": 80},
        "shuffle_state": True,
        "repeat_state": "context",
        "progress_ms": 12000,
        "item": _sample_track(track_id, name),
        "is_playing": is_playing,
    }


class TestNowPlayingModel:
    def test_from_spotify_maps_fields(self) -> None:
        snapshot = NowPlaying.from_spotify(_playback())
        assert snapshot.track.name == "Song"
        assert snapshot.is_playing is True
        assert snapshot.progress_ms == 12000
        assert snapshot.device_name == "Kitchen"
        assert snapshot.volume_percent == 80
        assert snapshot.shuffle is True
        assert snapshot.repeat == "context"

    def test_to_dict_is_json_ready(self) -> None:
        data = NowPlaying.from_spotify(_playback()).to_dict()
        assert data["track"]["id"] == "t1"
        assert data["is_playing"] is True
        assert data["repeat"] == "context"


class TestPlaybackClient:
    def test_now_playing_returns_model(self) -> None:
        mgr = PlaylistManager(_FakeClient([_playback()]))  # type: ignore[arg-type]
        snapshot = mgr.now_playing()
        assert isinstance(snapshot, NowPlaying)
        assert snapshot.track.uri == "spotify:track:t1"

    def test_now_playing_none_when_no_playback(self) -> None:
        # The real API answers 204 here; spotipy yields None.
        mgr = PlaylistManager(_FakeClient([None]))  # type: ignore[arg-type]
        assert mgr.now_playing() is None

    def test_devices(self) -> None:
        fake = _FakeClient([])  # type: ignore[var-annotated]
        # devices() is a different endpoint; provide it directly.
        fake.devices = lambda: {"devices": [{"id": "d1", "name": "Kitchen"}]}
        mgr = PlaylistManager(fake)  # type: ignore[arg-type]
        assert mgr.devices() == [{"id": "d1", "name": "Kitchen"}]


class TestWatch:
    def test_yields_only_on_change(self) -> None:
        responses = [
            _playback(name="First"),
            _playback(name="First"),
            _playback(track_id="t2", name="Second"),
        ]
        mgr = PlaylistManager(_FakeClient(responses))  # type: ignore[arg-type]

        # The watcher polls forever; take only the first two yielded changes.
        seen = [snap.track.name for snap in islice(watch_now_playing(mgr, interval=0.01), 2)]
        assert seen == ["First", "Second"]

    def test_yields_none_when_playback_stops(self) -> None:
        responses = [
            _playback(name="First"),
            None,
        ]
        mgr = PlaylistManager(_FakeClient(responses))  # type: ignore[arg-type]

        seen = [snap if snap is None else snap.track.name for snap in islice(watch_now_playing(mgr, interval=0.01), 2)]
        assert seen == ["First", None]


class TestBlocksUntilChange:
    def test_returns_new_on_change(self) -> None:
        responses = [
            _playback(name="First"),
            _playback(track_id="t2", name="Second"),
        ]
        mgr = PlaylistManager(_FakeClient(responses))  # type: ignore[arg-type]
        snapshot = blocks_until_change(mgr, interval=0.01, timeout=5)
        assert snapshot is not None
        assert snapshot.track.name == "Second"