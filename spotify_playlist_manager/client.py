"""The high-level facade: everything you can do to a playlist, simply.

Construct one from a spotipy client (see :mod:`spotify_playlist_manager.auth`)::

    from spotify_playlist_manager import PlaylistManager
    manager = PlaylistManager.from_env()          # uses SPOTIFY_* env vars

    mgr.create_playlist("Morning Run")
    share = mgr.create_playlist("Shared", public=True, description="For friends")
    mgr.add_tracks(share, [
        "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "spotify:track:7ouMYWpwJ422jRcDASZB7P",
        "0qanabqE3D3LmvKoP30H9q",               # bare Spotify track id
    ])
    mgr.playlist("Shared")                        # -> Playlist(name="Shared", ...)
    mgr.remove_tracks(share, ["spotify:track:7ouMYWpwJ422jRcDASZB7P"])
    mgr.clear(share)

Track references may be full URLs, spotify URIs, or bare IDs — handled
consistently everywhere.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

import spotipy

from .errors import PlaylistNotFoundError
from .logging_config import log_event
from .models import NowPlaying, Playlist, Track
from .utils import chunk, playlist_id_from_ref, track_uri_from_ref

_API_LOG = logging.getLogger("spotify_playlist_manager.api")
_USER_LOG = logging.getLogger("spotify_playlist_manager.user")


def _items_from_results(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the ``items`` list out of any paged Spotify response."""
    # Responses come in two shapes: {"items": [...]} or {"tracks": {"items": [...]}}.
    for key in ("items", "tracks"):
        value = results.get(key)
        if isinstance(value, dict) and "items" in value:
            return value["items"]
        if isinstance(value, list):
            return value
    return []


