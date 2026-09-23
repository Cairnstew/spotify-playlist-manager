"""Offline unit tests for streaming infra (ConnectDevice + playback control).

The librespot binary and a live Spotify account are exercised in the run book;
here we verify device discovery, subprocess lifecycle wiring, and that the
manager's stream_* methods translate to the right spotipy calls.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.errors import SpotifyPlaylistError
from spotify_playlist_manager.stream import ConnectDevice, find_librespot


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def devices(self):
        return {"devices": [{"id": "dev1", "name": "Spm-Connect", "is_active": False}]}

    def start_playback(self, device_id=None, context_uri=None, uris=None, offset=None, position_ms=None):
        self.calls.append(("start_playback", device_id, context_uri, uris, offset))

    def pause_playback(self, device_id=None):
        self.calls.append(("pause_playback", device_id))

    def next_track(self, device_id=None):
        self.calls.append(("next_track", device_id))

    def previous_track(self, device_id=None):
        self.calls.append(("previous_track", device_id))

    def volume(self, volume_percent, device_id=None):
        self.calls.append(("volume", device_id, volume_percent))

    def transfer_playback(self, device_id, force_play=True):
        self.calls.append(("transfer_playback", device_id, force_play))

    def current_playback(self):
        return {"is_playing": True, "item": {"type": "track", "id": "t1", "uri": "spotify:track:t1"}}


@pytest.fixture
def mgr() -> PlaylistManager:
    return PlaylistManager(_FakeClient())  # type: ignore[arg-type]


class TestFindDevice:
    def test_returns_matching_device(self, mgr: PlaylistManager) -> None:
        dev = mgr.find_device("Spm-Connect")
        assert dev is not None
        assert dev["id"] == "dev1"

    def test_none_when_no_match(self, mgr: PlaylistManager) -> None:
        assert mgr.find_device("missing") is None


class TestPlaybackControl:
    def test_play_uris(self, mgr: PlaylistManager) -> None:
        mgr.play_uris("dev1", ["spotify:track:a", "spotify:track:b"])
        assert mgr.client.calls == [("start_playback", "dev1", None, ["spotify:track:a", "spotify:track:b"], None)]  # type: ignore[attr-defined]

    def test_play_context(self, mgr: PlaylistManager) -> None:
        mgr.play_context("dev1", "spotify:playlist:p1", offset="spotify:track:x")
        assert mgr.client.calls == [("start_playback", "dev1", "spotify:playlist:p1", None, "spotify:track:x")]  # type: ignore[attr-defined]

    def test_pause(self, mgr: PlaylistManager) -> None:
        mgr.pause("dev1")
        assert mgr.client.calls == [("pause_playback", "dev1")]  # type: ignore[attr-defined]

    def test_resume(self, mgr: PlaylistManager) -> None:
        mgr.resume("dev1")
        assert mgr.client.calls == [("start_playback", "dev1", None, None, None)]  # type: ignore[attr-defined]

    def test_next_previous(self, mgr: PlaylistManager) -> None:
        mgr.next_track("dev1")
        mgr.previous_track("dev1")
        assert mgr.client.calls == [("next_track", "dev1"), ("previous_track", "dev1")]  # type: ignore[attr-defined]

    def test_volume_clamps(self, mgr: PlaylistManager) -> None:
        mgr.set_volume("dev1", 120)
        assert mgr.client.calls == [("volume", "dev1", 100)]  # type: ignore[attr-defined]
        mgr.set_volume("dev1", -5)
        assert mgr.client.calls[-1] == ("volume", "dev1", 0)  # type: ignore[attr-defined]

    def test_transfer(self, mgr: PlaylistManager) -> None:
        mgr.transfer("dev1")
        assert mgr.client.calls == [("transfer_playback", "dev1", True)]  # type: ignore[attr-defined]


class TestFindLibrespot:
    def test_raises_clear_error_when_missing(self) -> None:
        with mock.patch("spotify_playlist_manager.stream.shutil.which", return_value=None):
            with pytest.raises(SpotifyPlaylistError, match="librespot"):
                find_librespot()

    def test_returns_binary_when_present(self) -> None:
        with mock.patch("spotify_playlist_manager.stream.shutil.which", return_value="/nix/store/librespot/bin/librespot"):
            assert find_librespot() == "/nix/store/librespot/bin/librespot"


class TestConnectDevice:
    def test_command_shape(self) -> None:
        device = ConnectDevice(name="kitchen", backend="pulseaudio", username="u", password="p", binary="/fake/librespot")
        with mock.patch("subprocess.Popen") as popen, mock.patch("builtins.open", mock.mock_open()):
            device.start()
            cmd = popen.call_args.args[0]
        assert cmd[:2] == ["/fake/librespot", "--name"]
        assert "kitchen" in cmd
        assert "--backend" in cmd and "pulseaudio" in cmd
        assert "--username" in cmd and "u" in cmd
        assert "--password" in cmd and "p" in cmd
        assert "--disable-audio-cache" in cmd

    def test_start_is_idempotent(self) -> None:
        device = ConnectDevice(name="x", binary="/fake/librespot")
        proc = mock.Mock()
        proc.poll.return_value = None
        device._proc = proc
        with mock.patch("subprocess.Popen") as popen:
            device.start()
        popen.assert_not_called()

    def test_stop_terminates_process(self) -> None:
        device = ConnectDevice(name="x", binary="/fake/librespot")
        proc = mock.Mock()
        proc.poll.side_effect = [None, 0]
        proc.wait.return_value = 0
        device._proc = proc
        device._log_fh = mock.Mock()
        device.stop()
        proc.terminate.assert_called_once()

    def test_wait_until_device_polls_until_found(self) -> None:
        device = ConnectDevice(name="Spm-Connect", binary="/fake/librespot")
        # First two polls return no match, third returns the device.
        responses = iter(
            [
                [{"id": "d0", "name": "elsewhere"}],
                [{"id": "d1", "name": "Spm-Connect"}],
            ]
        )
        with mock.patch("spotify_playlist_manager.stream.time.sleep"):
            dev_id = device.wait_until_device(lambda: next(responses), timeout=10)
        assert dev_id == "d1"

    def test_wait_until_device_returns_none_on_timeout(self) -> None:
        device = ConnectDevice(name="never", binary="/fake/librespot")
        with mock.patch("spotify_playlist_manager.stream.time.sleep"):
            dev_id = device.wait_until_device(lambda: [], timeout=2)
        assert dev_id is None