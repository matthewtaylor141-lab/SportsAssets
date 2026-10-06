"""THE INSTITUTIONAL STREAM'S FOCUS UNIVERSE: WHAT BETTOR ACTUALLY HOLDS AND
EVALUATES, IN PRIORITY ORDER, EACH WITH ITS EXACT IDENTITY OR THE REASON IT
HAS NONE.

WHY. P5_LIVE_STREAM_BOOK_V1's same-book premise (S1) needs 30 comparable
stream-vs-retail samples at >= 95 % agreement. The stream, its evidence
recorder and the same-book probe used to hold only the experimental lane's
focus set (`shadow_experimental_store.focus_set`): 3-4 F1 / NFL-prop
instruments, one of which mapped exactly. The probe therefore never observed
the MLB / soccer contracts BETTOR evaluates, and 212 of 214 probes were
NOT_COMPARABLE. This module chooses what those three hold instead -- it does
not change the rule, its thresholds, its bounds or any gate.

THE TIERS, highest first (a contract is a member once, at its highest tier;
every tier it qualified for is kept in `reasons`):

  1 ACTUAL_OPEN_POSITION        smalllive_handoffs OPEN (Polymarket), the
                                execution mirror's net filled inventory not
                                yet settled, and its working live orders
  2 EXECUTION_INTENT            recent execution_intents of an allowlisted
                                strategy (actual_state <> PAPER_ONLY): live-
                                admissible first, then refused-at-admission
                                ("imminent": the decision that would execute
                                once its book currency is established)
  3 PAPER_INVESTMENT_POSITION   open paper positions of the investment
                                strategy PINNACLE_COMPLETED_GAME_PAPER
  4 V3_CANDIDATE                recent PINNACLE_COMPLETED_GAME_PAPER_V3
                                paper_decisions (ENTER first, then evaluated)
  5 MAPPED_INVESTMENT_UNIVERSE  retail-mapped (us_premap) contracts of a game
                                starting / in play now, in a sport the
                                investment strategy decided on in the last
                                7 days
  6 EXPLORATION_DIAGNOSTIC      open paper positions of every OTHER strategy
                                (exploration / training / benchmark):
                                observed for diagnostics only, ranked below
                                every investment tier, `diagnostic_only`
  7 BROADER_DISCOVERY           the experimental lane's focus set, last

BOUND. `MAX_MEMBERS` (32) = `institutional_api_stream.MAX_SYMBOLS`, the API
process's bootstrap / subscribe bound, which is the tighter of the two
stream limits (`institutional_stream.MAX_SYMBOLS` = 200 per process; a test
pins both). Members past the bound are counted per tier
(`dropped_beyond_bound`), never silently lost.

EXACT IDENTITY, NOTHING ELSE (`identify`). A member's institutional symbol is
established ONLY by `institutional_contract_map.map_retail_to_institutional`
(retail slug, YES = the long instrument's book, the venue's refdata record):
registered contract id = symbol = slug, the long side is the slug's first
participant, settles as a binary YES, the instrument's own scales, payout
$1.00. No fuzzy match, no title, no price. Anything else -- no refdata record
yet, not listed, a refusal -- is UNAVAILABLE with the reason, and an
UNAVAILABLE member is never subscribed, compared or priced. The member's own
position side is recorded beside it: the probe compares the instrument's ONE
book (the retail NO leg is the other side of that same book), while pricing
a decision stays the identity mapper's (YES / long only).

NOTHING HERE GRANTS ANYTHING. It reads tables and returns plain data; it
imports no execution, venue or paper module, writes only its own evidence
table, and every persisted row carries grants_live_eligibility = false and
orders_placed = 0 (both CHECKed, migration 213). Exploration stays paper-only:
its tier is diagnostic and nothing reads this module to decide eligibility.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone

from . import institutional_contract_map as ICM
from . import shadow_contract_family as CF

VERSION = "INSTITUTIONAL_FOCUS_UNIVERSE_V1"
TABLE = "institutional_focus_universe"

#: The investment strategy and the policy version whose candidates rank (the
#: strings paper_benchmark.CG_STRATEGY / CG_VERSION; a test pins them equal --
#: this module does not import the paper module).
INVESTMENT_STRATEGY = "PINNACLE_COMPLETED_GAME_PAPER"
INVESTMENT_VERSION = "PINNACLE_COMPLETED_GAME_PAPER_V3"

#: = institutional_api_stream.MAX_SYMBOLS (<= institutional_stream.MAX_SYMBOLS).
MAX_MEMBERS = 32
#: THE HELD-PAPER RESERVE (P0 market-data freshness, 2026-10-06). Of the
#: bound, up to this many slots go to HELD PAPER positions (tiers 3 and 6)
#: right after the actual / execution-intent tiers -- ahead of candidates,
#: the mapped universe and discovery -- so the symbols whose marks must be
#: refreshed inside the 300 s SLA are subscribed (and same-book probed, which
#: is what accumulates their PER-SYMBOL evidence). Tier names and ranks do not
#: change (exploration stays diagnostic_only, rank 6); a reserved member is
#: marked `held_reserve`. `compute` applies it; `prioritize` only when asked.
HELD_PAPER_RESERVE = 20

#: The held markets the held-mark refresh named in THIS process, in its own
#: priority order (paper_market_data.set_held fills it). A plain list of
#: slugs: this module imports nothing from the paper path.
_HELD_FIRST: list = []
#: How many held slugs are kept: = institutional_stream.MAX_SYMBOLS (a test
#: pins them equal), the most one process's stream will subscribe. The API
#: stream subscribes held markets beyond the focus bound up to its own
#: HELD_SYMBOL_BUDGET (institutional_api_stream).
HELD_FIRST_MAX = 200


def note_held_first(slugs) -> None:
    _HELD_FIRST[:] = [str(s) for s in slugs or () if s][:HELD_FIRST_MAX]


def held_first() -> list:
    return list(_HELD_FIRST)

T_ACTUAL = "ACTUAL_OPEN_POSITION"
T_INTENT = "EXECUTION_INTENT"
T_PAPER = "PAPER_INVESTMENT_POSITION"
T_CANDIDATE = "V3_CANDIDATE"
T_UNIVERSE = "MAPPED_INVESTMENT_UNIVERSE"
T_EXPLORATION = "EXPLORATION_DIAGNOSTIC"
T_DISCOVERY = "BROADER_DISCOVERY"
TIERS = (T_ACTUAL, T_INTENT, T_PAPER, T_CANDIDATE, T_UNIVERSE, T_EXPLORATION,
         T_DISCOVERY)
TIER_RANK = {t: i + 1 for i, t in enumerate(TIERS)}

EXACT = "EXACT"
UNAVAILABLE = "UNAVAILABLE"
U_SLUG = "RETAIL_SLUG_REFUSED"
U_NOT_READ = "INSTITUTIONAL_REFDATA_NOT_YET_READ"
U_NOT_LISTED = "INSTITUTIONAL_INSTRUMENT_NOT_LISTED"

#: Windows (seconds).
INTENT_WINDOW_S = 1800
CANDIDATE_WINDOW_S = 3600
POSITION_WINDOW_S = 7 * 86400
LEAGUE_LOOKBACK_S = 7 * 86400
GAME_PAST_S = 4 * 3600
GAME_AHEAD_S = 12 * 3600

_SLUG = re.compile(r"^[a-z0-9][a-z0-9.\-]{2,200}$")

# ── the readers, one per tier (read-only) ────────────────────────────

ACTUAL_HANDOFFS_SQL = """
    SELECT us_market_slug, opened_intent, group_id, handoff_id,
           first_live_fill_at AS at
      FROM smalllive_handoffs
     WHERE state = 'OPEN' AND venue = 'POLYMARKET'
     ORDER BY first_live_fill_at DESC
     LIMIT $1
