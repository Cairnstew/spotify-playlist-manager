#!/usr/bin/env python3
"""One-off LIVE verification of move_track reorder math against Spotify.

Creates a throwaway playlist, seeds it with 5 tracks, applies a battery of
moves, and compares the final order against the order my offline move tests
predict. Deletes the playlist afterwards. Safe to re-run (starts fresh).

Run from the dev shell:  python scripts/verify_moves_live.py
"""

from __future__ import annotations

import sys

from spotify_playlist_manager import PlaylistManager

SEED = [
    "spotify:track:5W3cjX2J3tjhG8zb6u0qHn",  # Harder, Better, Faster, Stronger
    "spotify:track:09TlxralXOGX35LUutvw7I",  # End of Line
    "spotify:track:0DiWol3AO6WpXZgp0goxAV",  # One More Time
    "spotify:track:1pKYYY0dkg23sQQXi0Q5zN",  # Around the World
    "spotify:track:4zu9wo2FXoBSsKjO6tRB3R",  # Robot Rock
]

# Each case: (call, expected_final_order_by_seed_index).
# Expected order is the *intent*: take the item out and insert it at its
# target index. The live API result must equal this exactly.
CASES = [
    (("move_track", 1, 3), [0, 2, 3, 1, 4]),     # move seed1 -> index 3
    (("move_to_top", 0), [0, 2, 3, 1, 4]),       # seed0 already at top: no-op
    (("move_up", 3, 2), [0, 1, 2, 3, 4]),        # move seed1 (idx3) up 2 -> idx1
    (("move_down", 0, 2), [1, 2, 0, 3, 4]),      # move seed0 (idx0) down 2 -> idx2
    (("move_to_bottom", 1), [1, 0, 3, 4, 2]),    # move seed2 (idx1) to last -> idx4
    (("move_to_top", 4), [2, 1, 0, 3, 4]),       # move seed2 (idx4) to first
]


def main() -> int:
    mgr = PlaylistManager.from_env()

    playlist = mgr.create_playlist(
        "SPM move-test scratch — safe to delete",
        public=False,
        description="temporary: verifies move reorder math; auto-deleted",
    )
    print(f"created scratch playlist {playlist.id}")

    try:
        mgr.replace_tracks(playlist.id, SEED)
        order = [t.uri for t in mgr.tracks(playlist.id)]
        print("seed order:")
        for i, uri in enumerate(order):
            print(f"  {i}: {uri}")

        failures = 0
        for call, expected_seed_order in CASES:
            method, *args = call
            func = getattr(mgr, method)
            result = func(playlist.id, *args)
            order = [t.uri for t in mgr.tracks(playlist.id)]
            expected_uris = [SEED[i] for i in expected_seed_order]
            ok = order == expected_uris
            print(f"{method}{args}: {'OK' if ok else 'MISMATCH'}  (new index {result})")
            print(f"   got      {[u.rsplit(':', 1)[-1] for u in order]}")
            print(f"   expected {[u.rsplit(':', 1)[-1] for u in expected_uris]}")
            failures += 0 if ok else 1

        if failures:
            print(f"\n{failures} case(s) FAILED — the reorder math is wrong.")
            return 1
        print("\nall move cases matched Spotify's real behavior")
        return 0
    finally:
        mgr.delete(playlist.id)
        print(f"deleted scratch playlist {playlist.id}")


if __name__ == "__main__":
    raise SystemExit(main())