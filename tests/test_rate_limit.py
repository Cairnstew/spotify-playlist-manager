"""Offline unit tests for the Spotify API rate-limit guard.

Covers the sliding-window pacing, 429 backoff (Retry-After + exponential),
the header parser, ``_ApiProxy`` integration, and the 429 resilience of the
player watchers — all without any network.
"""

from __future__ import annotations

import time
from itertools import islice

import pytest
import spotipy

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.client import _ApiProxy
from spotify_playlist_manager.models import NowPlaying
from spotify_playlist_manager.player import blocks_until_change, watch_now_playing
from spotify_playlist_manager.rate_limit import RateLimitGuard, retry_after_seconds


# ---------------------------------------------------------------------------
# retry_after_seconds
# ---------------------------------------------------------------------------


class TestRetryAfterParsing:
    def test_int_header(self) -> None:
        assert retry_after_seconds({"Retry-After": "5"}) == 5.0

    def test_float_header(self) -> None:
        assert retry_after_seconds({"Retry-After": "0.5"}) == 0.5

    def test_numeric_value_not_str(self) -> None:
        assert retry_after_seconds({"Retry-After": 2}) == 2.0

    def test_lowercase_key(self) -> None:
        assert retry_after_seconds({"retry-after": "1"}) == 1.0

    def test_missing_header_returns_none(self) -> None:
        assert retry_after_seconds(None) is None
        assert retry_after_seconds({}) is None
        assert retry_after_seconds({"Content-Type": "application/json"}) is None

    def test_unparseable_returns_none(self) -> None:
        assert retry_after_seconds({"Retry-After": "soon"}) is None
        assert retry_after_seconds({"Retry-After": ""}) is None


# ---------------------------------------------------------------------------
# RateLimitGuard: pacing
# ---------------------------------------------------------------------------


class TestPacing:
    def test_acquire_records_timestamps_and_does_not_block_under_capacity(self) -> None:
        guard = RateLimitGuard(requests_per_window=10, window_seconds=30)
        start = time.monotonic()
        for _ in range(10):
            guard.acquire()
        assert time.monotonic() - start < 1.0  # no sleeping under capacity
        assert len(guard._timestamps) == 10  # noqa: SLF001 (test-internal)

    def test_acquire_blocks_once_window_is_full(self) -> None:
        guard = RateLimitGuard(requests_per_window=2, window_seconds=0.8)
        guard.acquire()
        guard.acquire()
        start = time.monotonic()
        guard.acquire()  # must wait for the oldest timestamp to roll off
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4
        assert elapsed < 2.0

    def test_enabled_false_is_noop(self) -> None:
        guard = RateLimitGuard(enabled=False, requests_per_window=1, window_seconds=0.1)
        start = time.monotonic()
        for _ in range(5):
            guard.acquire()
        assert time.monotonic() - start < 0.5
        assert len(guard._timestamps) == 0  # noqa: SLF001 (test-internal)

    def test_rate_per_second_reflects_config(self) -> None:
        guard = RateLimitGuard(requests_per_window=180, window_seconds=30)
        assert guard.rate_per_second == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# RateLimitGuard: 429 backoff
# ---------------------------------------------------------------------------