"""
ACTUAL_INVENTORY_SQL = """
    SELECT us_market_slug,
           CASE WHEN intent LIKE '%SHORT%' THEN 'SHORT' ELSE 'LONG' END
               AS side,
           sum(CASE WHEN intent LIKE '%BUY%' THEN qty ELSE -qty END) AS net,
           max(observed_at) AS at
      FROM execmirror_fills f
     WHERE observed_at > now() - make_interval(secs => $2)
       AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                        WHERE s.us_market_slug = f.us_market_slug)
     GROUP BY 1, 2
    HAVING sum(CASE WHEN intent LIKE '%BUY%' THEN qty ELSE -qty END) > 1e-9
     ORDER BY at DESC
     LIMIT $1
"""
ACTUAL_ORDERS_SQL = """
    SELECT us_market_slug, intent, mirror_id, state,
           coalesce(submit_started_at, created_at) AS at
      FROM execmirror_orders
     WHERE state IN ('PLANNED','SUBMITTING','UNKNOWN','OPEN',
                     'PARTIALLY_FILLED')
     ORDER BY 5 DESC
     LIMIT $1
"""
INTENTS_SQL = """
    SELECT DISTINCT ON (us_market_slug)
           us_market_slug, intent_id, decision_id, strategy, policy_version,
           order_intent, holding_side, live_eligible, actual_state,
           actual_refusal, created_at AS at,
           evidence->'admission_facts'->'identity' AS identity
      FROM execution_intents
     WHERE created_at > now() - make_interval(secs => $1)
       AND actual_state <> 'PAPER_ONLY'
     ORDER BY us_market_slug, live_eligible DESC, created_at DESC
