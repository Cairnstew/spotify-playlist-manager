"""CLI entry point for spotify-playlist-manager.

This module lives inside the package so setuptools can register it as a
console_scripts entry point (see pyproject.toml).  The original repo-root
cli.py is preserved for `python cli.py` / `python -m` usage.
"""

from __future__ import annotations

import argparse
import sys

from spotify_playlist_manager import PlaylistManager
from spotify_playlist_manager.logging_config import setup_logging, _xdg_state_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotify-playlist-manager",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--flow",
        choices=("user", "app", "token"),
        default="user",
        help="auth flow (default: user/PKCE)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="enable debug logging (both console and file)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "path to the JSON log file "
            "(default: $XDG_STATE_HOME/spotify-playlist-manager/logs/spm.log)"
        ),
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="disable file logging entirely",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("whoami", help="print the authenticated profile")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("playlists", help="list your playlists")
    p.set_defaults(func=cmd_playlists)

    p = sub.add_parser("create", help="create a playlist")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p.add_argument("--private", action="store_true", help="make non-public")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("show", help="show one playlist and its tracks")
    p.add_argument("playlist", help="playlist URL, URI, or id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("delete", help="delete/unfollow a playlist")
    p.add_argument("playlist")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("search", help="search for tracks")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("add", help="add tracks to a playlist")
    p.add_argument("playlist")
    p.add_argument("tracks", nargs="+", help="track URLs, URIs, or ids")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="remove tracks from a playlist")
    p.add_argument("playlist")
    p.add_argument("tracks", nargs="+", help="track URLs, URIs, or ids")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("export", help="dump every playlist + track to a JSON file")
    p.add_argument("output", nargs="?", default="playlists-export.json", help="output JSON path")
    p.add_argument("--metadata-only", action="store_true", help="skip fetching each playlist's tracks")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("now", help="show the currently playing track")
    p.add_argument("--watch", action="store_true", help="keep polling and print every change")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between polls when --watch")
    p.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    p.set_defaults(func=cmd_now)

    p = sub.add_parser("move", help="move a track to a new position by index")
    p.add_argument("playlist")
    p.add_argument("from_index", type=int, help="current 0-based position")
    p.add_argument("to_index", type=int, help="target 0-based position")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("up", help="move a track up (earlier) in a playlist")
    p.add_argument("playlist")
    p.add_argument("track", help="track index, URL, URI, or id")
    p.add_argument("--amount", type=int, default=1, help="how many slots to move")
    p.set_defaults(func=cmd_move_up)

    p = sub.add_parser("down", help="move a track down (later) in a playlist")
    p.add_argument("playlist")
    p.add_argument("track", help="track index, URL, URI, or id")
    p.add_argument("--amount", type=int, default=1, help="how many slots to move")
    p.set_defaults(func=cmd_move_down)

    p = sub.add_parser(
        "stream",
        help="stream audio through a Spotify Connect device (librespot). "
        "Requires a Premium account and librespot on PATH (nix develop provides it).",
    )
    p.add_argument(
        "action",
        choices=("start", "play", "context", "pause", "resume", "next", "prev", "volume", "transfer", "devices", "stop"),
        help=(
            "start: launch the Connect device | play: play track URIs | "
            "context: play a playlist/album | pause/resume/next/prev/volume: "
            "control it | transfer: move playback here | devices: list devices | stop: kill device"
        ),
    )
    p.add_argument("target", nargs="?", default=None, help="for play: track URI(s) comma-separated, for context: playlist/album URI, for volume: 0-100")
    p.add_argument("--name", default="Spm-Connect", help="device name (default: Spm-Connect)")
    p.add_argument("--backend", default="pulseaudio", help="librespot audio backend (default: pulseaudio)")
    p.add_argument("--username", default=None, help="librespot account username (optional if cached)")
    p.add_argument("--password", default=None, help="librespot account password (optional if cached)")
    p.add_argument("--wait", type=float, default=60.0, help="seconds to wait for device registration")
    p.add_argument("--volume", type=int, default=None, help="volume 0-100 for volume action")
    p.set_defaults(func=cmd_stream)

    # -- waybar ----------------------------------------------------------
    p = sub.add_parser("waybar", help="waybar custom-module helpers (now playing / upvote button)")
    waybar_sub = p.add_subparsers(dest="waybar_action", required=True)

    w = waybar_sub.add_parser("now", help="emit now-playing waybar JSON (exec mode)")
    w.add_argument("--json", action="store_true", help="print waybar-compatible JSON; otherwise a human-readable line")
    w.set_defaults(func=cmd_waybar_now)

    w = waybar_sub.add_parser("upvote", help="waybar upvote button helper")
    w.add_argument("--json", action="store_true", help="emit waybar JSON (exec mode); without it, upvote and print the result (on-click mode)")
    w.add_argument("--playlist", default="", help="playlist id/URI/URL to vote in (default: auto-detect from now playing)")
    w.set_defaults(func=cmd_waybar_upvote)

    # -- votes -----------------------------------------------------------
    p = sub.add_parser("votes", help="vote-based playlist ordering")
    votes_sub = p.add_subparsers(dest="votes_action", required=True)

    v = votes_sub.add_parser("up", help="upvote the currently playing song")
    v.set_defaults(func=cmd_votes_up)

    v = votes_sub.add_parser("list", help="show top-voted songs")
    v.add_argument("--playlist", default="", help="filter by playlist ID")
    v.add_argument("--limit", type=int, default=20, help="max entries to show")
    v.set_defaults(func=cmd_votes_list)

    v = votes_sub.add_parser("apply", help="reorder a playlist by votes (most voted first)")
    v.add_argument("playlist", help="playlist URL, URI, or id")
    v.add_argument("--dry-run", action="store_true", help="show what would change without modifying Spotify")
    v.set_defaults(func=cmd_votes_apply)

    return parser


