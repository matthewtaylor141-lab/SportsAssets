"""Track-only qualification cohort (owner 95%-confidence program).

The dossier shortlist is tracked for WHALEEDGE evidence ONLY. Money
gates pinned here: the cohort can never intersect the copy gates
(COPY_WHALES / CRYPTO_WHALES — promotion is the owner's decision,
made by editing those sets explicitly), it joins the roster target
WITHOUT consuming roster_size slots, it survives the retire loop
without being pinned, and banned always wins.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sportsassets import roster
from sportsassets.api.copies_record import COPY_WHALES, CRYPTO_WHALES


def test_track_only_never_intersects_the_copy_gates():
    copyable = {w.lower() for w in (COPY_WHALES | CRYPTO_WHALES)}
    for addr, uname in roster.TRACK_ONLY.items():
        assert uname.lower() not in copyable, \
            f"{uname}: tracking must never imply copying"
        assert addr.lower() not in copyable, \
            f"{addr}: tracking must never imply copying"
    assert len(roster.TRACK_ONLY) == len(
        {u.lower() for u in roster.TRACK_ONLY.values()}), \
        "usernames are identities downstream — no collisions"


class _Conn:
    def __init__(self):
        self.execs = []

    async def execute(self, sql, *a):
        self.execs.append((sql, a))


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, rows):
        self._rows = rows
        self.conn = _Conn()

    async def fetch(self, sql):
        return self._rows

    def acquire(self):
        return _Acquire(self.conn)


def _run_apply(monkeypatch, existing_rows, candidates):
    pool = _Pool(existing_rows)

    async def fake_pool():
        return pool

    async def fake_alert(msg):
        return None

    monkeypatch.setattr(roster, "get_pool", fake_pool)
    monkeypatch.setattr(roster.telegram, "alert_admins", fake_alert)
    monkeypatch.setattr(
        roster, "settings", lambda: SimpleNamespace(roster_size=5))
    out = asyncio.run(roster.apply_roster(candidates))
    return pool, out


def test_track_only_joins_beyond_the_cap_and_survives_retirement(
        monkeypatch):
    addrs = list(roster.TRACK_ONLY)
    banned_addr, live_addr = addrs[0], addrs[1]
    existing = [
        {"address": "0xpinned", "username": "rn1", "banned": False,
         "pinned": True, "active": True, "sports_profit_alltime": 1.0,
         "source_rank": 1},
        # an active non-pinned stray NOT in candidates: retired
        {"address": "0xstray", "username": "old", "banned": False,
         "pinned": False, "active": True, "sports_profit_alltime": 0.0,
         "source_rank": 9},
        # a banned track-only wallet: banned always wins
        {"address": banned_addr, "username": "bad", "banned": True,
         "pinned": False, "active": False,
         "sports_profit_alltime": None, "source_rank": None},
        # an already-active track-only wallet: updated, never retired
        {"address": live_addr, "username": roster.TRACK_ONLY[live_addr],
         "banned": False, "pinned": False, "active": True,
         "sports_profit_alltime": None, "source_rank": None},
    ]
    cands = [{"address": f"0xcand{i}", "username": f"c{i}",
              "profit": 0.0, "rank": i} for i in range(5)]
    pool, out = _run_apply(monkeypatch, existing, cands)

    # the cohort consumed no roster slots: with 1 pinned + cap 5,
    # exactly 4 candidates fit — the same count as before the cohort
    # existed (pinned consuming a slot is pre-existing roster design)
    for c in cands[:4]:
        assert c["address"] in out["added"]
    assert cands[4]["address"] not in out["added"], \
        "the cap binds candidates exactly as it did pre-cohort"
    # cohort added except the banned wallet and the already-active one
    for a in addrs:
        if a in (banned_addr, live_addr):
            assert a not in out["added"]
        else:
            assert a in out["added"]
    assert all(banned_addr not in (args or ()) for _, args in
               pool.conn.execs), "banned never touches the table"
    # the stray is retired; the live track-only wallet is NOT
    assert out["removed"] == ["0xstray"]