"""
#: EVERY CURRENTLY OPEN PAPER POSITION, whatever the age of its fills: the
#: canonical open quantity of bettor_paper_ledger.POSITIONS_SQL (bought -
#: sold - the latest settlement version's qty, per account / group / slug /
#: holding side) > 1e-9. It used to read only paper_fills inside a recent
#: window, so a position entered earlier was invisible here and its symbol
#: was never prioritised or subscribed -- while it was still held and still
#: needed a mark inside the SLA.
PAPER_POSITIONS_SQL = """
    WITH f AS (
        SELECT pf.account_id, pf.group_id, pf.us_market_slug,
               pf.holding_side, max(pf.strategy) AS strategy,
               (array_agg(pf.fixture ORDER BY pf.filled_at))[1] AS fixture,
               (array_agg(pf.label->>'event_key' ORDER BY pf.filled_at))[1]
                   AS event_key,
               sum(pf.qty) FILTER (WHERE pf.direction = 'BUY') AS bought,
               coalesce(sum(pf.qty) FILTER (WHERE pf.direction = 'SELL'), 0)
                   AS sold,
               max(pf.filled_at) AS at
          FROM paper_fills pf
         GROUP BY 1, 2, 3, 4),
    s AS (
        SELECT DISTINCT ON (position_key) position_key, qty
          FROM paper_settlements
         ORDER BY position_key, version DESC)
    SELECT f.*, coalesce(f.bought, 0) - f.sold - coalesce(s.qty, 0)
               AS open_qty
      FROM f LEFT JOIN s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE coalesce(f.bought, 0) - f.sold - coalesce(s.qty, 0) > 1e-9
       AND (coalesce(f.strategy, '') = $1) = $2
     ORDER BY f.at DESC
     LIMIT $3
"""
CANDIDATES_SQL = """
    SELECT DISTINCT ON (us_market_slug)
           us_market_slug, decision_id, verdict, refusal, intent,
           holding_side, fixture, label->>'event_key' AS event_key,
           policy_version, decided_at AS at
      FROM paper_decisions
     WHERE strategy = $1 AND policy_version = $2
       AND decided_at > now() - make_interval(secs => $3)
       AND us_market_slug IS NOT NULL
     ORDER BY us_market_slug, (verdict = 'ENTER') DESC, decided_at DESC
"""
UNIVERSE_SQL = """
    WITH lg AS (
        SELECT DISTINCT coalesce(p.sports_type, p.team_league) AS sport
          FROM paper_decisions d
          JOIN us_premap p ON p.market_slug = d.us_market_slug
         WHERE d.strategy = $1
           AND d.decided_at > now() - make_interval(secs => $2)
           AND coalesce(p.sports_type, p.team_league) IS NOT NULL)
    SELECT DISTINCT ON (p.market_slug)
           p.market_slug AS us_market_slug, p.event_slug, p.kind, p.line,
           p.side_norm, p.game_start AS at,
           coalesce(p.sports_type, p.team_league) AS sport
      FROM us_premap p JOIN lg ON lg.sport = coalesce(p.sports_type,
                                                      p.team_league)
     WHERE p.market_slug IS NOT NULL
       AND p.game_start BETWEEN now() - make_interval(secs => $3)
                            AND now() + make_interval(secs => $4)
     ORDER BY p.market_slug, p.updated_at DESC
