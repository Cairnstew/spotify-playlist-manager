"""Spotify API rate-limit guard.

The Spotify Web API rate-limits user-scoped routes on a sliding window (the
documented boundary is roughly 180 requests per 30 seconds for
``user-read-*`` / ``user-modify-*`` scopes).  When the limit is exceeded the
API answers ``429 Too Many Requests`` with a ``Retry-After`` header; spotipy
retries internally a few times (its ``retries`` / ``status_retries``
parameters, backoff courtesy of urllib3), then raises
:class:`~spotipy.exceptions.SpotifyException` with ``http_status == 429``.

The problem this module solves is a long-running *polling* consumer — the
waybar now-playing widget polls ``/v1/me/player`` every few seconds, and when
a 429 happens the widget:

* wastes the whole ``Retry-After`` duration inside spotipy's retry logic
  (printing "Your application has reached a rate/request limit..."),
* then still raises, so waybar gets no output,
* and multiple stuck ``exec`` instances pile up because the old process hangs
  on retries while the next poll spawns another one.

:class:`RateLimitGuard` sits in front of every API call and applies two cheap
preventive measures instead:

1. **Pacing** — a sliding window of request timestamps; ``acquire()`` blocks
   until a slot is available (Spotify's own documented shape: N requests per
   W seconds, default 180 / 30).
2. **Backoff** — ``record_429(retry_after)`` remembers ``Retry-After`` from a
   429 response (falling back to exponential backoff when the header is
   absent) and holds ``acquire()`` until the cooldown expires, so the next
   call — and every caller after it, including new waybar instances — waits
   instead of hammering again.

The guard is thread-safe (backed by a ``threading.Condition``) and can be
disabled entirely (``enabled=False``) or tuned to a more conservative rate.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime
from typing import Mapping

from .logging_config import log_event

_RATE_LIMIT_LOG = logging.getLogger("spotify_playlist_manager.rate_limit")

# Spotify's documented rate-limit shape for user-scoped routes: N requests per
# rolling W-second window.  The window shape mirrors what the API actually
# enforces (the docs phrase it as "180 requests per 30 seconds", not as a
# single per-second number).
DEFAULT_REQUESTS_PER_WINDOW = 180
DEFAULT_WINDOW_SECONDS = 30.0

# Fallback backoff schedule used when a 429 response carries no usable
# Retry-After header: 1s, 2s, 4s, … capped at 60s.
DEFAULT_BASE_BACKOFF = 1.0
DEFAULT_MAX_BACKOFF = 60.0

_RETRY_AFTER_HEADERS = ("Retry-After", "retry-after")


def retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    """Extract the ``Retry-After`` header from response headers as seconds.

    Returns ``None`` when the header is absent or unparseable, so callers can
    fall back to exponential backoff.  Handles the two formats RFC 7231
    allows: a non-negative integer/float number of seconds (what Spotify
    sends) and an HTTP-date.
    """
    if not headers:
        return None
    raw = None
    for key in _RETRY_AFTER_HEADERS:
        raw = headers.get(key)
        if raw is not None:
            break
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None

    try:
        return float(raw)
    except ValueError:
        pass

    # HTTP-date form (RFC 7231 §7.1.3) — rare from Spotify, but valid.
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    elapsed = (parsed - _now_utc()).total_seconds()
    return max(elapsed, 0.0)


def _now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class RateLimitGuard:
    """Pace Spotify API calls and back off after a 429.

    Parameters
    ----------
    enabled:
        Master switch.  When False, :meth:`acquire` and :meth:`record_429`
        are no-ops (useful for tests and callers that want to disable
        pacing).  Enabled by default.
    requests_per_window:
        How many calls are allowed per rolling ``window_seconds``.  Defaults
        to Spotify's documented 180 requests per 30 seconds for user scopes.
    window_seconds:
        Length of the rolling window, in seconds.
    base_backoff:
        First backoff (seconds) used when a 429 has no ``Retry-After``
        header.  Doubles on every consecutive 429.
    max_backoff:
        Cap on the exponential backoff.
    """

    def __init__(
        self,
        enabled: bool = True,
        requests_per_window: int = DEFAULT_REQUESTS_PER_WINDOW,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        base_backoff: float = DEFAULT_BASE_BACKOFF,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
    ) -> None:
        self._enabled = bool(enabled)
        self._requests_per_window = max(1, int(requests_per_window))
        self._window_seconds = max(0.1, float(window_seconds))
        self._base_backoff = max(0.0, float(base_backoff))
        self._max_backoff = max(self._base_backoff, float(max_backoff))

        self._timestamps: deque[float] = deque()
        self._condition = threading.Condition()
        self._cooldown_until = 0.0
        self._retry_after = 0.0
        self._consecutive_429s = 0

    # -- Read-only introspection -------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def requests_per_window(self) -> int:
        return self._requests_per_window

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def rate_per_second(self) -> float:
        """The enforced average request rate (requests / window)."""
        return self._requests_per_window / self._window_seconds

    @property
    def retry_after(self) -> float:
        """Seconds of cooldown imposed by the most recent 429 (0 if none)."""
        return self._retry_after

    @property
    def cooldown_remaining(self) -> float:
        """Seconds until the current 429 cooldown expires (0 if none)."""
        return max(self._cooldown_until - time.monotonic(), 0.0)

    # -- The API ------------------------------------------------------------

    def acquire(self) -> None:
        """Block until a request slot is available, then take one.

        Enforces two constraints, whichever expires later:

        * the sliding-window rate (no more than ``requests_per_window`` calls
          in the last ``window_seconds``), and
        * any cooldown left over from a recorded 429.
        """
        if not self._enabled:
            return
        with self._condition:
            while True:
                now = time.monotonic()
                if now < self._cooldown_until:
                    wait = self._cooldown_until - now
                else:
                    self._prune(now)
                    if len(self._timestamps) < self._requests_per_window:
                        self._timestamps.append(now)
                        return
                    # Window is full: wait until the oldest request rolls off
                    # (that is when a slot frees up).
                    wait = self._window_seconds - (now - self._timestamps[0])
                self._condition.wait(max(wait, 0.001))

    def record_429(self, retry_after: float | None = None) -> None:
        """Record a 429 so :meth:`acquire` backs off before the next call.

        Parameters
        ----------
        retry_after:
            Seconds from the response's ``Retry-After`` header, if present.
            When None/<=0, an exponential backoff based on the number of
            consecutive 429s is used instead (``base_backoff * 2**n``,
            capped at ``max_backoff``).
        """
        if not self._enabled:
            return
        self._consecutive_429s += 1
        if retry_after is not None and retry_after > 0:
            delay = max(float(retry_after), 0.1)
        else:
            delay = min(
                self._base_backoff * (2 ** (self._consecutive_429s - 1)),
                self._max_backoff,
            )
        self._retry_after = delay
        with self._condition:
            self._cooldown_until = time.monotonic() + delay
            self._condition.notify_all()
        log_event(
            _RATE_LIMIT_LOG,
            "rate_limit.rate_limited",
            http_status=429,
            retry_after=round(delay, 3),
            consecutive_429s=self._consecutive_429s,
        )

    def record_success(self) -> None:
        """Reset the consecutive-429 counter (exponential backoff restarts)."""
        self._consecutive_429s = 0

    # -- Internals ------------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop timestamps that have fallen out of the sliding window."""
        window = self._window_seconds
        while self._timestamps and now - self._timestamps[0] >= window:
            self._timestamps.popleft()

    def __repr__(self) -> str:
        return (
            f"<RateLimitGuard enabled={self._enabled} "
            f"rate={self.rate_per_second:.2f}/s "
            f"({self._requests_per_window}/{self._window_seconds:.0f}s) "
            f"cooldown={self.cooldown_remaining:.1f}s>"
        )