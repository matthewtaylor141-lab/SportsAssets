"""THE POSITIONS IDENTITY-DEBT CENSUS: what the analytics persist dead-letters, counted.

WHY THIS EXISTS. `engine._persist_positions` drops every (whale, token)
position state that still has no condition_id after the token-catalog rescue
(the 2026-08-11/12 incident: one NULL froze the whole snapshot). It said so
with one WARNING per cycle -- "10902 row(s) still missing condition_id after
token-catalog rescue -- dead-lettered" every ~6 minutes on release 732cc0c6
(workers log 2026-10-09 04:20-05:20Z) -- a number with no owner, no age and
no answer to the only question that matters: does any ACTIVE position of OURS
have no market identity?

WHAT IT IS NOW. A counted, classified census of the dead-lettered states:

    OWNER    TRACKED_RESEARCH_WALLET  a roster wallet (whales); the `trades`
                                      ledger that rebuild_positions replays
                                      holds ONLY roster wallets
             OWN_BOOK                 a roster row whose address is one of
                                      our own configured wallets (pm_funder)

    CLASS    FLAT_ZERO_SIZE           no shares left (sold out)       HISTORICAL_DEBT
             RESOLVED                 resolved                         HISTORICAL_DEBT
             OPEN_NO_FILL_WITHIN_30D  shares left, no fill in 30 days HISTORICAL_DEBT
             OPEN_FILLED_WITHIN_30D   shares left, a fill in 30 days  ACTIVE

It is logged when it CHANGES (its signature: the counts by owner and class
and the refusal), not on every cycle; every cycle carries it on the analytics
heartbeat. An ACTIVE state of OUR OWN with no identity is refused BY NAME
(R_OWN_ACTIVE_IDENTITY_UNKNOWN): logged as an error and carried as the
analytics heartbeat's refusal, so it can never sit inside a count.

WHAT IT DOES NOT DO. No rescue beyond the existing token catalog (no slug
guess, no fabricated condition), no deletion, no backfill: the dead-lettered
states stay out of `positions` exactly as before; this only names them.

THE 30-DAY LINE. A dead-lettered state has no market, so its market's state
cannot be read; the only fact the replay has is the age of its last fill. A
state with open shares and a fill inside 30 days is counted ACTIVE (it may be
live); older is HISTORICAL_DEBT. The line classifies a count -- it gates
nothing, sizes nothing and never removes a state from view. Production
(research-sql run 37927888187, 2026-10-09 12:06Z): all 10,902 dead-lettered
states are OPEN_NO_FILL_WITHIN_30D, six pinned research wallets, last fill
2026-09-05 15:25Z, 69,181 fills, chain-lane tokens the catalog never held.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .positions import EPS

VERSION = "POSITIONS_IDENTITY_CENSUS_V1"
ACTIVE_WINDOW = timedelta(days=30)

OWNER_RESEARCH = "TRACKED_RESEARCH_WALLET"
OWNER_OWN = "OWN_BOOK"
HISTORICAL = "HISTORICAL_DEBT"
ACTIVE = "ACTIVE"
C_FLAT = "FLAT_ZERO_SIZE"
C_RESOLVED = "RESOLVED"
C_OPEN_STALE = "OPEN_NO_FILL_WITHIN_30D"
C_OPEN_RECENT = "OPEN_FILLED_WITHIN_30D"
KIND_OF = {C_FLAT: HISTORICAL, C_RESOLVED: HISTORICAL,
           C_OPEN_STALE: HISTORICAL, C_OPEN_RECENT: ACTIVE}

#: an ACTIVE dead-lettered position of OUR OWN: no market identity, so no
#: settlement, no mark and no exit can be bound to it
R_OWN_ACTIVE_IDENTITY_UNKNOWN = "OWN_ACTIVE_POSITION_HAS_NO_MARKET_IDENTITY"

ST_CLEAN = "NO_IDENTITY_DEBT"
ST_HISTORICAL = "HISTORICAL_DEBT_ONLY"
ST_ACTIVE_RESEARCH = "ACTIVE_RESEARCH_WALLET_DEBT"
ST_REFUSED = "REFUSED"

MAX_WHALES = 20
MAX_EXAMPLES = 5


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def classify(state, now: datetime) -> str:
    """One dead-lettered PositionState's class. Pure."""
    pos = state.position
    if pos.resolved:
        return C_RESOLVED
    if pos.shares <= EPS:
        return C_FLAT
    last = _aware(state.last_ts)
    if last is not None and last >= _aware(now) - ACTIVE_WINDOW:
        return C_OPEN_RECENT
    return C_OPEN_STALE