"""

#: The tables each tier reads; a tier whose table is absent is UNMEASURED.
TIER_TABLES = {
    T_ACTUAL: ("smalllive_handoffs", "execmirror_fills", "execmirror_orders",
               "paper_settlements"),
    T_INTENT: ("execution_intents",),
    T_PAPER: ("paper_fills", "paper_settlements"),
    T_CANDIDATE: ("paper_decisions",),
    T_UNIVERSE: ("paper_decisions", "us_premap"),
    T_EXPLORATION: ("paper_fills", "paper_settlements"),
}


def _now():
    return datetime.now(tz=timezone.utc)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _side(v) -> str | None:
    s = str(v or "").strip().upper()
    if not s:
        return None
    if "SHORT" in s or s in ("NO", "BUY_NO"):
        return "SHORT"
    if "LONG" in s or s in ("YES", "BUY_YES"):
        return "LONG"
    return s


async def _has(db, table) -> bool:
    return bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                  table))


async def _rows(db, sql, *args) -> list:
    return [dict(r) for r in await db.fetch(sql, *args)]


async def gather(db, *, limit: int = MAX_MEMBERS) -> dict:
    """{tier: [candidate dict]} plus {tier: status}. Read-only; a tier whose
    tables are absent or whose read fails is UNMEASURED with the reason (it
    contributes nothing -- never a guess)."""
    n = max(1, int(limit)) * 2
    out: dict = {t: [] for t in TIERS}
    status: dict = {}
    for tier, tables in TIER_TABLES.items():
        missing = [t for t in tables if not await _has(db, t)]
        if missing:
            status[tier] = {"status": "UNMEASURED",
                            "why": "table(s) absent: %s" % ", ".join(missing)}
            continue
        try:
            out[tier] = await _READERS[tier](db, n)
            status[tier] = {"status": "MEASURED", "why": None}
        except Exception as exc:                              # noqa: BLE001
            status[tier] = {"status": "UNMEASURED",
                            "why": "read failed: %s" % type(exc).__name__}
    return {"candidates": out, "status": status}


async def _actual(db, n):
    out = []
    for r in await _rows(db, ACTUAL_HANDOFFS_SQL, n):
        out.append({"slug": r["us_market_slug"],
                    "side": _side(r["opened_intent"]), "at": r["at"],
                    "why": "smalllive_handoffs OPEN (actual live position)",
                    "refs": {"handoff_id": r["handoff_id"],
                             "group_id": r["group_id"]}})
    for r in await _rows(db, ACTUAL_INVENTORY_SQL, n, POSITION_WINDOW_S):
        out.append({"slug": r["us_market_slug"], "side": r["side"],
                    "at": r["at"],
                    "why": "execution mirror net filled inventory %s, not "
                           "settled" % r["net"],
                    "refs": {"net_qty": str(r["net"])}})
    for r in await _rows(db, ACTUAL_ORDERS_SQL, n):
        out.append({"slug": r["us_market_slug"], "side": _side(r["intent"]),
                    "at": r["at"],
                    "why": "execution mirror live order %s" % r["state"],
                    "refs": {"mirror_id": r["mirror_id"]}})
    return out


async def _intents(db, n):
    rows = await _rows(db, INTENTS_SQL, INTENT_WINDOW_S)
    rows.sort(key=lambda r: (not r["live_eligible"],
                             -(_epoch(r["at"]) or 0.0)))
    out = []
    for r in rows[:n]:
        ident = _j(r.get("identity")) or {}
        out.append({
            "slug": r["us_market_slug"],
            "side": _side(r["holding_side"] or r["order_intent"]),
            "at": r["at"], "strategy": r["strategy"],
            "event": ident.get("fixture") or ident.get("payout_event"),
            "period": ident.get("period"),
            "why": ("execution intent LIVE_ADMISSIBLE (%s)" % r["actual_state"]
                    if r["live_eligible"] else
                    "execution intent imminent: refused at admission %s"
                    % r["actual_refusal"]),
            "refs": {"intent_id": r["intent_id"],
                     "decision_id": r["decision_id"],
                     "policy_version": r["policy_version"]}})
    return out


async def _positions(db, n):
    """Both paper position tiers from one read: the investment strategy's
    (tier 3) and every other strategy's (tier 6, diagnostic only) -- each
    with its OWN limit, so a large investment book can never crowd every
    other held position out of the read."""
    return (await _rows(db, PAPER_POSITIONS_SQL, INVESTMENT_STRATEGY, True,
                        n * 2)
            + await _rows(db, PAPER_POSITIONS_SQL, INVESTMENT_STRATEGY,
                          False, n * 2))


def _paper_member(r, *, diagnostic):
    return {"slug": r["us_market_slug"], "side": _side(r["holding_side"]),
            "at": r["at"], "strategy": r["strategy"],
            "event": r.get("event_key") or r.get("fixture"),
            "diagnostic_only": diagnostic,
            "why": ("open paper %s position %s (%s)"
                    % ("DIAGNOSTIC" if diagnostic else "INVESTMENT",
                       r["open_qty"], r["strategy"])),
            "refs": {"group_id": r["group_id"],
                     "open_qty": str(r["open_qty"])}}


async def _paper(db, n):
    return [_paper_member(r, diagnostic=False) for r in await _positions(db, n)
            if r["strategy"] == INVESTMENT_STRATEGY][:n]


async def _exploration(db, n):
    return [_paper_member(r, diagnostic=True) for r in await _positions(db, n)
            if r["strategy"] != INVESTMENT_STRATEGY][:n]


async def _candidates(db, n):
    rows = await _rows(db, CANDIDATES_SQL, INVESTMENT_STRATEGY,
                       INVESTMENT_VERSION, CANDIDATE_WINDOW_S)
    rows.sort(key=lambda r: (r["verdict"] != "ENTER",
                             -(_epoch(r["at"]) or 0.0)))
    return [{"slug": r["us_market_slug"],
             "side": _side(r["holding_side"] or r["intent"]),
             "at": r["at"], "strategy": INVESTMENT_STRATEGY,
             "event": r.get("event_key") or r.get("fixture"),
             "why": ("V3 candidate qualified (ENTER)" if r["verdict"] == "ENTER"
                     else "V3 candidate evaluated (%s)" % r["refusal"]),
             "refs": {"decision_id": r["decision_id"]}} for r in rows[:n]]


async def _universe(db, n):
    rows = await _rows(db, UNIVERSE_SQL, INVESTMENT_STRATEGY,
                       LEAGUE_LOOKBACK_S, GAME_PAST_S, GAME_AHEAD_S)
    now = time.time()
    rows.sort(key=lambda r: abs((_epoch(r["at"]) or now) - now))
    return [{"slug": r["us_market_slug"],
             "side": _side(r.get("side_norm")),
             "at": r["at"], "event": r.get("event_slug"),
             "market_type_hint": r.get("kind"),
             "why": "retail-mapped %s contract, game %s, a sport the "
                    "investment strategy decides on" % (r.get("sport"),
                                                        _iso(r["at"])),
             "refs": {"retail_event_slug": r.get("event_slug")}}
            for r in rows[:n]]


_READERS = {T_ACTUAL: _actual, T_INTENT: _intents, T_PAPER: _paper,
            T_CANDIDATE: _candidates, T_UNIVERSE: _universe,
            T_EXPLORATION: _exploration}


# ── ordering and the bound (pure) ────────────────────────────────────

def prioritize(candidates: dict, *, discovery=(), limit: int = MAX_MEMBERS,
               now=None, held_reserve: int = 0, held_first=()) -> dict:
    """Pure: {tier: [candidate]} (+ discovery symbols) -> the bounded,
    ordered, de-duplicated universe. A slug is a member ONCE, at its highest
    tier; every reason it qualified for is kept. Within a tier the reader's
    order stands. Members past `limit` are counted per tier.

    `held_reserve` (HELD_PAPER_RESERVE from `compute`): up to that many slots
    are kept for held paper positions (tiers 3 / 6) after tiers 1-2, in the
    order `held_first` names (the held-mark refresh's own priority: due and
    not covered by a stream first), then the reader's order. A `held_first`
    slug no reader returned joins tier 6 (diagnostic). Real-money tiers are
    never displaced by it."""
    bound = max(0, min(int(limit), MAX_MEMBERS))
    cands = {t: list((candidates or {}).get(t) or ()) for t in TIERS}
    cands[T_DISCOVERY] = cands[T_DISCOVERY] + [
        {"slug": s, "why": "experimental lane focus set (discovery)"}
        for s in discovery or ()]
    held_first = [str(s) for s in held_first or () if str(s or "").strip()]
    if held_first:
        known = {str(c.get("slug") or "") for t in TIERS for c in cands[t]}
        cands[T_EXPLORATION] = cands[T_EXPLORATION] + [
            {"slug": s, "diagnostic_only": True,
             "why": "held paper position needing a mark (held-mark refresh)"}
            for s in held_first if s not in known]
    by_slug: dict = {}
    order: list = []
    for tier in TIERS:
        for c in cands[tier]:
            slug = str(c.get("slug") or "").strip()
            if not slug:
                continue
            reason = {"tier": tier, "why": c.get("why")}
            if slug in by_slug:
                by_slug[slug]["reasons"].append(reason)
                continue
            m = {"retail_slug": slug, "tier": tier,
                 "tier_rank": TIER_RANK[tier], "why": c.get("why") or tier,
                 "reasons": [reason], "side": c.get("side"),
                 "bettor_event": c.get("event"),
                 "strategy": c.get("strategy"),
                 "period": c.get("period"),
                 "market_type_hint": c.get("market_type_hint"),
                 "diagnostic_only": bool(c.get("diagnostic_only")
                                         or tier == T_EXPLORATION),
                 "at": _iso(c.get("at")), "refs": c.get("refs") or {},
                 # NOTHING HERE GRANTS LIVE ELIGIBILITY; it is decided by the
                 # execution intent alone (execmirror.live_eligibility +
                 # actual_admission), never by membership.
                 "live_eligibility_effect": "NONE"}
            by_slug[slug] = m
            order.append(slug)
    selected = order[:bound]
    reserve = max(0, min(int(held_reserve or 0), bound))
    if reserve:
        pos = {s: i for i, s in enumerate(order)}
        pref = {s: i for i, s in enumerate(held_first)}
        protected = [s for s in order
                     if by_slug[s]["tier"] in (T_ACTUAL, T_INTENT)][:bound]
        held = sorted((s for s in order
                       if by_slug[s]["tier"] in (T_PAPER, T_EXPLORATION)),
                      key=lambda s: (pref.get(s, len(pref)), pos[s]))
        pinned = held[:max(0, min(reserve, bound - len(protected)))]
        chosen = set(protected) | set(pinned)
        rest = [s for s in order if s not in chosen]
        selected = protected + pinned + rest[:max(
            0, bound - len(protected) - len(pinned))]
        for s in pinned:
            by_slug[s]["held_reserve"] = True
    keep = set(selected)
    members = [by_slug[s] for s in selected]
    dropped: dict = {}
    for s in (s for s in order if s not in keep):
        t = by_slug[s]["tier"]
        dropped[t] = dropped.get(t, 0) + 1
    for i, m in enumerate(members):
        m["rank"] = i + 1
    at = now if now is not None else time.time()
    uid = hashlib.sha256(json.dumps(
        [at, [m["retail_slug"] for m in members]]).encode()).hexdigest()[:20]
    return {"version": VERSION, "universe_id": "fu_" + uid,
            "computed_at": at, "bound": bound,
            "candidates_total": len(order), "members": members,
            "dropped_beyond_bound": dropped,
            "per_tier": per_tier(members)}


def per_tier(members) -> dict:
    out = {t: 0 for t in TIERS}
    for m in members or ():
        out[m["tier"]] = out.get(m["tier"], 0) + 1
    return out


async def compute(db, *, discovery=(), limit: int = MAX_MEMBERS,
                  now=None, held_reserve: int = HELD_PAPER_RESERVE,
                  held_first=()) -> dict:
    """The universe from the database (read-only), identities NOT yet
    attached (`attach`). Held paper positions keep HELD_PAPER_RESERVE slots
    after the real-money tiers (see `prioritize`)."""
    g = await gather(db, limit=limit)
    u = prioritize(g["candidates"], discovery=discovery, limit=limit, now=now,
                   held_reserve=held_reserve, held_first=held_first)
    u["tier_status"] = dict(g["status"], **{T_DISCOVERY: {
        "status": "MEASURED", "why": None}})
    return u


# ── exact identity (pure) ────────────────────────────────────────────

def _settlement(rec) -> dict | None:
    if not isinstance(rec, dict):
        return None
    p = CF.proposition(rec)
    rule = p.get("settlementRule") or ""
    return {"cftc_instrument_id": p.get("cftcInstrumentId"),
            "institutional_event_id": p.get("eventSlug"),
            "outcome_type": p.get("outcomeType"),
            "outcome_strike": p.get("outcomeStrike"),
            "payout_value": p.get("payoutValue"),
            "binary_yes": CF.settlement_is_binary_yes(p),
            "rule_sha256": (hashlib.sha256(rule.encode()).hexdigest()
                            if rule else None),
            "rule": rule[:240] or None}


def identify(member: dict, record, *, retail_row=None,
             attempted: bool = True) -> dict:
    """Pure: the member's EXACT identity, or UNAVAILABLE with the reason.

    `record` is the venue's refdata record for the slug (None: not read yet,
    or -- `attempted` -- read and not listed). The instrument's ONE book is
    the long (YES) instrument's, so the mapping asked is (slug, YES), exactly
    as the probe and the stream key it; the member's own side is recorded
    beside it. Never fuzzy: only `map_retail_to_institutional`."""
    slug = str((member or {}).get("retail_slug") or "")
    rec = record if isinstance(record, dict) else None
    meta = (rec or {}).get("metadata") or {}
    base = {"retail_slug": slug, "book_leg": "yes",
            "member_side": (member or {}).get("side"),
            "bettor_event": (member or {}).get("bettor_event"),
            "retail_event_slug": (retail_row or {}).get("event_slug")
            or ((member or {}).get("refs") or {}).get("retail_event_slug"),
            "period": (member or {}).get("period"),
            "period_source": ("EXECUTION_INTENT_ADMISSION_FACTS"
                              if (member or {}).get("period") is not None
                              else "NOT_RECORDED"),
            "market_type": (meta.get("outcome_type")
                            or meta.get("instrument_product")
                            or (retail_row or {}).get("kind")
                            or (member or {}).get("market_type_hint")),
            "institutional_event_id": meta.get("event_id"),
            "settlement": _settlement(rec)}
    if not _SLUG.match(slug):
        return dict(base, status=UNAVAILABLE, reason=U_SLUG,
                    why="the retail slug %r is not a venue slug" % slug,
                    institutional_symbol=None)
    if rec is None:
        why = (U_NOT_LISTED if attempted else U_NOT_READ)
        return dict(base, status=UNAVAILABLE, reason=why,
                    why=("the institutional venue returned no instrument for "
                         "this slug" if attempted else
                         "the instrument's refdata has not been read yet"),
                    institutional_symbol=None)
    m = ICM.map_retail_to_institutional(slug, "yes", rec,
                                        retail_row=retail_row)
    if not m.get("ok") or m.get("institutional_symbol") != slug:
        return dict(base, status=UNAVAILABLE,
                    reason=m.get("refusal") or ICM.M_NOT_EXACT,
                    why=m.get("why"), institutional_symbol=None,
                    mapper_version=m.get("version"))
    side_map = ICM.map_retail_to_institutional(
        slug, "no" if base["member_side"] == "SHORT" else "yes", rec,
        retail_row=retail_row)
    return dict(base, status=EXACT, reason=None, why=None,
                institutional_symbol=m["institutional_symbol"],
                institutional_side=m.get("institutional_side"),
                price_transform=m.get("price_transform"),
                price_scale=m.get("price_scale"),
                qty_scale=m.get("qty_scale"),
                payout_value=m.get("payout_value"),
                mapper_version=m.get("version"),
                basis=list(m.get("basis") or []),
                member_side_maps_exactly=bool(side_map.get("ok")),
                member_side_refusal=side_map.get("refusal"))


def attach(universe: dict, *, record_for, retail=None, attempted=None
           ) -> dict:
    """Attach `identify` to every member. `record_for(slug)` -> record or
    None; `retail` {(slug, leg): us_premap row}; `attempted(slug)` -> was the
    refdata read made. Returns the universe (members updated in place)."""
    retail = retail or {}
    for m in (universe or {}).get("members") or ():
        s = m["retail_slug"]
        ident = identify(m, record_for(s),
                         retail_row=retail.get((s, "yes")),
                         attempted=True if attempted is None
                         else bool(attempted(s)))
        m["identity"] = ident
        m["identity_status"] = ident["status"]
        m["unavailable_reason"] = ident["reason"]
        m["institutional_symbol"] = ident.get("institutional_symbol")
    return universe


def exact_symbols(universe: dict) -> list:
    return [m["retail_slug"] for m in (universe or {}).get("members") or ()
            if m.get("identity_status") == EXACT]


def by_slug(universe: dict) -> dict:
    return {m["retail_slug"]: m for m in (universe or {}).get("members") or ()}


def summary(universe: dict) -> dict:
    """Compact, for heartbeats and describe()."""
    ms = (universe or {}).get("members") or []
    return {"version": VERSION, "universe_id": (universe or {}).get(
        "universe_id"), "count": len(ms), "bound": (universe or {}).get(
        "bound"), "per_tier": per_tier(ms),
        "exact": sum(1 for m in ms if m.get("identity_status") == EXACT),
        "unavailable": sum(1 for m in ms
                           if m.get("identity_status") == UNAVAILABLE),
        "dropped_beyond_bound": (universe or {}).get("dropped_beyond_bound")}


# ── persistence (migration 213) ──────────────────────────────────────

INSERT_SQL = """
    INSERT INTO institutional_focus_universe (
        computed_at, process_id, service, version, universe_id, bound, rank,
        tier, tier_rank, why, reasons, retail_slug, outcome_side,
        bettor_event, strategy, diagnostic_only, identity_status,
        unavailable_reason, institutional_symbol, market_type, period,
        retail_event_slug, institutional_event_id, settlement, identity,
        stream_wanted, refs)
    VALUES (to_timestamp($1), $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11::jsonb, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
            $22, $23, $24::jsonb, $25::jsonb, $26, $27::jsonb)
    ON CONFLICT (universe_id, retail_slug) DO NOTHING
"""


async def persist(pool, universe: dict, *, process_id: str, service: str,
                  wanted=None) -> int:
    """One row per member of this snapshot. Never raises; returns rows
    written. A member without an attached identity is not written."""
    n = 0
    wanted = set(wanted) if wanted is not None else None
    for m in (universe or {}).get("members") or ():
        ident = m.get("identity")
        if not isinstance(ident, dict):
            continue
        try:
            await pool.execute(
                INSERT_SQL, float(universe["computed_at"]), process_id,
                service, universe.get("version", VERSION),
                universe["universe_id"], int(universe["bound"]),
                int(m["rank"]), m["tier"], int(m["tier_rank"]),
                str(m.get("why") or m["tier"])[:500],
                json.dumps(m.get("reasons") or [], default=str),
                m["retail_slug"], m.get("side"), m.get("bettor_event"),
                m.get("strategy"), bool(m.get("diagnostic_only")),
                ident["status"], ident.get("reason"),
                ident.get("institutional_symbol"), ident.get("market_type"),
                None if ident.get("period") is None else str(ident["period"]),
                ident.get("retail_event_slug"),
                ident.get("institutional_event_id"),
                json.dumps(ident.get("settlement"), default=str),
                json.dumps(ident, default=str),
                None if wanted is None else m["retail_slug"] in wanted,
                json.dumps(m.get("refs") or {}, default=str))
            n += 1
        except Exception:                                     # noqa: BLE001
            continue
    return n
