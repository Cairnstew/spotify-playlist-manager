"""Offline unit tests for the waybar module and its CLI wrappers.

Everything is mocked: the PlaylistManager, the VoteStore, and the argparse
arguments. No Spotify API or real votes database is touched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spotify_playlist_manager.models import NowPlaying, Track
from spotify_playlist_manager.waybar import (
    UpvoteResult,
    find_playlist_for_track,
    now_playing_output,
    upvote_current_track,
    upvote_output,
)


def _make_track(track_id: str, name: str, artist: str = "Test Artist", album: str = "Test Album") -> Track:
    return Track(
        id=track_id,
        name=name,
        artists=[artist],
        album=album,
        uri=f"spotify:track:{track_id}",
        duration_ms=180_000,
    )


def _make_now(track: Track, is_playing: bool = True, device: str = "laptop") -> NowPlaying:
    return NowPlaying(
        track=track,
        is_playing=is_playing,
        progress_ms=65_000,
        device_name=device,
        device_type="computer",
        volume_percent=50,
        shuffle=False,
        repeat="off",
    )


class TestNowPlayingOutput:
    def test_playing_track(self) -> None:
        now = _make_now(_make_track("abc", "Some Song", "Some Artist", "Some Album"))
        out = now_playing_output(now)
        assert out["class"] == "playing"
        assert out["text"].startswith("▶ ")
        assert "Some Song" in out["text"]
        assert "Some Artist" in out["text"]
        out["text"] = out["text"].replace("▶ ", "")
        assert json.loads(json.dumps(out)) == out  # JSON-serialisable

    def test_paused_track(self) -> None:
        now = _make_now(_make_track("abc", "Some Song"), is_playing=False)
        out = now_playing_output(now)
        assert out["class"] == "paused"
        assert out["text"].startswith("⏸ ")

    def test_nothing_playing(self) -> None:
        out = now_playing_output(None)
        assert out["class"] == "stopped"
        assert out["text"] == "♪"
        assert "Nothing playing" in out["tooltip"]

    def test_tooltip_carries_metadata(self) -> None:
        now = _make_now(_make_track("abc", "Some Song", "Some Artist", "Some Album"))
        out = now_playing_output(now)
        assert "Some Album" in out["tooltip"]
        assert "laptop" in out["tooltip"]
        assert "01:05" in out["tooltip"]  # progress 65s


class TestUpvoteOutput:
    def test_zero_votes(self) -> None:
        out = upvote_output(0)
        assert out["text"] == "♡"
        assert out["class"] == "not-upvoted"

    def test_positive_votes(self) -> None:
        out = upvote_output(3)
        assert out["text"] == "♥ 3"
        assert out["class"] == "upvoted"

    def test_tooltip_includes_track(self) -> None:
        track = _make_track("abc", "Some Song", "Some Artist")
        out = upvote_output(2, track=track, playlist="pl1")
        assert "Some Song" in out["tooltip"]
        assert "pl1" in out["tooltip"]
        assert "2 votes" in out["tooltip"]

    def test_singular_vote(self) -> None:
        out = upvote_output(1)
        assert "1 vote" in out["tooltip"]


class TestFindPlaylistForTrack:
    def _mgr_with_playlists(self, playlist_ids: list[str]) -> MagicMock:
        mgr = MagicMock()
        mgr.playlists.return_value = [MagicMock(id=pid) for pid in playlist_ids]
        return mgr

    def test_finds_containing_playlist(self) -> None:
        mgr = self._mgr_with_playlists(["pl_a", "pl_b", "pl_c"])
        mgr.tracks.side_effect = [
            [_make_track("t1", "One")],
            [_make_track("t2", "Two"), _make_track("target", "Target")],
            [_make_track("t3", "Three")],
        ]
        assert find_playlist_for_track(mgr, "target") == "pl_b"

    def test_not_found_returns_empty(self) -> None:
        mgr = self._mgr_with_playlists(["pl_a"])
        mgr.tracks.return_value = [_make_track("t1", "One")]
        assert find_playlist_for_track(mgr, "missing") == ""

    def test_tolerates_playlist_fetch_errors(self) -> None:
        mgr = self._mgr_with_playlists(["pl_broken", "pl_ok"])
        mgr.tracks.side_effect = [RuntimeError("boom"), [_make_track("target", "Target")]]
        assert find_playlist_for_track(mgr, "target") == "pl_ok"


class TestUpvoteCurrentTrack:
    def _mgr_playing(self, track: Track) -> MagicMock:
        mgr = MagicMock()
        mgr.now_playing.return_value = _make_now(track)
        return mgr

    def test_upvotes_with_autodetected_playlist(self) -> None:
        track = _make_track("abc", "Some Song")
        mgr = self._mgr_playing(track)
        mgr.playlists.return_value = [MagicMock(id="pl1")]
        mgr.tracks.return_value = [track]

        store = MagicMock()
        store.upvote.return_value = 4
        result = upvote_current_track(mgr, store)

        assert isinstance(result, UpvoteResult)
        assert result.playlist == "pl1"
        assert result.track is track
        assert result.votes == 4
        store.upvote.assert_called_once_with("pl1", "abc", track)

    def test_nothing_playing_returns_none(self) -> None:
        mgr = MagicMock()
        mgr.now_playing.return_value = None
        store = MagicMock()
        assert upvote_current_track(mgr, store) is None
        store.upvote.assert_not_called()

    def test_explicit_playlist_does_not_scan(self) -> None:
        track = _make_track("abc", "Some Song")
        mgr = self._mgr_playing(track)

        store = MagicMock()
        store.upvote.return_value = 1
        result = upvote_current_track(mgr, store, playlist="pl_explicit")

        assert result.playlist == "pl_explicit"
        store.upvote.assert_called_once_with("pl_explicit", "abc", track)
        mgr.playlists.assert_not_called()


class TestWaybarCli:
    """The CLI wrappers emit exactly the right stdout and drive the mocks."""

    def _args(self, **kwargs) -> argparse.Namespace:
        defaults = {"json": False, "playlist": ""}
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def _mgr_playing(self, track: Track) -> MagicMock:
        mgr = MagicMock()
        mgr.now_playing.return_value = _make_now(track)
        return mgr

    def test_now_json_prints_only_json(self, capsys: pytest.CaptureFixture) -> None:
        from spotify_playlist_manager.cli import cmd_waybar_now

        mgr = self._mgr_playing(_make_track("abc", "Some Song", "Some Artist"))
        cmd_waybar_now(mgr, self._args(json=True))

        captured = capsys.readouterr()
        assert captured.err == ""
        data = json.loads(captured.out.strip())
        assert data["class"] == "playing"
        assert "Some Song" in data["text"]

    def test_now_plain_prints_human_line(self, capsys: pytest.CaptureFixture) -> None:
        from spotify_playlist_manager.cli import cmd_waybar_now

        mgr = self._mgr_playing(_make_track("abc", "Some Song", "Some Artist"))
        cmd_waybar_now(mgr, self._args(json=False))

        captured = capsys.readouterr()
        assert "Some Song" in captured.out.strip()
        assert len(captured.out.strip().splitlines()) == 1

    def test_upvote_json_mode(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from spotify_playlist_manager import cli as cli_mod

        track = _make_track("abc", "Some Song", "Some Artist")
        mgr = self._mgr_playing(track)
        mgr.playlists.return_value = [MagicMock(id="pl1")]
        mgr.tracks.return_value = [track]

        store = MagicMock()
        store.upvote.return_value = 2
        monkeypatch.setattr("spotify_playlist_manager.votes.VoteStore", lambda *a, **k: store)

        cli_mod.cmd_waybar_upvote(mgr, self._args(json=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["text"] == "♥ 2"
        assert data["class"] == "upvoted"
        # exec mode performed the upvote against the auto-detected playlist
        store.upvote.assert_called_once_with("pl1", "abc", track)

    def test_upvote_on_click_mode(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from spotify_playlist_manager import cli as cli_mod

        track = _make_track("abc", "Some Song", "Some Artist")
        mgr = self._mgr_playing(track)

        store = MagicMock()
        store.upvote.return_value = 1
        monkeypatch.setattr("spotify_playlist_manager.votes.VoteStore", lambda *a, **k: store)

        cli_mod.cmd_waybar_upvote(mgr, self._args(json=False, playlist="pl_click"))
        out = capsys.readouterr().out.strip()
        assert "Some Song" in out
        assert "1 vote" in out
        store.upvote.assert_called_once_with("pl_click", "abc", track)

    def test_upvote_nothing_playing(self, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from spotify_playlist_manager import cli as cli_mod

        mgr = MagicMock()
        mgr.now_playing.return_value = None
        store = MagicMock()
        monkeypatch.setattr("spotify_playlist_manager.votes.VoteStore", lambda *a, **k: store)

        cli_mod.cmd_waybar_upvote(mgr, self._args(json=True))
        data = json.loads(capsys.readouterr().out.strip())
        assert data["text"] == "♡"
        assert data["class"] == "not-upvoted"
        store.upvote.assert_not_called()