def census(missing: list, *, now: datetime,
           own_whale_ids: set | frozenset = frozenset(),
           own_wallets_configured: int = 0) -> dict:
    """The census of the dead-lettered states `missing` (states with no
    condition_id after the rescue). Pure."""
    now = _aware(now)
    by_owner: dict = {}
    by_whale: dict = {}
    examples: list = []
    own_active = 0
    for st in missing:
        owner = OWNER_OWN if st.whale_id in own_whale_ids else OWNER_RESEARCH
        cls = classify(st, now)
        kind = KIND_OF[cls]
        o = by_owner.setdefault(owner, {HISTORICAL: 0, ACTIVE: 0,
                                        "by_class": {}})
        o[kind] += 1
        o["by_class"][cls] = o["by_class"].get(cls, 0) + 1
        w = by_whale.setdefault(st.whale_id, {
            "whale_id": st.whale_id, "owner": owner, "states": 0,
            HISTORICAL: 0, ACTIVE: 0, "last_fill_at": None})
        w["states"] += 1
        w[kind] += 1
        last = _aware(st.last_ts)
        if last is not None and (w["last_fill_at"] is None
                                 or last > w["last_fill_at"]):
            w["last_fill_at"] = last
        if kind == ACTIVE:
            if owner == OWNER_OWN:
                own_active += 1
            if len(examples) < MAX_EXAMPLES:
                examples.append({"whale_id": st.whale_id, "owner": owner,
                                 "token_id": str(st.token_id),
                                 "shares": round(st.position.shares, 6),
                                 "last_fill_at": None if last is None
                                 else last.isoformat()})
    whales = sorted(by_whale.values(), key=lambda w: (-w["states"],
                                                      w["whale_id"]))
    for w in whales:
        w["last_fill_at"] = (None if w["last_fill_at"] is None
                             else w["last_fill_at"].isoformat())
    active = sum(o[ACTIVE] for o in by_owner.values())
    refusal = R_OWN_ACTIVE_IDENTITY_UNKNOWN if own_active else None
    status = (ST_REFUSED if refusal else ST_ACTIVE_RESEARCH if active
              else ST_HISTORICAL if missing else ST_CLEAN)
    return {
        "version": VERSION, "as_of": now.isoformat(),
        "dead_lettered": len(missing),
        "historical_debt": sum(o[HISTORICAL] for o in by_owner.values()),
        "active": active, "own_active": own_active,
        "status": status, "refusal": refusal,
        "by_owner": by_owner,
        "by_whale": whales[:MAX_WHALES],
        "whales_truncated": len(whales) > MAX_WHALES,
        "active_examples": examples,
        "own_wallets_configured": int(own_wallets_configured),
        "active_window_days": ACTIVE_WINDOW.days,
        "rule": ("a dead-lettered state (no condition_id after the token-"
                 "catalog rescue) is ACTIVE when it holds shares and filled "
                 "within %d days, else HISTORICAL_DEBT; nothing is rescued, "
                 "deleted or backfilled here" % ACTIVE_WINDOW.days)}


def signature(c: dict) -> tuple:
    """What a change is: the counts by owner and class, and the refusal --
    never the clock (as_of) or the examples."""
    return (c.get("dead_lettered"), c.get("refusal"),
            tuple(sorted((owner, cls, n)
                         for owner, o in (c.get("by_owner") or {}).items()
                         for cls, n in (o.get("by_class") or {}).items())))


def line(c: dict) -> str:
    """The one-line statement of a census, for the log."""
    parts = []
    for owner, o in sorted((c.get("by_owner") or {}).items()):
        cls = ", ".join("%s %d" % (k, v)
                        for k, v in sorted(o["by_class"].items()))
        parts.append("%s: %s %d / %s %d [%s]" % (
            owner, HISTORICAL, o[HISTORICAL], ACTIVE, o[ACTIVE], cls))
    return ("positions identity-debt census: %d dead-lettered (no condition_"
            "id after the token-catalog rescue) -- %s; status %s; refusal %s"
            % (c.get("dead_lettered") or 0,
               "; ".join(parts) or "none", c.get("status"),
               c.get("refusal") or "none"))


_LAST: dict = {"signature": None}


def log_on_change(c: dict, log) -> bool:
    """Log the census when its signature changed since the last one logged
    in this process (the first census of a process always logs). A refusal
    logs at ERROR, debt at WARNING, a clean census at INFO. Returns whether
    it logged."""
    sig = signature(c)
    if sig == _LAST["signature"]:
        log.debug("%s (unchanged)", line(c))
        return False
    _LAST["signature"] = sig
    if c.get("refusal"):
        log.error("%s; examples %s", line(c), c.get("active_examples"))
    elif c.get("dead_lettered"):
        log.warning(line(c))
    else:
        log.info(line(c))
    return True


def summary(c: dict | None) -> dict | None:
    """The census as the analytics heartbeat carries it (bounded)."""
    if not c:
        return None
    return {k: c.get(k) for k in (
        "version", "as_of", "dead_lettered", "historical_debt", "active",
        "own_active", "status", "refusal", "by_owner",
        "own_wallets_configured", "active_window_days")} | {
        "by_whale": (c.get("by_whale") or [])[:MAX_WHALES]}