def _endpoint_from_url(url: str) -> str:
    """Strip the base URL prefix and auth params, leaving just the path."""
    # spotipy prepends "https://api.spotify.com/v1" — strip that.
    for prefix in ("https://api.spotify.com", "http://api.spotify.com"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    # Strip query string for cleaner logging.
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


class _ApiProxy:
    """Thin proxy around ``spotipy.Spotify`` that logs every HTTP call.

    Intercepts ``_internal_call`` — the single dispatch point for all
    spotipy API methods — to record method, endpoint, response status,
    latency, and errors.  All other attribute access is forwarded to the
    underlying ``Spotify`` instance.
    """

    def __init__(self, client: spotipy.Spotify) -> None:
        object.__setattr__(self, "_client", client)

    # -- Proxy magic methods ------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_client"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_client"), name, value)

    def __repr__(self) -> str:
        return f"<ApiProxy wrapping {object.__getattribute__(self, '_client')!r}>"

    # -- Intercept the internal dispatch ------------------------------------

    def _internal_call(self, method: str, url: str, payload: Any, params: Any) -> Any:
        client = object.__getattribute__(self, "_client")
        endpoint = _endpoint_from_url(url)
        start = time.monotonic()

        try:
            result = client._internal_call(method, url, payload, params)
            elapsed_ms = round((time.monotonic() - start) * 1000)
            log_event(
                _API_LOG,
                f"{method} {endpoint}",
                http_method=method,
                endpoint=endpoint,
                status="ok",
                latency_ms=elapsed_ms,
            )
            return result
        except spotipy.SpotifyException as exc:
            elapsed_ms = round((time.monotonic() - start) * 1000)
            log_event(
                _API_LOG,
                f"{method} {endpoint}",
                http_method=method,
                endpoint=endpoint,
                status="error",
                latency_ms=elapsed_ms,
                http_status=exc.http_status,
                reason=exc.reason,
            )
            raise


class PlaylistManager:
    """Friendly wrapper around a spotipy client, focused on playlists.

    All methods accept playlist *references* (URL / URI / bare id) and track
    references in the same flexible forms.

    Parameters
    ----------
    client:
        An authenticated ``spotipy.Spotify`` instance — normally produced by
        :func:`~spotify_playlist_manager.auth.client_from_env`.
    """

    def __init__(self, client: spotipy.Spotify) -> None:
        self.client = _ApiProxy(client)

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, flow: str = "user", **kwargs: Any) -> "PlaylistManager":
        """Build a manager from ``SPOTIFY_*`` environment variables.

        See :func:`spotify_playlist_manager.auth.client_from_env` for the
        ``flow`` argument and which variables are required.
        """
        from .auth import client_from_env

        return cls(client_from_env(flow=flow, **kwargs))

    @classmethod
    def from_credentials(cls, client_id: str, client_secret: str, **kwargs: Any) -> "PlaylistManager":
        """Build a manager from explicit credentials (see auth module)."""
        from .auth import client_from_credentials

        return cls(client_from_credentials(client_id, client_secret, **kwargs))

    # ------------------------------------------------------------------ #
    # Account / identity
    # ------------------------------------------------------------------ #

    def me(self) -> dict[str, Any]:
        """Return the authenticated user's Spotify profile dict."""
        return self.client.me()

    def user_id(self) -> str:
        """Return the authenticated user's Spotify id."""
        return self.me().get("id", "")

    # ------------------------------------------------------------------ #
    # Reading playlists
    # ------------------------------------------------------------------ #

    def playlists(self) -> list[Playlist]:
        """Return a flattened list of the current user's playlists."""
        all_items: list[dict[str, Any]] = []
        offset = 0
        limit = 50
        while True:
            results = self.client.current_user_playlists(limit=limit, offset=offset)
            items = _items_from_results(results)
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += limit
        return [Playlist.from_spotify(item) for item in all_items]

    def playlist(self, ref: str, fetch_tracks: bool = False) -> Playlist:
        """Fetch one playlist by URL, URI, or bare id.

        Parameters
        ----------
        fetch_tracks:
            If True, also populate ``Playlist.tracks``.
        """
        playlist_id = playlist_id_from_ref(ref)
        try:
            data = self.client.playlist(playlist_id)
        except Exception as exc:  # spotipy raises spotipy.exceptions.SpotifyException
            raise PlaylistNotFoundError(f"Playlist not found: {ref!r}") from exc

        playlist = Playlist.from_spotify(data)
        if fetch_tracks:
            playlist.tracks = self.tracks(ref)
        return playlist

    def tracks(self, ref: str, limit: int | None = None) -> list[Track]:
        """Return all tracks currently in a playlist (in order).

        ``limit`` caps the number returned if provided.
        """
        playlist_id = playlist_id_from_ref(ref)
        items: list[dict[str, Any]] = []
        offset = 0
        page_size = 100
        while True:
            remaining = None if limit is None else limit - len(items)
            if remaining is not None and remaining <= 0:
                break
            take = min(page_size, remaining) if remaining is not None else page_size
            results = self.client.playlist_tracks(playlist_id, limit=take, offset=offset)
            page = _items_from_results(results)
            items.extend(page)
            if len(page) < take:
                break
            offset += page_size
        return [Track.from_spotify(item) for item in items]

    # ------------------------------------------------------------------ #
    # Creating / editing playlists
    # ------------------------------------------------------------------ #

    def create_playlist(
        self,
        name: str,
        public: bool = True,
        collaborative: bool = False,
        description: str = "",
    ) -> Playlist:
        """Create a playlist owned by the authenticated user and return it."""
        data = self.client.current_user_playlist_create(
            name,
            public=public,
            collaborative=collaborative,
            description=description,
        )
        playlist = Playlist.from_spotify(data)
        log_event(
            _USER_LOG,
            "playlist.create",
            playlist_id=playlist.id,
            playlist_name=playlist.name,
            public=public,
        )
        return playlist

    def update_playlist(
        self,
        ref: str,
        name: str | None = None,
        public: bool | None = None,
        collaborative: bool | None = None,
        description: str | None = None,
    ) -> Playlist:
        """Change a playlist's name / visibility / description.

        Only the arguments given are changed. Returns the refreshed playlist.
        """
        playlist_id = playlist_id_from_ref(ref)
        self.client.playlist_change_details(
            playlist_id,
            name=name,
            public=public,
            collaborative=collaborative,
            description=description,
        )
        updated = self.playlist(playlist_id)
        log_event(
            _USER_LOG,
            "playlist.update",
            playlist_id=playlist_id,
            playlist_name=updated.name,
            **{"name": name} if name is not None else {},
        )
        return updated

    def rename(self, ref: str, name: str) -> Playlist:
        """Rename a playlist and return the refreshed playlist."""
        return self.update_playlist(ref, name=name)

    def delete(self, ref: str) -> None:
        """Unfollow/delete a playlist for the current user."""
        playlist_id = playlist_id_from_ref(ref)
        self.client.current_user_unfollow_playlist(playlist_id)
        log_event(_USER_LOG, "playlist.delete", playlist_id=playlist_id)

    # ------------------------------------------------------------------ #
    # Mutating track contents
    # ------------------------------------------------------------------ #

    def add_tracks(
        self,
        ref: str,
        tracks: Iterable[str],
        position: int | None = None,
        dedupe: bool = False,
    ) -> None:
        """Add tracks to a playlist.

        ``tracks`` may be any iterable of track URLs / URIs / bare IDs. The
        Spotify API caps a single call at 100 tracks, so larger batches are
        chunked automatically.

        Parameters
        ----------
        position:
            Optional insertion index (0-based).
        dedupe:
            If True, drop tracks already in the playlist before adding.
        """
        uris = [track_uri_from_ref(t) for t in tracks]
        if dedupe:
            existing = {t.uri for t in self.tracks(ref)}
            uris = [uri for uri in uris if uri not in existing]
        if not uris:
            return

        playlist_id = playlist_id_from_ref(ref)
        total_added = 0
        for batch in chunk(uris, size=100):
            self.client.playlist_add_items(
                playlist_id, batch, position=position
            )
            total_added += len(batch)

        log_event(
            _USER_LOG,
            "tracks.add",
            playlist_id=playlist_id,
            track_count=total_added,
            position=position,
        )

    def remove_tracks(self, ref: str, tracks: Iterable[str]) -> None:
        """Remove all occurrences of the given tracks from a playlist."""
        uris = [track_uri_from_ref(t) for t in tracks]
        if not uris:
            return
        playlist_id = playlist_id_from_ref(ref)
        for batch in chunk(uris, size=100):
            self.client.playlist_remove_all_occurrences_of_items(playlist_id, batch)
        log_event(
            _USER_LOG,
            "tracks.remove",
            playlist_id=playlist_id,
            track_count=len(uris),
        )

    def replace_tracks(self, ref: str, tracks: Iterable[str]) -> None:
        """Replace the entire playlist contents with the given tracks."""
        uris = [track_uri_from_ref(t) for t in tracks]
        playlist_id = playlist_id_from_ref(ref)
        self.client.playlist_replace_items(playlist_id, uris)
        log_event(
            _USER_LOG,
            "tracks.replace",
            playlist_id=playlist_id,
            track_count=len(uris),
        )

    def clear(self, ref: str) -> None:
        """Remove every track from a playlist."""
        self.replace_tracks(ref, [])

    def reorder_tracks(
        self,
        ref: str,
        range_start: int,
        insert_before: int,
        range_length: int = 1,
    ) -> None:
        """Move ``range_length`` tracks starting at ``range_start`` to just
        before ``insert_before`` (0-based indices)."""
        playlist_id = playlist_id_from_ref(ref)
        self.client.playlist_reorder_items(
            playlist_id,
            range_start,
            insert_before,
            range_length=range_length,
        )
        log_event(
            _USER_LOG,
            "tracks.reorder",
            playlist_id=playlist_id,
            range_start=range_start,
            insert_before=insert_before,
            range_length=range_length,
        )

    # ------------------------------------------------------------------ #
    # Moving / reordering single items
    # ------------------------------------------------------------------ #

    def track_position(self, ref: str, track_ref: str) -> int:
        """Return the 0-based index of the first occurrence of a track.

        ``track_ref`` may be any track reference form (URL / URI / bare ID).
        Raises :class:`TrackNotFoundError` if the track is not in the playlist.
        """
        from .errors import TrackNotFoundError

        uri = track_uri_from_ref(track_ref)
        for index, track in enumerate(self.tracks(ref)):
            if track.uri == uri:
                return index
        raise TrackNotFoundError(f"Track {track_ref!r} is not in playlist {ref!r}")

    def move_track(self, ref: str, track: int | str, new_index: int) -> int:
        """Move a single track to ``new_index`` (0-based, final position).

        ``track`` may be an integer index or any track reference form; when a
        reference is given its current position is looked up first. Returns
        the index the track ended up at after clamping.

        The Spotify API computes ``insert_before`` differently depending on
        direction (a downward move targets ``new_index + 1`` because the item
        is removed from the list first); this method applies that rule.
        """
        tracks = self.tracks(ref)
        total = len(tracks)
        if total == 0:
            return 0

        old_index = track if isinstance(track, int) else self.track_position(ref, track)
        # Clamp to valid range.
        old_index = max(0, min(old_index, total - 1))
        new_index = max(0, min(new_index, total - 1))
        if old_index == new_index:
            return new_index

        # After the moved range is removed, a target below the old slot is an
        # insert position in the survivors; a target above needs +1 to land in
        # the same place the untouched item at that index now occupies.
        insert_before = new_index + 1 if new_index > old_index else new_index
        self.reorder_tracks(
            ref, range_start=old_index, insert_before=insert_before, range_length=1
        )
        return new_index

    def move_up(self, ref: str, track: int | str, amount: int = 1) -> int:
        """Move a track earlier in the playlist by ``amount`` slots.

        ``track`` may be an index or any track reference form. Returns the
        new index.
        """
        index = track if isinstance(track, int) else self.track_position(ref, track)
        return self.move_track(ref, index, index - max(amount, 0))

    def move_down(self, ref: str, track: int | str, amount: int = 1) -> int:
        """Move a track later in the playlist by ``amount`` slots.

        ``track`` may be an index or any track reference form. Returns the
        new index.
        """
        index = track if isinstance(track, int) else self.track_position(ref, track)
        return self.move_track(ref, index, index + max(amount, 0))

    def move_to_top(self, ref: str, track: int | str) -> int:
        """Move a track to the top of the playlist. Returns the new index (0)."""
        return self.move_track(ref, track, 0)

    def move_to_bottom(self, ref: str, track: int | str) -> int:
        """Move a track to the bottom of the playlist.

        Returns its new index (the last position).
        """
        last = len(self.tracks(ref)) - 1
        return self.move_track(ref, track, last)

    # ------------------------------------------------------------------ #
    # Finding tracks
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Live playback
    # ------------------------------------------------------------------ #

    def now_playing(self) -> NowPlaying | None:
        """Return a :class:`NowPlaying` snapshot of the current playback.

        Returns ``None`` when nothing is playing on any device (the Web API
        answers ``204 No Content`` in that case, which spotipy surfaces as
        ``None``). Requires the ``user-read-currently-playing`` and
        ``user-read-playback-state`` scopes.
        """
        data = self.client.current_playback()
        if not data:
            return None
        return NowPlaying.from_spotify(data)

    def devices(self) -> list[dict[str, Any]]:
        """Return the user's available playback devices."""
        data = self.client.devices() or {}
        return data.get("devices", [])

    # ------------------------------------------------------------------ #
    # Streaming playback (drive a Spotify Connect device via the Web API)
    #
    # The Web API itself never streams audio; it *controls* a Connect device
    # (a phone, the desktop app, or a librespot process — see stream.py) by
    # device id. Every method here takes a device_id; find it from devices().
    # Streaming requires a Premium account.
    # ------------------------------------------------------------------ #

    def find_device(self, name: str) -> dict[str, Any] | None:
        """Return the first enabled device whose name matches ``name``."""
        for dev in self.devices():
            if dev.get("name") == name:
                return dev
        return None

    def play_uris(self, device_id: str, uris: list[str]) -> None:
        """Start playback of the given track/episode URIs on a device."""
        self.client.start_playback(device_id=device_id, uris=uris)
        log_event(
            _USER_LOG,
            "playback.play_uris",
            device_id=device_id,
            track_count=len(uris),
        )

    def play_context(self, device_id: str, context_uri: str, offset: str | None = None) -> None:
        """Start playing a context (playlist/album/artist) on a device.

        ``offset`` optionally selects a starting track URI within the context.
        """
        self.client.start_playback(
            device_id=device_id, context_uri=context_uri, offset=offset
        )
        log_event(
            _USER_LOG,
            "playback.play_context",
            device_id=device_id,
            context_uri=context_uri,
        )

    def pause(self, device_id: str) -> None:
        """Pause playback on a device."""
        self.client.pause_playback(device_id=device_id)
        log_event(_USER_LOG, "playback.pause", device_id=device_id)

    def resume(self, device_id: str) -> None:
        """Resume (start, keeping the active context) playback on a device."""
        # start_playback with no uris/context resumes the current playback
        # context on the active device. If more than one device is active,
        # spotify may need the device_id — we pass it nonetheless.
        self.client.start_playback(device_id=device_id)
        log_event(_USER_LOG, "playback.resume", device_id=device_id)

    def next_track(self, device_id: str) -> None:
        """Skip to the next track on a device."""
        self.client.next_track(device_id=device_id)
        log_event(_USER_LOG, "playback.next", device_id=device_id)

    def previous_track(self, device_id: str) -> None:
        """Skip to the previous track on a device."""
        self.client.previous_track(device_id=device_id)
        log_event(_USER_LOG, "playback.previous", device_id=device_id)

    def set_volume(self, device_id: str, volume_percent: int) -> None:
        """Set playback volume (0-100) on a device."""
        clamped = max(0, min(100, volume_percent))
        self.client.volume(clamped, device_id=device_id)
        log_event(_USER_LOG, "playback.volume", device_id=device_id, volume_percent=clamped)

    def transfer(self, device_id: str, force_play: bool = True) -> None:
        """Transfer playback to a device (the "play on this device" action).

        This is precisely what makes the device discoverable/controllable from
        the normal Spotify apps: the account now has an active Connect device.
        """
        self.client.transfer_playback(device_id, force_play=force_play)
        log_event(_USER_LOG, "playback.transfer", device_id=device_id)

    def state(self, device_id: str | None = None) -> dict[str, Any] | None:
        """Return the full current-playback state dict for the active device.

        ``device_id`` identifies the *target* device (the one to report on);
        if None, reports the currently active device(s). None if nothing is
        playing anywhere.
        """
        data = self.client.current_playback()
        return data or None

    def search(self, query: str, limit: int = 10) -> list[Track]:
        """Search Spotify for tracks matching ``query``."""
        results = self.client.search(query, limit=limit, type="track")
        return [Track.from_spotify(item) for item in _items_from_results(results)]

    def resolve(self, ref: str) -> Track:
        """Resolve a single track reference (URL / URI / bare id / search
        query) to a :class:`Track`.

        A non-URI string that is not a valid Spotify id is treated as a search
        query and the top match is returned.
        """
        from .errors import TrackNotFoundError, TrackReferenceError

        try:
            uri = track_uri_from_ref(ref)
        except TrackReferenceError:
            results = self.search(ref, limit=1)
            if not results:
                raise TrackNotFoundError(f"No track found for query {ref!r}") from None
            return results[0]

        data = self.client.track(uri)
        if not data:
            raise TrackNotFoundError(f"No track found for reference {ref!r}")
        return Track.from_spotify(data)
