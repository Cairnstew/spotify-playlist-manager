"""Spotify Playlist Manager.

A thin, friendly wrapper around the Spotify Web API that just needs your
credentials and gives you easy functions/tools to read and manipulate
playlists.
"""

from __future__ import annotations

import logging

from .auth import (
    client_from_credentials,
    client_from_env,
)
from .client import PlaylistManager
from .errors import (
    AuthenticationError,
    ConfigurationError,
    PlaylistNotFoundError,
    SpotifyPlaylistError,
    TrackNotFoundError,
    TrackReferenceError,
)
from .models import NowPlaying, Playlist, Track
from .player import blocks_until_change, watch_now_playing
from .rate_limit import RateLimitGuard, retry_after_seconds
from .stream import ConnectDevice, find_librespot

__version__ = "0.1.0"

# Library-safe default: attach a NullHandler so that importing the package
# never configures logging or emits output.  Call setup_logging() from the
# CLI entry-point (or your application) to attach real handlers.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "PlaylistManager",
    "Playlist",
    "Track",
    "NowPlaying",
    "watch_now_playing",
    "blocks_until_change",
    "ConnectDevice",
    "find_librespot",
    "client_from_env",
    "client_from_credentials",
    "RateLimitGuard",
    "retry_after_seconds",
    "AuthenticationError",
    "ConfigurationError",
    "PlaylistNotFoundError",
    "SpotifyPlaylistError",
    "TrackNotFoundError",
    "TrackReferenceError",
    "__version__",
]