class TestBackoff:
    def test_record_429_with_retry_after_blocks_acquire(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        guard.record_429(retry_after=0.4)
        assert guard.retry_after == 0.4
        start = time.monotonic()
        guard.acquire()
        assert time.monotonic() - start >= 0.25
        assert guard.cooldown_remaining == 0.0

    def test_record_429_without_retry_after_uses_exponential_backoff(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)

        guard.record_429()  # 1st: 0.1
        assert guard.retry_after == pytest.approx(0.1)

        guard.record_429()  # 2nd consecutive: 0.2
        assert guard.retry_after == pytest.approx(0.2)

        guard.record_429()  # 3rd: 0.4
        assert guard.retry_after == pytest.approx(0.4)

    def test_exponential_backoff_is_capped(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=0.35)
        for _ in range(5):  # would be 3.2s uncapped
            guard.record_429()
        assert guard.retry_after == pytest.approx(0.35)

    def test_record_success_resets_consecutive_counter(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        guard.record_429()
        guard.record_429()  # consecutive -> 0.2
        guard.record_success()
        guard.record_429()  # back to 1st attempt -> 0.1
        assert guard.retry_after == pytest.approx(0.1)

    def test_record_429_blocks_until_cooldown_expires(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        guard.record_429(retry_after=0.3)
        assert guard.cooldown_remaining > 0
        time.sleep(0.5)
        assert guard.cooldown_remaining == 0.0


# ---------------------------------------------------------------------------
# _ApiProxy integration
# ---------------------------------------------------------------------------


class _FakeUnderlyingClient:
    """Stands in for spotipy.Spotify: implements _internal_call, and forwards
    the attribute lookups the proxy tests need."""

    def __init__(self, responses: list | None = None) -> None:
        self._queue = list(responses or [])
        self.calls = 0

    def _internal_call(self, method: str, url: str, payload, params) -> object:
        self.calls += 1
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"ok": True, "method": method, "url": url}


def _429(headers: dict | None = None) -> spotipy.SpotifyException:
    return spotipy.SpotifyException(429, -1, "rate limited", headers=headers or {})


class TestProxyIntegration:
    def test_proxy_passes_arguments_through_and_returns_result(self) -> None:
        fake = _FakeUnderlyingClient()
        proxy = _ApiProxy(fake)  # type: ignore[arg-type]
        result = proxy._internal_call("GET", "https://api.spotify.com/v1/me", None, {})
        assert result == {"ok": True, "method": "GET", "url": "https://api.spotify.com/v1/me"}
        assert fake.calls == 1

    def test_proxy_accepts_custom_guard(self) -> None:
        guard = RateLimitGuard(enabled=False)
        fake = _FakeUnderlyingClient([{"ok": True}])
        proxy = _ApiProxy(fake, rate_limit=guard)  # type: ignore[arg-type]
        proxy._internal_call("GET", "https://api.spotify.com/v1/me", None, {})
        assert guard.enabled is False

    def test_429_records_retry_after_on_guard(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        fake = _FakeUnderlyingClient([_429(headers={"Retry-After": "0.4"})])
        proxy = _ApiProxy(fake, rate_limit=guard)  # type: ignore[arg-type]

        with pytest.raises(spotipy.SpotifyException) as exc_info:
            proxy._internal_call("GET", "https://api.spotify.com/v1/me/player", None, {})
        assert exc_info.value.http_status == 429
        assert guard.retry_after == pytest.approx(0.4)
        assert guard.cooldown_remaining > 0

        # The recorded cooldown makes the *next* acquire back off.
        guard2 = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        guard2.record_429(retry_after=0.3)
        start = time.monotonic()
        guard2.acquire()
        assert time.monotonic() - start >= 0.2

    def test_429_without_header_falls_back_to_exponential(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        fake = _FakeUnderlyingClient([_429(headers={})])
        proxy = _ApiProxy(fake, rate_limit=guard)  # type: ignore[arg-type]

        with pytest.raises(spotipy.SpotifyException):
            proxy._internal_call("GET", "https://api.spotify.com/v1/me/player", None, {})
        assert guard.retry_after == pytest.approx(0.1)

    def test_success_resets_backoff_state(self) -> None:
        guard = RateLimitGuard(base_backoff=0.1, max_backoff=10)
        fake = _FakeUnderlyingClient([_429(headers={}), {"ok": True}])
        proxy = _ApiProxy(fake, rate_limit=guard)  # type: ignore[arg-type]

        with pytest.raises(spotipy.SpotifyException):
            proxy._internal_call("GET", "https://api.spotify.com/v1/me", None, {})
        assert guard.retry_after == pytest.approx(0.1)

        proxy._internal_call("GET", "https://api.spotify.com/v1/me", None, {})
        assert guard.retry_after == pytest.approx(0.1)  # unchanged by success

    def test_playlist_manager_threads_guard_through(self) -> None:
        guard = RateLimitGuard(enabled=False)
        fake = _FakeUnderlyingClient([{"ok": True}])
        mgr = PlaylistManager(fake, rate_limit=guard)  # type: ignore[arg-type]
        assert mgr.client._guard is guard  # noqa: SLF001 (test-internal)

    def test_default_guard_is_enabled(self) -> None:
        fake = _FakeUnderlyingClient([{"ok": True}])
        mgr = PlaylistManager(fake)  # type: ignore[arg-type]
        assert mgr.client._guard.enabled is True  # noqa: SLF001 (test-internal)


# ---------------------------------------------------------------------------
# player.py 429 resilience
# ---------------------------------------------------------------------------


def _sample_track(track_id: str = "t1", name: str = "Instant Crush") -> dict:
    return {
        "type": "track",
        "id": track_id,
        "name": name,
        "artists": [{"name": "Daft Punk"}],
        "uri": f"spotify:track:{track_id}",
    }


def _playback(uri: str = "spotify:track:t1", track_id: str = "t1", name: str = "Song") -> dict:
    return {
        "device": {"name": "Kitchen", "type": "Computer", "volume_percent": 80},
        "shuffle_state": True,
        "repeat_state": "context",
        "progress_ms": 12000,
        "item": _sample_track(track_id, name),
        "is_playing": True,
    }


class _FlakyManager:
    """A duck-typed PlaylistManager that can raise 429s mid-stream."""

    def __init__(self, responses: list) -> None:
        self._queue = list(responses)
        self.calls = 0

    def now_playing(self) -> NowPlaying | None:
        self.calls += 1
        if not self._queue:
            return None
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return NowPlaying.from_spotify(item)


class TestWatchRateLimit:
    def test_watch_skips_429_without_emitting_none(self) -> None:
        mgr = _FlakyManager(
            [
                _playback(name="First"),
                _429(headers={"Retry-After": "1"}),
                _playback(track_id="t2", name="Second"),
            ]
        )
        seen = [
            snap if snap is None else snap.track.name
            for snap in islice(watch_now_playing(mgr, interval=0.01), 2)  # type: ignore[arg-type]
        ]
        # The 429 must NOT surface as "nothing playing": only real snapshots.
        assert seen == ["First", "Second"]
        assert mgr.calls == 3

    def test_watch_propagates_non_429_errors(self) -> None:
        mgr = _FlakyManager([spotipy.SpotifyException(401, -1, "unauthorized")])
        with pytest.raises(spotipy.SpotifyException):
            next(islice(watch_now_playing(mgr, interval=0.01), 1))  # type: ignore[arg-type]

    def test_blocks_until_change_survives_rate_limit(self) -> None:
        mgr = _FlakyManager(
            [
                _playback(name="First"),
                _429(headers={"Retry-After": "1"}),
                _playback(track_id="t2", name="Second"),
            ]
        )
        snapshot = blocks_until_change(mgr, interval=0.01, timeout=5)  # type: ignore[arg-type]
        assert snapshot is not None
        assert snapshot.track.name == "Second"

    def test_blocks_until_change_rate_limited_on_first_poll(self) -> None:
        # First poll is a 429: no baseline, so the next snapshot is the change.
        mgr = _FlakyManager(
            [
                _429(headers={"Retry-After": "1"}),
                _playback(name="First"),
            ]
        )
        snapshot = blocks_until_change(mgr, interval=0.01, timeout=5)  # type: ignore[arg-type]
        assert snapshot is not None
        assert snapshot.track.name == "First"