"""Small helpers for parsing Spotify references and batching API calls.

The Spotify Web API identifies tracks by 22-char base62 IDs and exposes them
through three equivalent representations:

    https://open.spotify.com/track/<id>?si=<token>
    spotify:track:<id>
    <id>                                           (bare form)

Every reference form is accepted anywhere this package accepts a track.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from .errors import TrackReferenceError

_SPOTIFY_URL_RE = re.compile(
    r"https?://open\.spotify\.com/(?P<kind>track|playlist|album)/(?P<id>[A-Za-z0-9]+)"
)
_SPOTIFY_URI_RE = re.compile(r"spotify:(?P<kind>track|playlist|album):(?P<id>[A-Za-z0-9]+)")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


def track_id_from_ref(ref: str) -> str:
    """Extract a Spotify track ID from a URL, URI, or bare ID.

    Raises TrackReferenceError for anything else.
    """
    ref = ref.strip()
    url_match = _SPOTIFY_URL_RE.match(ref)
    if url_match and url_match.group("kind") == "track":
        return url_match.group("id")

    uri_match = _SPOTIFY_URI_RE.match(ref)
    if uri_match and uri_match.group("kind") == "track":
        return uri_match.group("id")

    if _BARE_ID_RE.fullmatch(ref):
        return ref

    raise TrackReferenceError(
        f"Unrecognized track reference: {ref!r}. "
        "Use a Spotify track URL, 'spotify:track:<id>', or a bare track ID."
    )


def track_uri_from_ref(ref: str) -> str:
    """Return the canonical ``spotify:track:<id>`` URI for any reference form."""
    return f"spotify:track:{track_id_from_ref(ref)}"


def playlist_id_from_ref(ref: str) -> str:
    """Extract a Spotify playlist ID from a URL, URI, or bare ID."""
    ref = ref.strip()
    url_match = _SPOTIFY_URL_RE.match(ref)
    if url_match and url_match.group("kind") == "playlist":
        return url_match.group("id")

    uri_match = _SPOTIFY_URI_RE.match(ref)
    if uri_match and uri_match.group("kind") == "playlist":
        return uri_match.group("id")

    if _BARE_ID_RE.fullmatch(ref):
        return ref

    raise TrackReferenceError(
        f"Unrecognized playlist reference: {ref!r}. "
        "Use a Spotify playlist URL, 'spotify:playlist:<id>', or a bare playlist ID."
    )


def playlist_uri_from_ref(ref: str) -> str:
    """Return the canonical ``spotify:playlist:<id>`` URI for any reference form."""
    return f"spotify:playlist:{playlist_id_from_ref(ref)}"


def chunk(items: Iterable[str], size: int = 100) -> Iterator[list[str]]:
    """Yield an iterable in fixed-size chunks.

    ``size`` defaults to 100, the Spotify API limit for one playlist call
    (both adding and removing tracks).
    """
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch