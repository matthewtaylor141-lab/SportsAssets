"""FROZEN-WINDOW FRESHNESS: THE WHOLE OBSERVATION WINDOW, NOT ONE SNAPSHOT
(RC6 lane D1, measurement). Read only; no order path; nothing here changes
a bound, a gate or what counts as current.

WHAT WAS MEASURED BEFORE (RC5 / RC6). priority_members_fresh was ONE
instant: the coverage pass's fresh set, published with the next snapshot.
Production (research-sql run 37870039455) shows why one instant is not the
measure: over 24 h the instantaneous priority rate ranged 0.01-1.00, its
denominator 77-306 as candidates came and went (re-chosen every pass), the
plane's snapshots arrived p50 92 s / p90 248 s apart with gaps up to 1,854
s (minutes no sample covered at all), and the instant a snapshot named was
not the instant its counts were verified.

THE MEASURE (this module, written by the plane's freshness task; read by
completion.read):

  FROZEN MEMBERSHIP. Once per WINDOW_S (UTC hour) the eligible set is
  frozen at the window's first sample and persisted as one append-only
  FRESHNESS_WINDOW event ("fwin:<window start>"): every member with its
  tier, venue, market family, period, event start and alternative-line
  flag, and the sha256 of the sorted membership. Eligible = every active
  registry contract the plane holds as priority (open PAPER positions:
  HELD_POSITION; evaluated candidates: CANDIDATE) and every market a PAPER
  order is still working on (WORKING_ORDER) -- tiers in that order of
  precedence. A plane restarted inside the window re-reads the persisted
  membership; it is NEVER re-chosen. A member that joins later is measured
  apart (`joined`, visible every sample) and enters the next window.

  ONE SAMPLE A MINUTE (SAMPLE_EVERY_S), by the plane's freshness task
  (beside the pass, so a minutes-long pass does not stop it), persisted as
  FRESHNESS_SAMPLE ("fsample:<minute>"): one state code per frozen member,
  in frozen order, at ONE verification instant (`verified_at`):

    S  CURRENT_PMX_STREAM       the stream's own current() at the bound
    R  CURRENT_PLANE_REFRESH    the plane's REST book read inside the bound
    G  CURRENT_PLANE_SNAPSHOT   the plane's snapshot-only gRPC read inside
                                the bound (when it runs)
    P  CURRENT_PAPER_REST       the paper runtime's successful book read
                                inside the bound (error IS NULL)
    K  CURRENT_KALSHI_BOOK      a readable Kalshi book inside the bound
    N  NOT_CURRENT              every other answer, its reason counted
    U  NOT_HELD_BY_THE_PLANE    the plane's books do not hold it (not
                                listed, refdata pending, not assigned)
    X  EXTERNAL_UNAVAILABLE     the VENUE's own state says the market is not
                                open: a terminal state (any age) or a
                                not-open state read inside the bound -- the
                                held-position rule (bettor_paper_freshness),
                                evidence named; excluded from the eligible
                                count exactly as there, and counted

  Each sample also carries, for the current members, the distribution of
  RECEIPT age (our receipt instant -> verified_at) and of SOURCE-EVENT age
  (the venue's transactTime -> verified_at; the venue does not document
  what that stamp means, research/p5_live_book_currency_review.md, so it is
  reported and never used to judge currency): the three instants are kept
  apart, never merged, never re-stamped.

  THE WINDOW INTEGRAL (`integrate`). A sample stands for at most CARRY_S
  (two sample intervals); time no sample covers is OUTAGE and counts every
  frozen member eligible and NOT current (an outage is never dropped from
  the denominator). A whole hour without a window event uses the last
  frozen membership before it, at any age, labelled CARRIED (the readback
  reads the newest window at or before each horizon's start however old
  it is: an outage of any length crossing the start is in the
  denominator). Time before the FIRST frozen window ever written
  (`measured_since`) is reported as not measured by this build
  (`unmeasured_before_s`) -- never as fresh, never as an outage of a
  measure that did not exist yet; that instant is the first window ever,
  never merely the first one a read happened to return. The rate
  is fresh member-seconds / eligible member-seconds, per subgroup: tier,
  the management view (HELD_POSITION + WORKING_ORDER), venue, market
  family, period, phase (at the instant), and the alternative-line overlay.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "FROZEN_WINDOW_FRESHNESS_V1"
KIND_WINDOW = "FRESHNESS_WINDOW"
KIND_SAMPLE = "FRESHNESS_SAMPLE"
#: one frozen membership per UTC hour
WINDOW_S = 3600.0
#: one sample a minute, by the plane's freshness task
SAMPLE_EVERY_S = 60.0
#: a sample stands for at most two sample intervals; past it, OUTAGE
CARRY_S = 120.0
#: the member list's bound (the refresher's MAX_TRACKED)
MAX_MEMBERS = 2000
JOINED_SAMPLE = 20
REASONS_TOP = 12

TIER_HELD = "HELD_POSITION"
TIER_ORDER = "WORKING_ORDER"
TIER_CANDIDATE = "CANDIDATE"
TIERS = (TIER_HELD, TIER_ORDER, TIER_CANDIDATE)
VIEW_MANAGEMENT = "HELD_POSITION_OR_WORKING_ORDER"

VENUE_PMUS = "POLYMARKET_US"
VENUE_KALSHI = "KALSHI"

#: the PAPER ledger's working-order states (bettor_paper_ledger.OPEN_STATES;
#: a test pins the equality -- this module must not import the ledger)
OPEN_ORDER_STATES = ("PENDING_SIMULATION", "RESTING", "PARTIALLY_FILLED",
                     "CANCEL_PENDING")

C_STREAM, C_REFRESH, C_SNAPSHOT, C_PAPER, C_KALSHI = "S", "R", "G", "P", "K"
C_NOT, C_UNHELD, C_EXTERNAL = "N", "U", "X"
CURRENT_CODES = frozenset((C_STREAM, C_REFRESH, C_SNAPSHOT, C_PAPER,
                           C_KALSHI))
CODE_NAMES = {
    C_STREAM: "CURRENT_PMX_STREAM", C_REFRESH: "CURRENT_PLANE_REFRESH",
    C_SNAPSHOT: "CURRENT_PLANE_SNAPSHOT", C_PAPER: "CURRENT_PAPER_REST",
    C_KALSHI: "CURRENT_KALSHI_BOOK", C_NOT: "NOT_CURRENT",
    C_UNHELD: "NOT_HELD_BY_THE_PLANE", C_EXTERNAL: "EXTERNAL_UNAVAILABLE"}

#: the market families the readback reports (owner directive 2D)
F_MONEYLINE, F_SPREAD, F_TOTAL = "MONEYLINE", "SPREAD", "TOTAL"
F_TEAM_TOTAL, F_FUTURE, F_OTHER = "TEAM_TOTAL", "FUTURE", "OTHER"
F_UNKNOWN = "UNKNOWN"
FAMILIES = (F_MONEYLINE, F_SPREAD, F_TOTAL, F_TEAM_TOTAL, F_FUTURE, F_OTHER,
            F_UNKNOWN)
LINE_FAMILIES = (F_SPREAD, F_TOTAL, F_TEAM_TOTAL)
#: the overlay: every line of an event x family x period with more than one
#: line in the frozen set. The venue's market type does not name a main
#: line (bettor_market_family.VENUE_LINE_TYPES), so no line is called the
#: main one; each stays in its own family as well.
ALT_LINE = "ALTERNATIVE_LINE"
FUTURE_METRICS = frozenset({"CHAMPION", "AWARD_WINNER", "SEASON_WINS"})
WINNER_METRICS = frozenset({"WINNER"})

#: THE VENUE'S MARKET STATES THAT MAKE A MEMBER EXTERNAL_UNAVAILABLE: the
#: held-position rule's sets (bettor_paper_freshness.TERMINAL_MARKET_STATES /
#: TRANSIENT_NOT_OPEN_STATES; a test pins them) plus the institutional
#: enum's names (vendor.pmx_proto InstrumentState)
TERMINAL_STATES = frozenset({
    "MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED",
    "MARKET_STATE_MATCH_AND_CLOSE_AUCTION", "MARKET_STATE_SETTLED",
    "MARKET_STATE_RESOLVED", "EXPIRED", "CLOSED", "SETTLED", "RESOLVED",
    "INSTRUMENT_STATE_CLOSED", "INSTRUMENT_STATE_EXPIRED",
    "INSTRUMENT_STATE_TERMINATED",
    "INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION"})
TRANSIENT_STATES = frozenset({
    "MARKET_STATE_HALTED", "MARKET_STATE_SUSPENDED", "MARKET_STATE_PREOPEN",
    "MARKET_STATE_PAUSED", "HALTED", "SUSPENDED", "PREOPEN", "PAUSED",
    "INSTRUMENT_STATE_PREOPEN", "INSTRUMENT_STATE_SUSPENDED",
    "INSTRUMENT_STATE_HALTED", "INSTRUMENT_STATE_PENDING"})

#: active_refresh.R_REFRESH_NOT_OPEN (the REST book's state was not open)
REFRESH_NOT_OPEN = "ACTIVE_REFRESH_MARKET_NOT_OPEN"

RULE = ("rate = fresh member-seconds / eligible member-seconds over the "
        "observation window; a member is fresh when a book of it was "
        "received inside the bound (S stream, R plane REST refresh, G plane "
        "snapshot refresh, P paper REST read, K Kalshi book); a sample "
        "stands for at most %.0f s and every second no sample covers is an "
        "OUTAGE counted eligible and not fresh; EXTERNAL_UNAVAILABLE (the "
        "venue's own not-open state, the held-position rule) is excluded "
        "from eligible and counted; the membership is frozen per %.0f s "
        "window with its sha256" % (CARRY_S, WINDOW_S))


# ═════════════════════════════════════════════════════════════════════
# PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        try:
            return float(v.timestamp())
        except (TypeError, ValueError, OSError):
            return None
    if isinstance(v, (int, float)):
        return float(v)
    from .. import institutional_same_book as SB
    return SB._epoch_of(v)


def window_start_of(now: float, window_s: float = WINDOW_S) -> float:
    return float(int(float(now) // window_s) * window_s)


def family_of(market_type, family=None) -> str:
    """The reporting family of one contract, from the venue's market type
    and the ontology's metric (registry `family`). Never a guess: what no
    rule names is OTHER, and a contract with neither is UNKNOWN."""
    mt = str(market_type or "").lower()
    fa = str(family or "").upper()
    if not mt and not fa:
        return F_UNKNOWN
    if "future" in mt or fa in FUTURE_METRICS:
        return F_FUTURE
    if fa in WINNER_METRICS or "moneyline" in mt or mt.endswith(
            ("_winner",)):
        return F_MONEYLINE
    if fa == "TEAM_SCORE" or "team_total" in mt or (
            "team_points" in mt and "total" in mt):
        return F_TEAM_TOTAL
    if fa == "MARGIN" or "spread" in mt:
        return F_SPREAD
    if "total" in mt:
        return F_TOTAL
    return F_OTHER


def phase_at(event_start, now: float) -> str:
    from .active_refresh import phase_of
    return phase_of(event_start, now=now)


def membership_hash(members) -> str:
    """sha256 of the sorted "tier|venue|contract_id" lines."""
    lines = sorted("%s|%s|%s" % (m["tier"], m["venue"], m["contract_id"])
                   for m in members)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def freeze(members, *, window_start: float, now: float, sla_s: float,
           window_s: float = WINDOW_S) -> dict:
    """PURE. The eligible members -> the window's frozen membership: sorted
    by contract id, the alternative-line overlay set, hashed."""
    ms = sorted(({k: m.get(k) for k in (
        "contract_id", "tier", "venue", "family", "period", "event_start",
        "event_id", "line", "orders")} for m in members),
        key=lambda m: m["contract_id"])[:MAX_MEMBERS]
    groups: dict = {}
    for m in ms:
        if m["family"] in LINE_FAMILIES and m.get("event_id"):
            k = (m["event_id"], m["family"], m.get("period"))
            groups.setdefault(k, set()).add(str(m.get("line")))
    out = []
    for m in ms:
        k = (m.get("event_id"), m["family"], m.get("period"))
        alt = bool(m["family"] in LINE_FAMILIES and len(groups.get(k, ())) > 1)
        out.append([m["contract_id"], m["tier"], m["venue"], m["family"],
                    m.get("period"), m.get("event_start"), int(alt),
                    int(bool(m.get("orders")))])
    return {"version": VERSION, "window_start": float(window_start),
            "window_s": float(window_s), "frozen_at": float(now),
            "sla_s": float(sla_s), "n": len(out),
            "membership_hash": membership_hash(
                [{"contract_id": r[0], "tier": r[1], "venue": r[2]}
                 for r in out]),
            "fields": ["contract_id", "tier", "venue", "family", "period",
                       "event_start", "alternative_line", "working_order"],
            "members": out,
            "basis": ("frozen at the window's first sample: active registry "
                      "priority members (HELD_POSITION <= P_HELD, CANDIDATE "
                      "<= P_CANDIDATE) and every market a PAPER order is "
                      "working on (WORKING_ORDER); never re-chosen")}


def member_dicts(win: dict) -> list:
    f = win.get("fields") or []
    return [dict(zip(f, r)) for r in (win.get("members") or [])]


def _dist(xs) -> dict | None:
    xs = sorted(float(x) for x in xs if x is not None)
    if not xs:
        return None

    def q(p):
        return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 1)
    return {"n": len(xs), "p50": q(0.5), "p90": q(0.9), "max": round(
        xs[-1], 1)}


# ═════════════════════════════════════════════════════════════════════
# ONE MEMBER'S STATE AT ONE INSTANT
# ═════════════════════════════════════════════════════════════════════

def external_from(state, *, read_at, now: float, sla_s: float,
                  source: str):
    """(True, why) when the venue's own `state`, read at `read_at`, makes
    the member EXTERNAL_UNAVAILABLE under the held-position rule: terminal
    at any age, not-open while read inside the bound."""
    st = str(state or "").upper()
    if not st:
        return False, None
    if st in TERMINAL_STATES:
        return True, "%s:MARKET_STATE_TERMINAL:%s" % (source, st)
    if st in TRANSIENT_STATES and read_at is not None and \
            0.0 <= now - float(read_at) <= sla_s:
        return True, "%s:MARKET_NOT_OPEN:%s" % (source, st)
    return False, None


def classify(m: dict, *, mgr, refreshed: dict, entries: dict, paper: dict,
             kalshi: dict, now: float, sla_s: float) -> tuple:
    """(code, why, receipt_at, source_at) for one frozen member at `now`.
    `refreshed` {symbol: (receipt, code)} are the refresher's members
    current through it now; `entries` its records (state of a not-open
    read); `paper` {slug: newest successful paper book read (6 h)};
    `kalshi` {ticker: current Kalshi book row}. Reads the books, writes
    nothing."""
    s = m["contract_id"]
    if m.get("venue") == VENUE_KALSHI:
        k = kalshi.get(s)
        if k is None:
            return C_NOT, "KALSHI:NO_BOOK_HELD", None, None
        at = _epoch(k.get("observed_at"))
        if k.get("readable") and not k.get("error") and at is not None \
                and 0.0 <= now - at <= sla_s:
            return C_KALSHI, None, at, None
        return C_NOT, ("KALSHI:BOOK_OLDER_THAN_THE_BOUND"
                       if k.get("readable") else "KALSHI:BOOK_NOT_READABLE"
                       ), at, None
    held = mgr is not None and s in (getattr(mgr, "symbol_to_shard", None)
                                     or {})
    cur = None
    if held:
        try:
            cur = mgr.current(s, now=now, max_snapshot_age_s=sla_s) or {}
        except Exception as exc:                                # noqa: BLE001
            cur = {"ok": False, "refusal": "READ_RAISED:%s"
                   % type(exc).__name__}
        ev = cur.get("evidence") or {}
        sn = ev.get("snapshot") or {}
        if cur.get("ok"):
            return (C_STREAM, None, _epoch(sn.get("received_at")),
                    _epoch(sn.get("venue_ts")))
    got = refreshed.get(s)
    if got is not None:
        e = entries.get(s) or {}
        return got[1], None, got[0], _epoch(e.get("venue_ts"))
    p = paper.get(s)
    p_at = None if p is None else _epoch(p.get("at"))
    if p_at is not None and 0.0 <= now - p_at <= sla_s:
        # the held-position rule's order: the read's own not-open state
        # first (a current read of a closed market is not a current book)
        ext, why = external_from(p.get("market_state"), read_at=p_at,
                                 now=now, sla_s=sla_s, source="PAPER_REST")
        if ext:
            return C_EXTERNAL, why, None, None
        return C_PAPER, None, p_at, _epoch(p.get("venue_ts"))
    # NOT CURRENT: is the market itself not open, by the venue's own word?
    if cur is not None:
        mk = (cur.get("evidence") or {}).get("market") or {}
        sn = (cur.get("evidence") or {}).get("snapshot") or {}
        if mk.get("state_source") == "STREAM":
            ext, why = external_from(mk.get("state"),
                                     read_at=sn.get("received_at"), now=now,
                                     sla_s=sla_s, source="STREAM")
            if ext:
                return C_EXTERNAL, why, None, None
    e = entries.get(s) or {}
    if e.get("outcome") == REFRESH_NOT_OPEN and e.get("tried_at") is not None \
            and 0.0 <= now - float(e["tried_at"]) <= sla_s:
        # the plane's own REST read of the venue's book said not open,
        # inside the bound
        return C_EXTERNAL, "REFRESH:MARKET_NOT_OPEN", None, None
    if p is not None:
        ext, why = external_from(p.get("market_state"), read_at=p_at,
                                 now=now, sla_s=sla_s, source="PAPER_REST")
        if ext:
            return C_EXTERNAL, why, None, None
    if not held:
        return C_UNHELD, "NOT_HELD_BY_THE_PLANE_BOOKS", None, None
    why = "STREAM:%s" % str(cur.get("refusal") or "NOT_CURRENT")[:60]
    if e.get("outcome") and e.get("outcome") != "CURRENT":
        why += "|REFRESH:%s" % str(e.get("outcome"))[:60]
    return C_NOT, why, _epoch(((cur.get("evidence") or {}).get(
        "snapshot") or {}).get("received_at")), None


# ═════════════════════════════════════════════════════════════════════
# THE PLANE'S SIDE: freeze, sample, persist (the freshness task)
# ═════════════════════════════════════════════════════════════════════

PRIORITY_SQL = (
    "SELECT contract_id, venue, priority, market_type, family, period, "
    "       event_id, event_start, ontology->'meaning'->>'line' AS line "
    "  FROM market_plane_registry WHERE active AND priority <= $1 "
    " ORDER BY contract_id LIMIT $2")
ORDERS_SQL = (
    "SELECT DISTINCT us_market_slug AS slug FROM paper_orders "
    " WHERE state = ANY($1::text[]) AND us_market_slug IS NOT NULL")
ROWS_SQL = (
    "SELECT contract_id, venue, priority, market_type, family, period, "
    "       event_id, event_start, ontology->'meaning'->>'line' AS line "
    "  FROM market_plane_registry WHERE contract_id = ANY($1::text[])")
PAPER_SQL = (
    "SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, "
    "       extract(epoch FROM observed_at)::float8 AS at, venue_ts, "
    "       market_state "
    "  FROM paper_book_observations "
    " WHERE us_market_slug = ANY($1::text[]) AND error IS NULL "
    "   AND observed_at > to_timestamp($2) - interval '6 hours' "
    " ORDER BY us_market_slug, observed_at DESC")
KALSHI_SQL = (
    "SELECT ticker, readable, error, observed_at FROM kalshi_books_current "
    " WHERE ticker = ANY($1::text[])")
WINDOW_READ_SQL = (
    "SELECT payload FROM market_plane_events WHERE event_key = $1")


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _has(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


async def eligible(conn) -> list:
    """The eligible members NOW (the freeze's input): registry priority
    members and the markets of working PAPER orders, each with its tier
    (HELD_POSITION > WORKING_ORDER > CANDIDATE), venue, family, period,
    event start, event id and line."""
    from .populate import P_CANDIDATE, P_HELD
    rows = {r["contract_id"]: dict(r) for r in await conn.fetch(
        PRIORITY_SQL, P_CANDIDATE, MAX_MEMBERS)}
    orders = set()
    if await _has(conn, "paper_orders"):
        orders = {r["slug"] for r in await conn.fetch(
            ORDERS_SQL, list(OPEN_ORDER_STATES))}
    extra = sorted(orders - set(rows))
    if extra:
        for r in await conn.fetch(ROWS_SQL, extra):
            rows[r["contract_id"]] = dict(r, priority=None)
    out = []
    for s in sorted(set(rows) | orders):
        r = rows.get(s) or {"contract_id": s, "venue": VENUE_PMUS,
                            "priority": None}
        pr = r.get("priority")
        tier = (TIER_HELD if pr is not None and int(pr) <= P_HELD else
                TIER_ORDER if s in orders else TIER_CANDIDATE)
        out.append({"contract_id": s, "tier": tier,
                    "venue": r.get("venue") or VENUE_PMUS,
                    "family": family_of(r.get("market_type"),
                                        r.get("family")),
                    "period": r.get("period"),
                    "event_start": _epoch(r.get("event_start")),
                    "event_id": r.get("event_id"), "line": r.get("line"),
                    "orders": s in orders})
    return out


async def load_or_freeze(conn, *, now: float, sla_s: float,
                         window_s: float = WINDOW_S) -> dict:
    """This window's frozen membership: the persisted one when the window
    already has it (a restart inside the window), else frozen now from
    `eligible` and persisted ONCE (ON CONFLICT DO NOTHING, then re-read:
    the first one written is the window's)."""
    from . import registry as R
    ws = window_start_of(now, window_s)
    key = "fwin:%d" % int(ws)
    got = _j(await conn.fetchval(WINDOW_READ_SQL, key))
    if isinstance(got, dict) and got.get("members") is not None:
        return dict(got, loaded=True)
    win = freeze(await eligible(conn), window_start=ws, now=now,
                 sla_s=sla_s, window_s=window_s)
    await R.record_event(conn, KIND_WINDOW, key, win)
    got = _j(await conn.fetchval(WINDOW_READ_SQL, key))
    return dict(got if isinstance(got, dict) else win, loaded=False)


def refreshed_codes(refresher, mgr, *, now: float, sla_s: float) -> dict:
    """{symbol: (receipt, code)} of the refresher's members current through
    it now: R for a REST read, G for a snapshot-only gRPC read."""
    if refresher is None or mgr is None:
        return {}
    out = {}
    origin_of = getattr(refresher, "origin_of", None)
    for s, at in (refresher.current(mgr, now=now, bound=sla_s) or {}).items():
        origin = origin_of(s) if origin_of is not None else None
        out[s] = (at, C_SNAPSHOT if origin == "SNAPSHOT" else C_REFRESH)
    return out


async def sample(conn, mgr, refresher, win: dict, *, now: float,
                 sla_s: float, joined_members=None) -> dict:
    """ONE SAMPLE of the frozen membership at `now` (and of the members that
    joined since the freeze, apart). Reads; persists nothing."""
    members = member_dicts(win)
    joined = list(joined_members or ())
    every = members + joined
    slugs = sorted({m["contract_id"] for m in every
                    if m.get("venue") != VENUE_KALSHI})
    tickers = sorted({m["contract_id"] for m in every
                      if m.get("venue") == VENUE_KALSHI})
    paper = {}
    if slugs and await _has(conn, "paper_book_observations"):
        paper = {r["slug"]: dict(r) for r in await conn.fetch(
            PAPER_SQL, slugs, float(now))}
    kalshi = {}
    if tickers and await _has(conn, "kalshi_books_current"):
        kalshi = {r["ticker"]: dict(r) for r in await conn.fetch(
            KALSHI_SQL, tickers)}
    refreshed = refreshed_codes(refresher, mgr, now=now, sla_s=sla_s)
    entries = getattr(refresher, "entries", None) or {}

    def run(ms):
        codes, reasons, rcv, src = [], {}, [], []
        for m in ms:
            code, why, r_at, s_at = classify(
                m, mgr=mgr, refreshed=refreshed, entries=entries,
                paper=paper, kalshi=kalshi, now=now, sla_s=sla_s)
            codes.append(code)
            if code in CURRENT_CODES:
                if r_at is not None:
                    rcv.append(now - r_at)
                if s_at is not None:
                    src.append(now - s_at)
            elif why:
                k = why.split("|")[0] if code != C_NOT else why
                reasons[k] = reasons.get(k, 0) + 1
        return "".join(codes), reasons, rcv, src
    codes, reasons, rcv, src = run(members)
    j_codes, _jr, _a, _b = run(joined)
    counts = {CODE_NAMES[c]: codes.count(c) for c in CODE_NAMES
              if codes.count(c)}
    conn_d = {}
    if mgr is not None:
        try:
            d = (mgr.shard_digest() or [{}])[0]
            conn_d = {"connected": d.get("connected"),
                      "state": d.get("state"), "attempts": d.get("attempts")}
        except Exception:                                       # noqa: BLE001
            conn_d = {}
    return {"version": VERSION, "window_start": win.get("window_start"),
            "membership_hash": win.get("membership_hash"),
            "n": len(members), "verified_at": float(now),
            "sla_s": float(sla_s), "codes": codes, "counts": counts,
            "reasons": dict(sorted(reasons.items(),
                                   key=lambda kv: -kv[1])[:REASONS_TOP]),
            "times": {
                "verified_at": float(now),
                "receipt_age_s": _dist(rcv),
                "source_event_age_s": _dist(src),
                "source_event_note": (
                    "the venue's transactTime; its meaning is not documented"
                    " (research/p5_live_book_currency_review.md): reported, "
                    "never used to judge currency")},
            "stream": conn_d,
            "joined": {"n": len(joined), "codes": j_codes,
                       "ids": [m["contract_id"] for m in joined][
                           :JOINED_SAMPLE],
                       "tiers": [m["tier"] for m in joined][:JOINED_SAMPLE]}}


async def step(pool, mgr, refresher, st: dict, *, now: float,
               sla_s: float, window_s: float = WINDOW_S) -> dict | None:
    """ONE SAMPLER TICK (the plane's freshness task): when SAMPLE_EVERY_S
    has passed, make sure this window's membership is frozen, sample it,
    persist the sample ("fsample:<minute>", ON CONFLICT DO NOTHING).
    Returns the sample's digest, or None when not due."""
    from . import registry as R
    if st.get("sampled_at") is not None and \
            now - st["sampled_at"] < SAMPLE_EVERY_S:
        return None
    st["sampled_at"] = now
    async with pool.acquire() as c:
        win = st.get("window")
        if win is None or win.get("window_start") != window_start_of(
                now, window_s):
            win = await load_or_freeze(c, now=now, sla_s=sla_s,
                                       window_s=window_s)
            st["window"] = win
        frozen = {m[0] for m in win.get("members") or ()}
        joined = [m for m in await eligible(c)
                  if m["contract_id"] not in frozen]
        smp = await sample(c, mgr, refresher, win, now=now, sla_s=sla_s,
                           joined_members=joined)
        await R.record_event(c, KIND_SAMPLE, "fsample:%d" % int(now // 60),
                             smp)
    st["samples"] = int(st.get("samples") or 0) + 1
    st["last"] = {"verified_at": now, "n": smp["n"],
                  "current": sum(1 for x in smp["codes"]
                                 if x in CURRENT_CODES),
                  "external": smp["codes"].count(C_EXTERNAL),
                  "joined": smp["joined"]["n"],
                  "membership_hash": smp["membership_hash"],
                  "window_start": smp["window_start"]}
    return dict(st["last"])


# ═════════════════════════════════════════════════════════════════════
# THE READBACK'S SIDE: the window integral (pure) and its reader
# ═════════════════════════════════════════════════════════════════════

def _static_groups(m: dict) -> tuple:
    """The member's subgroups that do not move with time (all but phase)."""
    g = ["ALL", "tier:%s" % m.get("tier"), "venue:%s" % m.get("venue"),
         "family:%s" % m.get("family"),
         "period:%s" % (m.get("period") or "UNKNOWN")]
    if m.get("tier") in (TIER_HELD, TIER_ORDER) or m.get("working_order"):
        g.append("view:%s" % VIEW_MANAGEMENT)
    if m.get("alternative_line"):
        g.append("family:%s" % ALT_LINE)
    return tuple(g)


_FIELDS = ("fresh_member_s", "eligible_member_s", "external_member_s",
           "outage_member_s")


class _Acc:
    """Member-seconds per subgroup. A sample's members are first counted by
    (static subgroups, phase, state class) -- few distinct keys -- and each
    key's count x the span is then added to every subgroup it names."""

    def __init__(self):
        self.acc: dict = {}

    def add(self, keys, phase: str, field: str, v: float):
        for g in keys + ("phase:%s" % phase,):
            a = self.acc.get(g)
            if a is None:
                a = self.acc[g] = dict.fromkeys(_FIELDS, 0.0)
            a[field] += v


def integrate(windows: list, samples: list, *, start: float, end: float,
              carry_s: float = CARRY_S, measured_since=None) -> dict:
    """PURE. The time-weighted freshness of the frozen memberships over
    [start, end] (module docstring). `windows` are FRESHNESS_WINDOW
    payloads, `samples` FRESHNESS_SAMPLE payloads (any order). A sample
    whose membership hash is not its window's is discarded and named; its
    time is outage.

    `measured_since` is when this build began measuring: the frozen_at of
    the FIRST window ever written (`fetch` reads it). Only time before it
    is "not measured"; every later second no sample covers is OUTAGE, the
    newest membership frozen before it carried -- whatever window the
    caller happened to read first (review of 785907f2: an outage longer
    than the read's lead that crossed a horizon's start was left out of the
    denominator). Without it (a pure caller handing every window), the
    first window given stands for it, and the readback says so."""
    wins = sorted((w for w in windows if isinstance(w, dict)
                   and w.get("window_start") is not None),
                  key=lambda w: float(w["window_start"]))
    by_ws = {float(w["window_start"]): w for w in wins}
    wins = [by_ws[k] for k in sorted(by_ws)]
    mem = {}
    for ws, w in by_ws.items():
        ms = member_dicts(w)
        mem[ws] = [(_static_groups(m), m.get("event_start")) for m in ms]
    bad, smp = [], []
    for s in samples:
        w = by_ws.get(float(s.get("window_start") or -1))
        if w is None or s.get("membership_hash") != w.get(
                "membership_hash") or len(s.get("codes") or "") != len(
                mem.get(float(w["window_start"])) or ()):
            bad.append({"verified_at": s.get("verified_at"),
                        "why": "SAMPLE_DOES_NOT_MATCH_ITS_FROZEN_WINDOW"})
            continue
        smp.append(s)
    smp.sort(key=lambda s: float(s["verified_at"]))
    first = None
    if wins:
        first = (float(measured_since) if measured_since is not None
                 else min(float(w.get("frozen_at") or w["window_start"])
                          for w in wins))
    t0 = max(float(start), first) if first is not None else None
    acc = _Acc()
    if t0 is None or t0 >= end:
        return {"status": "UNMEASURED", "why": (
            "NO_FROZEN_WINDOW_IN_THE_HORIZON" if t0 is None
            else "MEASUREMENT_STARTS_AFTER_THE_HORIZON"),
            "groups": {}, "measured_from": t0,
            "unmeasured_before_s": round(float(end) - float(start), 1),
            "discarded_samples": bad}
    W = float(wins[0].get("window_s") or WINDOW_S)

    def members_for(at: float):
        ws = float(int(at // W) * W)
        if ws in mem:
            return mem[ws], ws, False
        prior = [x for x in sorted(mem) if x <= ws]
        if prior:
            return mem[prior[-1]], prior[-1], True
        return [], None, False
    covered = 0.0
    outages, carried = [], set()
    per_sample = []
    no_membership = [0.0]

    def outage(a: float, b: float):
        # split at window boundaries; every frozen member eligible, not fresh
        x = a
        while x < b - 1e-9:
            nxt = min(b, (int(x // W) + 1) * W)
            ms, ws, car = members_for(x)
            if car:
                carried.add(ws)
            if ws is None:
                # measured time with no membership frozen at or before it:
                # only a caller that withheld the windows can make it; it
                # is named, never read as fresh
                no_membership[0] += nxt - x
            mid = (x + nxt) / 2.0
            cnt: dict = {}
            for keys, st in ms:
                k = (keys, phase_at(st, mid))
                cnt[k] = cnt.get(k, 0) + 1
            for (keys, ph), n in cnt.items():
                acc.add(keys, ph, "eligible_member_s", n * (nxt - x))
                acc.add(keys, ph, "outage_member_s", n * (nxt - x))
            x = nxt
        outages.append((a, b))

    cursor = t0
    for i, s in enumerate(smp):
        ts = float(s["verified_at"])
        if ts >= end:
            break
        nxt = float(smp[i + 1]["verified_at"]) if i + 1 < len(smp) else end
        a = max(ts, t0)
        # a sample stands for its own frozen membership, across an hour
        # boundary too, until the next sample or CARRY_S
        b = min(nxt, ts + carry_s, end)
        if a > cursor + 1e-9:
            outage(cursor, a)
        if b <= a:
            cursor = max(cursor, a)
            continue
        ms = mem[float(s["window_start"])]
        span = b - a
        cnt: dict = {}
        cur_n = elig_n = 0
        for (keys, st), c in zip(ms, s["codes"]):
            cls = ("X" if c == C_EXTERNAL else
                   "F" if c in CURRENT_CODES else "N")
            k = (keys, phase_at(st, ts), cls)
            cnt[k] = cnt.get(k, 0) + 1
            if cls != "X":
                elig_n += 1
                cur_n += int(cls == "F")
        for (keys, ph, cls), n in cnt.items():
            if cls == "X":
                acc.add(keys, ph, "external_member_s", n * span)
                continue
            acc.add(keys, ph, "eligible_member_s", n * span)
            if cls == "F":
                acc.add(keys, ph, "fresh_member_s", n * span)
        per_sample.append(None if not elig_n else cur_n / elig_n)
        covered += span
        cursor = b
    if cursor < end - 1e-9:
        outage(cursor, end)
    groups = {}
    for k, a in sorted(acc.acc.items()):
        e = a["eligible_member_s"]
        groups[k] = {kk: round(v, 1) for kk, v in a.items()}
        groups[k]["rate"] = round(a["fresh_member_s"] / e, 4) if e > 0 \
            else None
        tot = e + a["external_member_s"]
        groups[k]["rate_external_counted_not_fresh"] = (
            round(a["fresh_member_s"] / tot, 4) if tot > 0 else None)
    inst = sorted(x for x in per_sample if x is not None)
    span_s = float(end) - t0
    longest = sorted(outages, key=lambda o: -(o[1] - o[0]))[:5]
    return {"status": "MEASURED", "measured_from": t0, "end": float(end),
            "observation_s": round(span_s, 1),
            "unmeasured_before_s": round(t0 - float(start), 1),
            "covered_s": round(covered, 1),
            "outage_s": round(span_s - covered, 1),
            "outage_without_membership_s": round(no_membership[0], 1),
            "outage_intervals": len(outages),
            "longest_outages": [{"from": round(a, 1), "to": round(b, 1),
                                 "s": round(b - a, 1)} for a, b in longest],
            "samples_used": len(per_sample),
            "discarded_samples": bad[:10],
            "discarded_samples_n": len(bad),
            "carried_windows": sorted(carried),
            "instantaneous_rate": (None if not inst else {
                "n": len(inst), "min": round(inst[0], 4),
                "p10": round(inst[int(0.1 * (len(inst) - 1))], 4),
                "p50": round(inst[len(inst) // 2], 4),
                "max": round(inst[-1], 4)}),
            "groups": groups}


SAMPLES_SQL = (
    "SELECT payload FROM market_plane_events "
    " WHERE kind = $1 AND at > to_timestamp($2) - interval '5 minutes' "
    "   AND at <= to_timestamp($3) + interval '5 minutes'")
#: the readback's horizons (each clamped to when this build began measuring)
HORIZONS = (("1h", 3600.0), ("6h", 21600.0), ("24h", 86400.0))
#: every subgroup the directive names, reported MEASURED or, with no frozen
#: member in the horizon, NOT_IN_ELIGIBLE_SET -- never silently absent
DECLARED_GROUPS = (
    ["ALL", "view:%s" % VIEW_MANAGEMENT]
    + ["tier:%s" % t for t in TIERS]
    + ["venue:%s" % v for v in (VENUE_PMUS, VENUE_KALSHI)]
    + ["family:%s" % f for f in FAMILIES + (ALT_LINE,)])


#: THE WINDOWS THE READBACK NEEDS, in one pass over the window events' keys
#: (the payloads are then read by primary key): every window touching
#: [start - 2 windows, end]; the NEWEST window at or before `start`, at any
#: age (the membership an outage crossing the start carries -- however long
#: the plane was down); and the FIRST window ever written (when this build
#: began measuring: only time before it is "not measured").
WINDOW_KEYS_SQL = (
    "WITH w AS MATERIALIZED ("
    "  SELECT event_key, at FROM market_plane_events WHERE kind = $1) "
    "SELECT event_key, 'RANGE' AS why FROM w "
    " WHERE at > to_timestamp($2) - interval '5 minutes' "
    "   AND at <= to_timestamp($3) + interval '5 minutes' "
    "UNION ALL (SELECT event_key, 'PRIOR' FROM w "
    " WHERE at <= to_timestamp($4) ORDER BY at DESC LIMIT 1) "
    "UNION ALL (SELECT event_key, 'FIRST' FROM w ORDER BY at LIMIT 1)")
PAYLOADS_SQL = (
    "SELECT event_key, payload FROM market_plane_events "
    " WHERE event_key = ANY($1::text[])")


async def fetch(conn, *, start: float, end: float) -> tuple:
    """(window payload texts, sample payload texts, measured_since) for
    [start, end]: the windows WINDOW_KEYS_SQL names (the range, the newest
    at or before the start at any age, the first ever), the samples
    touching the range, and the first window's frozen_at -- None when no
    window was ever written. Read only; parsing is left to `readback`, off
    the loop."""
    keys = await conn.fetch(WINDOW_KEYS_SQL, KIND_WINDOW,
                            float(start) - 2 * WINDOW_S, float(end),
                            float(start))
    first_key = next((r["event_key"] for r in keys if r["why"] == "FIRST"),
                     None)
    pay = {r["event_key"]: r["payload"] for r in await conn.fetch(
        PAYLOADS_SQL, sorted({r["event_key"] for r in keys}))}
    measured_since = None
    if first_key is not None:
        fw = _j(pay.get(first_key))
        if isinstance(fw, dict) and fw.get("frozen_at") is not None:
            measured_since = float(fw["frozen_at"])
    smps = [r["payload"] for r in await conn.fetch(
        SAMPLES_SQL, KIND_SAMPLE, float(start) - CARRY_S, float(end))]
    return [pay[k] for k in sorted(pay)], smps, measured_since


def readback(wins, smps, measured_since=None, *, now: float,
             horizons=HORIZONS) -> dict:
    """PURE (CPU: run it off the event loop). The readback of every horizon:
    the window integral, the frozen windows with their hashes, the declared
    subgroups' status, and the newest sample with its three instants.
    `measured_since` is `fetch`'s third value (the first window ever
    written); without it the first window given stands for it, labelled."""
    wins = [w for w in (_j(x) for x in wins) if isinstance(w, dict)]
    smps = [x for x in (_j(x) for x in smps) if isinstance(x, dict)]
    if measured_since is None and wins:
        basis = "FIRST_WINDOW_GIVEN_TO_THE_READBACK"
        measured_since = min(float(w.get("frozen_at") or w.get(
            "window_start") or 0) for w in wins)
    else:
        basis = ("FIRST_FRESHNESS_WINDOW_EVER_WRITTEN"
                 if measured_since is not None else None)
    out = {"version": VERSION, "rule": RULE, "sla_s": None,
           "window_s": WINDOW_S, "sample_every_s": SAMPLE_EVERY_S,
           "carry_s": CARRY_S,
           "measured_since": measured_since,
           "measured_since_basis": basis,
           "measured_since_note": (
               "time before the first frozen window is not measured by this "
               "build (unmeasured_before_s); every later second no sample "
               "covers is an OUTAGE in the denominator, the newest "
               "membership frozen before it carried"),
           "horizons": {}}
    carried = set()
    for k, h in horizons:
        got = integrate(wins, smps, start=float(now) - float(h),
                        end=float(now), measured_since=measured_since)
        got["horizon_s"] = float(h)
        got["declared_groups"] = {
            g: ("MEASURED" if g in (got.get("groups") or {})
                else "NOT_IN_ELIGIBLE_SET") for g in DECLARED_GROUPS}
        carried.update(float(x) for x in got.get("carried_windows") or ())
        out["horizons"][k] = got
    longest = max((float(h) for _k, h in horizons), default=0.0)
    start = float(now) - longest
    by_ws = {float(w.get("window_start") or 0): w for w in wins}
    out["windows"] = [{"window_start": w.get("window_start"),
                       "frozen_at": w.get("frozen_at"), "n": w.get("n"),
                       "membership_hash": w.get("membership_hash"),
                       "sla_s": w.get("sla_s"),
                       "carried_into_the_horizon": ws in carried}
                      for ws, w in sorted(by_ws.items())
                      if ws + WINDOW_S > start or ws in carried]
    if out["windows"]:
        out["sla_s"] = out["windows"][-1].get("sla_s")
    latest = max(smps, key=lambda s: float(s.get("verified_at") or 0),
                 default=None)
    if latest is not None:
        ls = {k: latest.get(k) for k in ("verified_at", "window_start",
                                          "membership_hash", "n", "counts",
                                          "reasons", "times", "stream")}
        ls["age_s"] = round(float(now) - float(latest.get("verified_at")
                                               or 0), 1)
        j = latest.get("joined") or {}
        ls["joined"] = {"n": j.get("n"), "ids": j.get("ids"),
                        "tiers": j.get("tiers"),
                        "current": sum(1 for c in (j.get("codes") or "")
                                       if c in CURRENT_CODES),
                        "note": ("eligible now, not in this window's frozen "
                                 "membership: measured here, counted from "
                                 "the next window")}
        out["latest_sample"] = ls
    return out
