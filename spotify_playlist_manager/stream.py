"""Streaming playback via a librespot Spotify Connect device.

The Spotify Web API is metadata-only — it can never hand you audio bytes.
Real playback ("play this like the normal app does", "appear as a separate
device") works over *Spotify Connect*, and the clean open-source way to get a
Connect device on a desktop/headless box is ``librespot`` (in nixpkgs). It
registers a device under your account that the phone/desktop Spotify apps and
the Web API all see, then actually plays audio to your sound card.

This module has two cooperating parts:

* :class:`ConnectDevice` — launches and tracks a ``librespot`` subprocess so
  a device with your chosen name shows up in the account.
* ``PlaylistManager.stream_*``  (see :mod:`spotify_playlist_manager.client`)
  — drives that device through the Web API (start/pause/next/volume/...),
  which is exactly the "the API understands it's a separate device" bit.

Typical flow::

    from spotify_playlist_manager import PlaylistManager
    from spotify_playlist_manager.stream import ConnectDevice, find_librespot

    mgr = PlaylistManager.from_env()
    device = ConnectDevice(name="kitchen-speaker")
    device.start()                       # spawn librespot, wait to appear
    mgr.play_on_device(device.device_id, uris=["spotify:track:..."])
    mgr.pause_device(device.device_id)
    device.stop()

Deck / prerequisites
--------------------
* ``librespot`` must be on PATH (``nix develop`` provides it; otherwise
  install it separately).
* Streaming requires a **Spotify Premium** account — librespot will refuse a
  free account.
* Authentication: librespot uses real-account credentials, not your API app
  secret. Pass ``username``/``password`` (interactive is supported too: run
  the binary yourself once with ``--username X --password Y`` and it caches
  the credentials for later runs).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .errors import SpotifyPlaylistError
from .logging_config import RedactionFilter, log_event

_STREAM_LOG = logging.getLogger("spotify_playlist_manager.stream")

DEFAULT_DEVICE_NAME = "Spm-Connect"
DEFAULT_BACKEND = "pulseaudio"
STARTUP_WAIT = 60.0          # worst-case seconds to wait for device registration
POLL_INTERVAL = 1.5


def find_librespot() -> str:
    """Return the ``librespot`` binary path, raising a clear error if missing."""
    path = shutil.which("librespot")
    if not path:
        raise SpotifyPlaylistError(
            "librespot is not on PATH. Run `nix develop` (the dev shell provides "
            "it) or install librespot yourself to use streaming."
        )
    return path


@dataclass(slots=True)
class ConnectDevice:
    """A librespot Spotify Connect device managed as a subprocess.

    Parameters
    ----------
    name:
        Device name as it appears in your Spotify apps and the Web API.
    backend:
        librespot audio backend: ``pulseaudio`` (default), ``alsa`` (needs a
        ``device`` on this host), ``rodio``, or ``pipe``.
    username / password:
        Real-account credentials for librespot. If omitted, librespot uses its
        cached credentials (from a previous run).
    """

    name: str = DEFAULT_DEVICE_NAME
    backend: str = DEFAULT_BACKEND
    username: str | None = None
    password: str | None = None
    extra_args: list[str] = field(default_factory=list)
    binary: str = field(default_factory=find_librespot)

    _proc: subprocess.Popen | None = field(default=None, repr=False, init=False)
    _log_fh: object | None = field(default=None, repr=False, init=False)

    _log_path: str = field(default="librespot.log", init=False)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Spawn librespot and wait until its device is registered."""
        if self.running:
            return

        cmd = [
            self.binary,
            "--name", self.name,
            "--backend", self.backend,
            "--disable-audio-cache",
            "-v",
        ]
        if self.username is not None and self.password is not None:
            cmd += ["--username", self.username, "--password", self.password]

        safe_cmd = RedactionFilter.redact_argv(cmd)
        log_event(
            _STREAM_LOG,
            "stream.start",
            device_name=self.name,
            backend=self.backend,
            argv=safe_cmd,
        )

        self._log_fh = open(self._log_path, "a")
        self._proc = subprocess.Popen(
            cmd,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def wait_until_device(self, check_device_id, timeout: float = STARTUP_WAIT) -> str | None:
        """Poll the Web API until ``check_device_id`` reports a device whose
        name matches this device. Returns the device id.

        ``check_device_id`` is a zero-arg callable returning a list of device
        dicts (``[{"id": ..., "name": ...}]``).  Built to be wired to
        ``manager.devices()``.
        """
        start_time = time.monotonic()
        deadline = start_time + timeout
        while time.monotonic() < deadline:
            for dev in check_device_id():
                if dev.get("name") == self.name and dev.get("id"):
                    elapsed = round(time.monotonic() - start_time, 1)
                    log_event(
                        _STREAM_LOG,
                        "stream.device_registered",
                        device_name=self.name,
                        device_id=dev["id"],
                        wait_seconds=elapsed,
                    )
                    return dev["id"]
            time.sleep(POLL_INTERVAL)
        elapsed = round(time.monotonic() - start_time, 1)
        log_event(
            _STREAM_LOG,
            "stream.device_timeout",
            device_name=self.name,
            timeout_seconds=timeout,
            elapsed_seconds=elapsed,
        )
        return None

    def stop(self) -> None:
        """Terminate the librespot process if running."""
        was_running = self._proc is not None and self._proc.poll() is None
        if was_running:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            log_event(
                _STREAM_LOG,
                "stream.stop",
                device_name=self.name,
            )
        self._proc = None
        if self._log_fh is not None:
            fh = self._log_fh
            self._log_fh = None
            fh.close()

    def __enter__(self) -> "ConnectDevice":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()