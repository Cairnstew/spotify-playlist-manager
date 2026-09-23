"""Authentication: turn credentials into an authenticated Spotify client.

The wrapper only needs ONE thing from you to do everything —
a `PlaylistManager` built from environment variables via :func:`client_from_env`:

    export SPOTIFY_CLIENT_ID=...
    export SPOTIFY_CLIENT_SECRET=...      # not needed for PKCE, see below
    export SPOTIFY_REDIRECT_URI=http://localhost:8888/callback

Three flows are supported:

* ``"user"`` (default) — Authorization Code with PKCE. Opens your browser,
  asks you (the account owner) to authorize, and gets a *user* token. This is
  the flow for anything that reads or modifies your playlists. Only needs a
  client id and redirect URI (PKCE deliberately sends no secret; Spotify
  dashboard > Edit Settings must list your redirect URI).
* ``"app"`` — Client Credentials. No user involved; good for public data,
  search, and read-only server-side jobs. Needs client id + secret.
* ``"token"`` — takes an already-obtained access token string directly; a
  client is returned with no auth manager at all.

The token is cached to disk (``.spotify-cache`` by default) and refreshing
happens automatically on expiry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import CacheFileHandler

from .errors import AuthenticationError, ConfigurationError
from .logging_config import log_event

_AUTH_LOG = logging.getLogger("spotify_playlist_manager.auth")

ENV_CLIENT_ID = "SPOTIFY_CLIENT_ID"
ENV_CLIENT_SECRET = "SPOTIFY_CLIENT_SECRET"
ENV_REDIRECT_URI = "SPOTIFY_REDIRECT_URI"
ENV_CACHE_PATH = "SPOTIFY_CACHE_PATH"

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8877/callback"
DEFAULT_CACHE_PATH = ".spotify-cache"

# Enough scope to build + edit your own playlists, plus read live playback;
# widen as needed.
DEFAULT_SCOPE = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public "
    "user-library-read user-library-modify "
    "user-read-currently-playing user-read-playback-state"
)

load_dotenv()


def _env_or_raise(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"Missing {name}. Set it in your environment or .env file — "
            "copy .env.example and fill it in."
        )
    return value


def _cache_handler(cache_path: str | Path) -> CacheFileHandler:
    return CacheFileHandler(cache_path=str(cache_path))


def client_from_env(flow: str = "user", **kwargs: Any) -> spotipy.Spotify:
    """Build an authenticated client from the environment (or a .env file).

    Parameters
    ----------
    flow:
        ``"user"`` (Authorization Code/PKCE, default), ``"app"`` (Client
        Credentials), or ``"token"`` (raw access token in
        ``SPOTIFY_ACCESS_TOKEN``).
    """
    log_event(_AUTH_LOG, "auth.init", flow=flow)

    if flow == "token":
        token = os.environ.get("SPOTIFY_ACCESS_TOKEN", "").strip()
        if not token:
            raise AuthenticationError(
                "flow='token' requires SPOTIFY_ACCESS_TOKEN in the environment."
            )
        log_event(_AUTH_LOG, "auth.token_used")
        return spotipy.Spotify(auth=token)

    client_id = _env_or_raise(ENV_CLIENT_ID)

    if flow == "app":
        client_secret = _env_or_raise(ENV_CLIENT_SECRET)
        auth_manager = spotipy.SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        log_event(_AUTH_LOG, "auth.client_credentials")
        return spotipy.Spotify(auth_manager=auth_manager)

    # Default: user-scoped PKCE flow. PKCE is a public-client flow — no
    # client secret is sent (and SpotifyPKCE 2.26 does not accept one).
    redirect_uri = os.environ.get(ENV_REDIRECT_URI, DEFAULT_REDIRECT_URI)
    cache_path = os.environ.get(ENV_CACHE_PATH, DEFAULT_CACHE_PATH)
    scope = kwargs.get("scope", os.environ.get("SPOTIFY_SCOPE", DEFAULT_SCOPE))

    # Log whether the cache exists (token reuse vs fresh auth).
    cache_exists = Path(cache_path).exists()
    log_event(
        _AUTH_LOG,
        "auth.pkce_init",
        cache_exists=cache_exists,
        redirect_uri=redirect_uri,
    )

    auth_manager = spotipy.SpotifyPKCE(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_handler=_cache_handler(cache_path),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def client_from_credentials(
    client_id: str,
    client_secret: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scope: str = DEFAULT_SCOPE,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    flow: str = "user",
) -> spotipy.Spotify:
    """Build an authenticated client from explicit credential values.

    Same behavior as :func:`client_from_env` but driven by parameters rather
    than the environment — useful for embedding in scripts and apps. For the
    ``"user"`` flow only ``client_id`` and ``redirect_uri`` are used (PKCE);
    ``client_secret`` is required for the ``"app"`` flow.
    """
    if flow == "app":
        auth_manager = spotipy.SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        return spotipy.Spotify(auth_manager=auth_manager)

    auth_manager = spotipy.SpotifyPKCE(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_handler=_cache_handler(cache_path),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)