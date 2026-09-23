"""Exception hierarchy for the Spotify Playlist Manager wrapper."""

from __future__ import annotations


class SpotifyPlaylistError(Exception):
    """Base class for all errors raised by this package."""


class AuthenticationError(SpotifyPlaylistError):
    """Raised when credentials are missing, invalid, or a token cannot be obtained."""


class ConfigurationError(SpotifyPlaylistError):
    """Raised when required environment/config values are missing."""


class PlaylistNotFoundError(SpotifyPlaylistError):
    """Raised when a playlist cannot be found on the Spotify account."""


class TrackNotFoundError(SpotifyPlaylistError):
    """Raised when a track reference cannot be resolved to a real track."""


class TrackReferenceError(SpotifyPlaylistError):
    """Raised when a track reference is in an unrecognized format."""