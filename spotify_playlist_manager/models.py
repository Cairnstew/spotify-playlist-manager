"""Typed views of the Spotify objects this wrapper deals with.

The Spotify Web API returns big nested JSON; these dataclasses expose the
fields playlist code actually uses, and ``from_spotify`` classmethods convert
raw API dicts into them. They are deliberately plain dataclasses so callers
can construct them freely in tests and offline tooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _artist_names(artists: Any) -> list[str]:
    if not artists:
        return []
    return [a.get("name", "") for a in artists]  # type: ignore[union-attr]


@dataclass(slots=True)
class Track:
    """A single Spotify track (or a reference to one).

    ``uri`` is the canonical ``spotify:track:<id>`` form used by all
    playlist-mutation API calls.
    """

    id: str
    name: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    uri: str = ""
    duration_ms: int = 0

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """Track payloads come wrapped differently by each Spotify endpoint.

        * Search/me responses are flat: ``{type: "track", ...}``.
        * "Liked songs" and older playlist endpoints wrap: ``{track: {...}}``.
        * Current playlist items wrap: ``{item: {...}}`` (with ``track`` and
          ``episode`` kept as booleans next to it — never treat those as the
          payload).
        """
        if isinstance(payload, dict):
            for key in ("item", "track"):
                candidate = payload.get(key)
                if isinstance(candidate, dict):
                    return candidate
        return payload

    @classmethod
    def from_spotify(cls, item: dict[str, Any]) -> "Track":
        payload = cls._unwrap(item)
        if not isinstance(payload, dict) or payload.get("type") != "track":
            return cls(id="", name="(unknown)", artists=[]) 
        return cls(
            id=payload.get("id") or "",
            name=payload.get("name") or "",
            artists=_artist_names(payload.get("artists")),
            album=(payload.get("album") or {}).get("name", ""),
            uri=payload.get("uri") or "",
            duration_ms=int(payload.get("duration_ms") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict that ``json.dumps`` can handle directly."""
        return {
            "id": self.id,
            "name": self.name,
            "artists": self.artists,
            "album": self.album,
            "uri": self.uri,
            "duration_ms": self.duration_ms,
        }

    def __str__(self) -> str:
        artists = ", ".join(self.artists)
        return f"{self.name} — {artists}" if artists else self.name


@dataclass(slots=True)
class Playlist:
    """A Spotify playlist with enough fields to render and mutate it."""

    id: str
    name: str
    uri: str = ""
    description: str = ""
    owner: str = ""
    public: bool = True
    tracks: list[Track] = field(default_factory=list)
    snapshot_id: str = ""

    @classmethod
    def from_spotify(cls, data: dict[str, Any], tracks: list[Track] | None = None) -> "Playlist":
        owner = ""
        owner_data = data.get("owner")
        if isinstance(owner_data, dict):
            owner = owner_data.get("display_name") or owner_data.get("id") or ""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            uri=data.get("uri") or "",
            description=data.get("description") or "",
            owner=owner,
            public=bool(data.get("public", True)),
            tracks=tracks if tracks is not None else [],
            snapshot_id=data.get("snapshot_id") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict, including any loaded ``tracks``."""
        return {
            "id": self.id,
            "name": self.name,
            "uri": self.uri,
            "description": self.description,
            "owner": self.owner,
            "public": self.public,
            "snapshot_id": self.snapshot_id,
            "tracks": [track.to_dict() for track in self.tracks],
        }

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


@dataclass(slots=True)
class NowPlaying:
    """The authenticated user's live playback snapshot.

    Maps the Web API ``/me/player`` response: the current track plus what is
    happening around it (device, progress, shuffle, repeat).
    """

    track: Track
    is_playing: bool = False
    progress_ms: int = 0
    device_name: str = ""
    device_type: str = ""
    volume_percent: int = 0
    shuffle: bool = False
    repeat: str = "off"  # "off" | "context" | "track"

    @classmethod
    def from_spotify(cls, data: dict[str, Any]) -> "NowPlaying":
        device = data.get("device") or {}
        return cls(
            track=Track.from_spotify(data.get("item") or {}),
            is_playing=bool(data.get("is_playing")),
            progress_ms=int(data.get("progress_ms") or 0),
            device_name=device.get("name", ""),
            device_type=device.get("type", ""),
            volume_percent=int(device.get("volume_percent") or 0),
            shuffle=bool(data.get("shuffle_state")),
            repeat=str(data.get("repeat_state") or "off"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track.to_dict(),
            "is_playing": self.is_playing,
            "progress_ms": self.progress_ms,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "volume_percent": self.volume_percent,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
        }

    def __str__(self) -> str:
        state = "▶" if self.is_playing else "⏸"
        return f"{state} {self.track}  [{self.progress_ms}ms]"