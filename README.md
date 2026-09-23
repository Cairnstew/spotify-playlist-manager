# Spotify Playlist Manager

A thin, friendly wrapper around the [Spotify Web API](https://developer.spotify.com/documentation/web-api)
focused on **playlist management**. It just needs your credentials, then gives
you small, readable functions to read, create, edit, and delete playlists —
without touching raw API endpoints, pagination, or token refresh yourself.

## Why this exists

The Spotify Web API is powerful but fiddly: three auth flows, token caches,
100-track call limits, five different ways to spell a track ID, nested JSON
everywhere. This package is a *foundation* for projects that expand on
playlist management — **your** logic on top, this wrapper underneath.

## Features

- **One-line authentication** — `PlaylistManager.from_env()` reads
  `SPOTIFY_*` env vars and opens your browser once; tokens are cached and
  refreshed automatically.
- **Flexible references everywhere** — pass a playlist or track as a Spotify
  URL, a `spotify:track:...` URI, or a bare ID.
- **Easy playlist operations** — create, update, rename, delete, list, show,
  clear.
- **Easy track operations** — add (with automatic 100-track chunking),
  remove, replace, reorder, and move-up/move-down/move-to-top/bottom — all by
  index or by any track reference, plus search and single-track resolution.
- **Typed models** — `Playlist`, `Track`, and `NowPlaying` dataclasses with
  sensible `__str__` output and `from_spotify()` conversions.
- **Batteries included** — a tiny CLI (`cli.py`), a Nix dev shell, and a
  live-verification script (`scripts/verify_moves_live.py`) that exercises
  the reorder math against real Spotify before you trust it.

## Setup

Create a Spotify app at <https://developer.spotify.com/dashboard> and note its
Client ID and Client Secret. Under *Edit Settings* add the redirect URI you
use (the default is `http://127.0.0.1:8877/callback`).

Then:

```bash
cp .env.example .env        # fill in SPOTIFY_CLIENT_ID etc.
```

### Nix (recommended here)

```bash
nix develop                # drops you into a shell with python + spotipy
```

### Or plain pip

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```python
from spotify_playlist_manager import PlaylistManager

mgr = PlaylistManager.from_env()          # opens browser the first time only

print(mgr.me()["display_name"])

# Create and fill a playlist
playlist = mgr.create_playlist(
    "Morning Run",
    public=False,
    description="Tempo tracks for a run",
)
mgr.add_tracks(playlist.id, [
    "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
    "spotify:track:7ouMYWpwJ422jRcDASZB7P",
    "0qanabqE3D3LmvKoP30H9q",            # bare track id works too
])

# Move tracks around — by index or by track reference
mgr.move_up(playlist.id, 2)                       # move 3rd song one slot earlier
mgr.move_down(playlist.id, "spotify:track:7ouMYWpwJ422jRcDASZB7P")
mgr.move_track(playlist.id, 0, 4)                 # move first song to 5th
mgr.move_to_top(playlist.id, 3)
mgr.move_to_bottom(playlist.id, "spotify:track:4uLU6hMCjMI75M1A2tKUQC")

# Move a song N slots
mgr.move_up(playlist.id, 4, amount=3)

# Read it back
p = mgr.playlist(playlist.id, fetch_tracks=True)
for track in p.tracks:
    print(track)                          # "Instant Crush — Daft Punk"

# Move the first track to third position
mgr.reorder_tracks(p.id, range_start=0, insert_before=2)

# Dump every playlist + its tracks to JSON (see: export.py)
from spotify_playlist_manager.export import export_playlists_to_file
export_playlists_to_file(mgr, "playlists-export.json")

# Tidy up
mgr.remove_tracks(p.id, ["spotify:track:7ouMYWpwJ422jRcDASZB7P"])
mgr.clear(p.id)
mgr.delete(p.id)
```

### Live playback (currently playing)

Read what the authenticated user is playing right now — including the device,
progress, shuffle and repeat state. Requires the `user-read-currently-playing`
and `user-read-playback-state` scopes (already in the default scope set).

```python
snapshot = mgr.now_playing()          # NowPlaying | None (None = nothing playing)
if snapshot:
    print(snapshot.track)             # "Instant Crush — Daft Punk"
    print(snapshot.device_name)       # "Kitchen"
    print(snapshot.is_playing, snapshot.progress_ms, snapshot.shuffle)
    snapshot.to_dict()                # JSON-ready dict
```

To *watch* it change live (Spotify has no push events; this polls):

```python
from spotify_playlist_manager.player import watch_now_playing, blocks_until_change

for now in watch_now_playing(mgr, interval=3):
    if now is None:
        print("playback stopped")
    else:
        print(now.track)

# Or block until the next track (useful for scripting):
next_track = blocks_until_change(mgr, timeout=300)
```

### Streaming audio (Spotify Connect via librespot)

**Important limitation:** the Spotify Web API is metadata-only — it can never
hand you audio bytes. Real playback ("the same way the normal app does it";
appear as a separate device) works over **Spotify Connect**. This repo wires
up **librespot** as a Connect device and then drives it through the Web API,
so your phone/desktop Spotify apps see a new device and the API can control it.

Prerequisites:
- **Spotify Premium** — required for any Connect streaming (free accounts refuse).
- `librespot` on PATH — `nix develop` provides it.
- librespot logs in with your *real account* (username/password), not the API
  app secret. It caches credentials after the first successful run.

```python
from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.stream import ConnectDevice

mgr = PlaylistManager.from_env()

device = ConnectDevice(name="kitchen-speaker")
device.start()                          # spawn librespot; device appears in apps
device_id = device.wait_until_device(mgr.devices, timeout=60)

mgr.play_uris(device_id, ["spotify:track:4uLU6hMCjMI75M1A2tKUQC"])
mgr.play_context(device_id, "spotify:playlist:<id>")   # whole playlist/album
mgr.pause(device_id) / mgr.resume(device_id)
mgr.next_track(device_id) / mgr.previous_track(device_id)
mgr.set_volume(device_id, 70)
mgr.transfer("kitchen-speaker")         # move playback to this device
device.stop()                           # kill the librespot process
```

### Auth flows

| `flow`          | What it is                                   | Needs                     | Good for |
|-----------------|----------------------------------------------|---------------------------|----------|
| `"user"` (default) | Authorization Code with PKCE               | client id + redirect URI  | anything touching *your* playlists |
| `"app"`         | Client Credentials                           | client id + secret        | public search/read-only automation |
| `"token"`       | a raw access token in `SPOTIFY_ACCESS_TOKEN` | a pre-obtained token      | embed in existing scripts |

For the `"user"` flow, PKCE deliberately sends **no client secret** — the
client id and redirect URI are enough.

## CLI

```bash
python cli.py whoami
python cli.py playlists
python cli.py create "My New Playlist" --private
python cli.py show "My New Playlist"
python cli.py search "daft punk"
python cli.py add "My New Playlist" https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
python cli.py remove "My New Playlist" spotify:track:4uLU6hMCjMI75M1A2tKUQC
python cli.py export out.json          # every playlist + tracks as JSON
python cli.py now                      # currently playing (or "nothing playing")
python cli.py now --watch --interval 3 # print every track change, forever
python cli.py now --json               # current playback as JSON
python cli.py move <playlist> <from> <to>          # move by index
python cli.py up <playlist> <track> [--amount N]   # move earlier
python cli.py down <playlist> <track> [--amount N] # move later

# Streaming (Spotify Connect via librespot; Premium account required)
python cli.py stream start --name kitchen --username <account> --password <pw>   # launch device
python cli.py stream devices                                   # list all devices
python cli.py stream play "spotify:track:...,spotify:track:..." --name kitchen
python cli.py stream context "spotify:playlist:37i..." --name kitchen
python cli.py stream pause | resume | next | prev --name kitchen
python cli.py stream volume 70 --name kitchen
python cli.py stream transfer --name kitchen   # move playback to this device
```

## Project layout

```
spotify_playlist_manager/
    auth.py      # authentication -> authenticated spotipy client
    client.py    # PlaylistManager — the high-level facade
    models.py    # Playlist, Track, NowPlaying dataclasses (with to_dict() for JSON)
    export.py    # pull all playlists + tracks into JSON-ready structures
    player.py    # watch_now_playing / blocks_until_change live-playback helpers
    stream.py    # ConnectDevice (librespot) + Web-API playback control
    utils.py     # reference parsing + batching
    errors.py    # exception hierarchy
tests/           # no-network unit tests
cli.py           # small terminal tool exercising the wrapper
flake.nix        # Nix dev shell
pyproject.toml   # pip-installable package metadata
```

## Extending

Because `PlaylistManager` wraps a plain `spotipy.Spotify` client, everything
Spotify's API offers is reachable through `mgr.client` when you need to go
beyond the friendly surface. Suggested next steps:

- a sync runner (e.g. mirror a folder of playlists into an archive playlist)
- higher-level operations like *merge two playlists* or *dedupe*
- a schedule/daemon for recurring playlist updates

## Tests

```bash
pytest                              # offline unit tests (fast, no network)
RUN_LIVE=1 pytest tests/test_export_integration.py -v   # live API: export all playlists
```

## License

MIT