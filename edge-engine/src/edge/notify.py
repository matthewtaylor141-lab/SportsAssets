"""Phone alerts straight from the engine (ntfy — free, no account).

The owner is away. Reading a status page is not an option, and "no news" has
already meant "silently not trading" more than once in this project. So the
engine tells the phone, on its own, when something it should know changes:

  * the VERDICT changes — the one line naming what is blocking volume, so a
    stall announces itself instead of waiting to be discovered;
  * a real fill lands;
  * once a day, what actually happened.

Deliberately quiet: only transitions are pushed, never a heartbeat. A phone
that buzzes every cycle gets muted, and a muted alert channel is the same as
no alert channel.

Delivery is best-effort and off the hot path — the trading loop never waits
on a notification and never fails because of one.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time

log = logging.getLogger(__name__)

_Q: "queue.Queue[tuple[str, str, str]]" = queue.Queue(maxsize=200)
_STATE = {"started": False, "sent": 0, "dropped": 0}


def enabled() -> bool:
    return bool(os.environ.get("EDGE_NTFY_TOPIC", "").strip())


def _worker() -> None:
    import requests

    sess = requests.Session()
    server = os.environ.get("EDGE_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    topic = os.environ.get("EDGE_NTFY_TOPIC", "").strip()
    while True:
        title, body, priority = _Q.get()
        try:
            sess.post(f"{server}/{topic}", data=body.encode(), timeout=10,
                      headers={
                          # Header values must be latin-1 safe; em-dashes and
                          # the like would otherwise raise inside requests.
                          "Title": title.encode("ascii", "replace").decode(),
                          "Priority": priority, "Tags": "chart_with_upwards_trend"})
            _STATE["sent"] += 1
        except Exception as exc:  # noqa: BLE001 — an alert must never matter
            log.debug("ntfy push failed: %s", exc)  # more than a trade


def push(title: str, body: str, priority: str = "default") -> None:
    """Queue one notification. Never blocks, never raises."""
    if not enabled():
        return
    if not _STATE["started"]:
        _STATE["started"] = True
        threading.Thread(target=_worker, daemon=True, name="ntfy").start()
    try:
        _Q.put_nowait((title, body, priority))
    except queue.Full:
        _STATE["dropped"] += 1


class VerdictWatcher:
    """Pushes when the verdict CHANGES, plus a floor-rate reminder while a
    non-trading verdict persists (so a stall that lasts all day is not
    announced once at 3am and never again)."""

    def __init__(self, remind_after_s: float = 6 * 3600) -> None:
        self.last: str | None = None
        self.last_push = 0.0
        self.remind_after_s = remind_after_s

    def observe(self, verdict: str, now: float | None = None) -> bool:
        """Returns True if a push was queued."""
        now = now or time.time()
        if not verdict:
            return False
        changed = self._head(verdict) != self._head(self.last or "")
        trading = verdict.startswith("TRADING")
        stale = (not trading and self.last is not None
                 and now - self.last_push >= self.remind_after_s)
        if not (changed or stale):
            self.last = verdict
            return False
        self.last, self.last_push = verdict, now
        push("Edge engine" if trading else "Edge engine — not trading",
             verdict, priority="default" if trading else "high")
        return True

    @staticmethod
    def _head(verdict: str) -> str:
        """Compare the CLASS of verdict, not its numbers — otherwise a count
        ticking from 699 to 700 reads as news."""
        return verdict.split(":", 1)[0]


def daily_summary(ledger, risk) -> str:
    """What happened in the last 24h, in one message."""
    now = time.time()
    fills = ledger.live_fill_count_since(now - 86_400)
    staked = ledger.live_staked_since(now - 86_400)
    pnl = ledger.realized_pnl_since(now - 86_400, live_only=True)
    opens = len(ledger.open_positions(live_only=True))
    cap = risk.caps.per_day
    return (f"{fills} live fills, ${staked:.2f} staked of ${cap:.0f} budget\n"
            f"realized {pnl:+.2f}, {opens} open\n"
            f"mode {risk.mode}")