def cmd_whoami(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    profile = mgr.me()
    print(f"{profile.get('display_name', profile.get('id', '?'))} <{profile.get('id')}>")


def cmd_playlists(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    for playlist in mgr.playlists():
        print(f"{playlist.name}\t{playlist.id}\t{'public' if playlist.public else 'private'}")


def cmd_create(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    playlist = mgr.create_playlist(
        args.name,
        public=not args.private,
        description=args.description,
    )
    print(f"created: {playlist.name} ({playlist.id})")


def cmd_show(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    playlist = mgr.playlist(args.playlist, fetch_tracks=True)
    print(f"# {playlist.name}  [{playlist.owner}]")
    if playlist.description:
        print(playlist.description)
    for i, track in enumerate(playlist.tracks, start=1):
        print(f"{i:>3}. {track}")


def cmd_delete(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    mgr.delete(args.playlist)
    print("deleted.")


def cmd_search(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    for i, track in enumerate(mgr.search(args.query, limit=args.limit), start=1):
        print(f"{i:>3}. {track}\t{track.uri}")


def cmd_add(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    mgr.add_tracks(args.playlist, args.tracks)
    print(f"added {len(args.tracks)} track(s).")


def cmd_remove(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    mgr.remove_tracks(args.playlist, args.tracks)
    print(f"removed {len(args.tracks)} track(s).")


def cmd_export(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    from spotify_playlist_manager.export import export_playlists_to_file

    path = export_playlists_to_file(mgr, args.output, include_tracks=not args.metadata_only)
    print(f"exported playlists -> {path}")


def cmd_now(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    import json as _json

    from spotify_playlist_manager.player import watch_now_playing

    def render(now) -> str:
        if now is None:
            return "nothing playing"
        if args.json:
            return _json.dumps(now.to_dict())
        state = "▶" if now.is_playing else "⏸"
        return f"{state} {now.track}  on {now.device_name or 'unknown device'}"

    if args.watch:
        for snapshot in watch_now_playing(mgr, interval=args.interval):
            print(render(snapshot), flush=True)
    else:
        print(render(mgr.now_playing()))


def _print_track_at(mgr: PlaylistManager, ref: str, index: int) -> None:
    tracks = mgr.tracks(ref)
    track = tracks[index] if 0 <= index < len(tracks) else None
    if track:
        print(f"now at {index}: {track}")
    else:
        print(f"playlist has {len(tracks)} tracks; nothing at {index}")


def cmd_move(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    new_index = mgr.move_track(args.playlist, args.from_index, args.to_index)
    _print_track_at(mgr, args.playlist, new_index)


def cmd_move_up(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    new_index = mgr.move_up(args.playlist, args.track, amount=args.amount)
    _print_track_at(mgr, args.playlist, new_index)


def cmd_move_down(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    new_index = mgr.move_down(args.playlist, args.track, amount=args.amount)
    _print_track_at(mgr, args.playlist, new_index)


def cmd_stream(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    from spotify_playlist_manager.stream import ConnectDevice

    def device_id() -> str:
        dev = mgr.find_device(args.name)
        if not dev or not dev.get("id"):
            raise SystemExit(f"device {args.name!r} not found. Start it first: spotify-playlist-manager stream start --name {args.name}")
        return dev["id"]

    if args.action == "devices":
        for dev in mgr.devices():
            active = "*" if dev.get("is_active") else " "
            print(f"{active} {dev.get('name', '?')}  {dev.get('id', '?')}")
        return

    if args.action == "start":
        device = ConnectDevice(name=args.name, backend=args.backend, username=args.username, password=args.password)
        device.start()
        dev_id = device.wait_until_device(mgr.devices, timeout=args.wait)
        if dev_id is None:
            device.stop()
            print(f"device {args.name!r} did not register within {args.wait}s. Check librespot.log")
            raise SystemExit(1)
        print(f"device {args.name!r} started: {dev_id}")
        return

    if args.action == "stop":
        dev = mgr.find_device(args.name)
        if dev:
            print(f"device {args.name!r} registered as {dev['id']}; to stop its audio, pause it (or Ctrl-C if running in a terminal).")
        else:
            print(f"no device named {args.name!r} registered.")
        return

    dev_id = device_id()
    if args.action == "play":
        uris = [u.strip() for u in args.target.split(",") if u.strip()]
        mgr.play_uris(dev_id, uris)
        print(f"playing on {args.name!r}: {', '.join(uris)}")
    elif args.action == "context":
        mgr.play_context(dev_id, args.target)
        print(f"playing context {args.target} on {args.name!r}")
    elif args.action == "pause":
        mgr.pause(dev_id)
        print("paused.")
    elif args.action == "resume":
        mgr.resume(dev_id)
        print("resumed.")
    elif args.action == "next":
        mgr.next_track(dev_id)
        print("next track.")
    elif args.action == "prev":
        mgr.previous_track(dev_id)
        print("previous track.")
    elif args.action == "volume":
        if args.volume is None and args.target is None:
            raise SystemExit("volume must be 0-100 (pass as target or --volume)")
        mgr.set_volume(dev_id, int(args.volume if args.volume is not None else args.target))
        print("volume set.")
    elif args.action == "transfer":
        mgr.transfer(dev_id)
        print(f"transferred playback to {args.name!r}.")


# ── votes ────────────────────────────────────────────────────────────────


def _find_playlist_for_track(mgr: PlaylistManager, track_id: str) -> str:
    """Find the first playlist containing *track_id*. Returns empty string if not found."""
    for pl in mgr.playlists():
        try:
            tracks = mgr.tracks(pl.id)
            if any(t.id == track_id for t in tracks):
                return pl.id
        except Exception:
            continue
    return ""


def cmd_votes_up(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    from spotify_playlist_manager.votes import VoteStore

    np = mgr.now_playing()
    if np is None:
        print("nothing playing.", file=sys.stderr)
        return

    store = VoteStore()

    # Try to find which playlist this track belongs to
    playlist_id = _find_playlist_for_track(mgr, np.track.id)

    new_count = store.upvote(playlist_id, np.track.id, np.track)
    print(f"♥ {np.track}  ({new_count} vote{'s' if new_count != 1 else ''})")
    if playlist_id:
        print(f"  playlist: {playlist_id}")


def cmd_votes_list(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    from spotify_playlist_manager.votes import VoteStore

    store = VoteStore()
    entries = store.top_tracks(args.playlist, limit=args.limit)

    if not entries:
        print("no votes yet.")
        return

    for i, entry in enumerate(entries, 1):
        label = f"{entry.artist} — {entry.track_name}" if entry.artist else entry.track_id
        print(f"  {i:2}. {entry.votes:>3}  {label}")


def cmd_votes_apply(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    from spotify_playlist_manager.votes import VoteStore, apply_votes

    store = VoteStore()
    results = apply_votes(mgr, store, args.playlist, dry_run=args.dry_run)

    if not results:
        print("no votes to apply.")
        return

    prefix = "[dry-run] " if args.dry_run else ""
    for i, (name, artist, votes) in enumerate(results, 1):
        label = f"{artist} — {name}" if artist else name
        print(f"  {prefix}{i:2}. {votes:>3}  {label}")

    if not args.dry_run:
        print(f"\nreordered {len(results)} track(s).")


# ── waybar ───────────────────────────────────────────────────────────────


def cmd_waybar_now(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    """One now-playing snapshot for a waybar custom module.

    ``--json`` prints only the waybar JSON on stdout (nothing else), as an
    ``exec`` module expects; waybar's ``interval`` re-runs this to poll.
    """
    import json as _json

    from spotify_playlist_manager.waybar import now_playing_output

    now = mgr.now_playing()
    if args.json:
        print(_json.dumps(now_playing_output(now)))
    else:
        print(str(now) if now else "nothing playing")


def cmd_waybar_upvote(mgr: PlaylistManager, args: argparse.Namespace) -> None:
    """Upvote button helper.

    ``--json`` is the ``exec`` mode: emit the vote-count waybar JSON only.
    It is **read-only** — it shows the current count without incrementing,
    so waybar's ``interval`` polling cannot inflate the count. Without it
    (waybar's ``on-click`` mode) the current track is upvoted and a
    human-readable result is printed.
    """
    import json as _json

    from spotify_playlist_manager.votes import VoteStore
    from spotify_playlist_manager.waybar import (
        current_vote_output,
        upvote_current_track,
        upvote_output,
    )

    store = VoteStore()

    if args.json:
        # exec/poll mode: display only, never increment.
        out = current_vote_output(mgr, store, playlist=args.playlist)
        print(_json.dumps(out if out is not None else upvote_output(0)))
        return

    # on-click mode: actually upvote (increments the counter).
    result = upvote_current_track(mgr, store, playlist=args.playlist)
    if result is None:
        print("nothing playing.", file=sys.stderr)
        return
    print(f"♥ {result.track}  ({result.votes} vote{'s' if result.votes != 1 else ''})")
    if result.playlist:
        print(f"  playlist: {result.playlist}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # -- Logging setup (precedence: CLI flag > env var > default) ----------
    log_level = "DEBUG" if args.verbose else "WARNING"
    file_level = "DEBUG" if args.verbose else "INFO"

    if args.no_log_file:
        log_file = None
    elif args.log_file is not None:
        log_file = args.log_file
    else:
        log_file = str(_xdg_state_dir() / "spm.log")

    setup_logging(
        level=log_level,
        log_file=log_file,
        file_level=file_level,
        console=True,
    )

    # -- Run the command ---------------------------------------------------
    try:
        mgr = PlaylistManager.from_env(flow=args.flow)
        args.func(mgr, args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
