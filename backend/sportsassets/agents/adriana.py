"""ADRIANA'S DESK: THE SHADOW ARBITRAGE CENSUS OVER RECORDED BOOKS (265).

Adriana (Head of Arbitrage, SHADOW_ONLY) runs one census pass at a time:

  1. READ the newest recorded book of every market the system observed in
     the last BOOK_WINDOW_S (paper_book_observations: the read-only market
     data the paper path recorded, with our receipt instant) and that
     market's catalogue identity (us_premap). Nothing is fetched from a
     venue here: no network, no credential.
  2. BUILD contracts with explicit payoff maps over a proved outcome space:
       TOTALS   tsc-<lg>-<a>-<b>-<date>[-fh|-sh]-<L>: YES = the total is
                more than L, NO = it is not (the venue's own question);
       SPREADS  asc-<lg>-<a>-<b>-<date>[-seg]-(neg|pos)-<L>: YES = team a
                covers (the margin a-b is above +L for neg, -L for pos).
     The integer underlying is cut by every listed half-point line of the
     same event, family and period (adriana_arb.line_buckets): exhaustive by
     construction. Any other family, or a slug the grammar does not read, is
     counted by name and skipped -- never guessed.
  3. EVALUATE every pair of contracts of one event / family / period across
     markets with the pure engine (adriana_arb.pair_scanner): settlement and
     payoff equivalence, synchronized freshness, executable depth on every
     leg, every fee, slippage and cost, the largest profitable matched size.
  4. FAIL CLOSED ON SETTLEMENT TERMS NOT READ. What a venue pays when an
     event is cancelled or postponed decides whether a structure can lose.
     No Polymarket US void / postponement terms are recorded where this
     census can read them, so the engine is run with a declared HYPOTHESIS
     (both legs settle 50-50) only to measure the economics, and every
     structure is then REFUSED with VOID_TERMS_NOT_ESTABLISHED -- a
     structure that would be proven under that hypothesis is counted as a
     CONDITIONAL candidate, never as an opportunity. An opportunity row is
     written only for a structure whose every outcome reconciles on terms
     actually established (`ESTABLISHED_VOID_TERMS`, empty today).
  5. KALSHI / POLYMARKET (international): the engine prices them, but no
     book of either venue is recorded in Postgres, so the census states
     each venue UNAVAILABLE with the reason -- never an empty success.
  6. RECORD the pass (adriana_arb_scans), every opportunity
     (adriana_arb_opportunities) and the refusals (adriana_arb_refusals,
     the first MAX_RECORDED_REFUSALS ranked by how close they came; the
     scan row carries the full counts and how many were recorded, so no cap
     is silent), all in one transaction declared as ADRIANA: the database
     refuses any order, intent, fill, approval or control write in it.
  7. COLLABORATE through records only: a HANDOFF to Archer (execution
     review) and a REVIEW_REQUEST to Karen (challenge) per new opportunity;
     a daily REVIEW_REQUEST to Audrey citing the latest census; and one
     open task per structural blocker (terms not established, a venue with
     no recorded books) on her own queue.

NO AUTHORITY: this module imports no venue, order, credential or capital
module, opens no connection and writes only Adriana's own tables plus her
messages and tasks. `assert_no_authority` holds the engine to the same.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import adriana_arb as A
from . import pos_authority as PA
from . import registry as R

VERSION = "ADRIANA_CENSUS_V1"
AGENT = R.ADRIANA
BOOK_WINDOW_S = 900
MAX_MARKETS = 800
MAX_RECORDED_REFUSALS = 150
MAX_AGE_S = A.DEFAULT_MAX_AGE_S
MAX_SKEW_S = A.DEFAULT_MAX_SKEW_S

R_NO_SCHEMA = "MIGRATION_265_NOT_APPLIED"
VOID_TERMS_NOT_ESTABLISHED = "VOID_TERMS_NOT_ESTABLISHED"
#: (venue, family) whose cancellation / postponement payout is established
#: from recorded venue terms. Empty: none is, so nothing is proven.
ESTABLISHED_VOID_TERMS: dict = {}
#: the hypothesis the economics are MEASURED under (never a verdict)
HYPOTHESIS = {"void_payout": Decimal("0.5"), "postponed_payout": Decimal("0.5"),
              "label": "HYPOTHESIS_BOTH_LEGS_SETTLE_50_50_ON_VOID_OR_POSTPONEMENT"}

FAMILY_OF_PREFIX = {"tsc": A.TOTAL, "asc": A.SPREAD}
SKIP_FAMILY = {"aec": "MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED",
               "atc": "MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED",
               "astatc": "PROP_FAMILY_NOT_MODELLED"}
_SLUG = re.compile(
    r"^(?P<pfx>tsc|asc)-(?P<lg>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<seg>fh|sh|1h|2h|q[1-4]|p[1-3]))?-"
    r"(?:(?P<sign>neg|pos)-)?(?P<line>\d+(?:pt\d+)?)$")

AUTHORITY = dict(A.AUTHORITY)
VENUES_NOT_RECORDED = {
    A.KALSHI: ("NO_KALSHI_BOOK_SOURCE: no Kalshi order book is recorded in "
               "Postgres (the admin board is an in-memory cache and the "
               "suspended edge-shadow service kept its own SQLite); the "
               "engine prices Kalshi legs, the census cannot read any"),
    A.POLYMARKET: ("NO_POLYMARKET_INTERNATIONAL_BOOK_SOURCE: no book is "
                   "recorded and no fee schedule is known"),
}


def _d(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        if isinstance(x, dict):
            x = x.get("value")
        return Decimal(str(x))
    except Exception:                                           # noqa: BLE001
        return None


def _j(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:                                           # noqa: BLE001
        return None


def parse_slug(slug: str) -> dict | None:
    """The family grammar of a Polymarket US market slug, or None."""
    m = _SLUG.match(str(slug or ""))
    if not m:
        return None
    g = m.groupdict()
    raw = g["line"].replace("pt", ".")
    line = Decimal(raw)
    fam = FAMILY_OF_PREFIX[g["pfx"]]
    if fam == A.SPREAD and not g["sign"]:
        return None
    if fam == A.TOTAL and g["sign"]:
        return None
    # the threshold the YES side must clear on the integer underlying
    # (total points; or margin a - b, which is above +L for 'neg-L' and
    # above -L for 'pos-L')
    thr = line if fam == A.TOTAL else (line if g["sign"] == "neg" else -line)
    period = {"fh": "FIRST_HALF", "1h": "FIRST_HALF", "sh": "SECOND_HALF",
              "2h": "SECOND_HALF"}.get(g["seg"] or "", (g["seg"] or "FULL").upper())
    event = "%s-%s-%s-%s" % (g["lg"], g["a"], g["b"], g["date"])
    return {"family": fam, "league": g["lg"], "a": g["a"], "b": g["b"],
            "date": g["date"], "period": period, "line": line,
            "threshold": thr, "event": event,
            "subject": "TOTAL_POINTS" if fam == A.TOTAL else
            "MARGIN_%s_MINUS_%s" % (g["a"].upper(), g["b"].upper())}


def _levels(raw) -> list:
    out = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        px, q = _d(e.get("px")), _d(e.get("qty"))
        if px is None or q is None or not (Decimal(0) < px < Decimal(1)) \
                or q < 1:
            continue
        out.append((px, int(q)))
    return out


def _aware(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def build_universe(rows, *, void_terms=None) -> dict:
    """Contracts, books and outcome spaces from recorded rows. Pure.

    `rows`: dicts with us_market_slug, observed_at, offers, bids, error,
    sport (catalogue sports_type, may be None). Every row that cannot become
    a contract is counted under its reason in `skipped`."""
    vt = dict(ESTABLISHED_VOID_TERMS if void_terms is None else void_terms)
    skipped: dict[str, int] = {}
    parsed = []
    for r in rows:
        slug = str(r.get("us_market_slug") or "")
        pfx = slug.split("-", 1)[0]
        if pfx in SKIP_FAMILY:
            skipped[SKIP_FAMILY[pfx]] = skipped.get(SKIP_FAMILY[pfx], 0) + 1
            continue
        p = parse_slug(slug)
        if p is None:
            skipped["SLUG_GRAMMAR_NOT_READ"] = skipped.get(
                "SLUG_GRAMMAR_NOT_READ", 0) + 1
            continue
        if r.get("error"):
            skipped["BOOK_READ_FAILED"] = skipped.get("BOOK_READ_FAILED", 0) + 1
            continue
        if p["threshold"] % 1 == 0:
            skipped["WHOLE_NUMBER_LINE_PUSH_TERMS_NOT_READ"] = skipped.get(
                "WHOLE_NUMBER_LINE_PUSH_TERMS_NOT_READ", 0) + 1
            continue
        parsed.append((r, p))
    groups: dict[str, list] = {}
    for r, p in parsed:
        key = "%s|%s|%s" % (p["event"], p["family"], p["period"])
        groups.setdefault(key, []).append((r, p))
    contracts, books, spaces = [], [], {}
    for key, items in groups.items():
        space = A.line_buckets(key, sorted({p["threshold"] for _, p in items}))
        spaces[key] = space
        for r, p in items:
            slug = r["us_market_slug"]
            terms = vt.get((A.POLYMARKET_US, p["family"]))
            vp = (terms or HYPOTHESIS)["void_payout"]
            pp = (terms or HYPOTHESIS)["postponed_payout"]
            # one venue, one event: every leg shares the venue's grading of
            # that event, so the window is the event's date (identical for
            # every leg by construction, compared for equality only)
            day = datetime.strptime(p["date"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
            spec = A.SettlementSpec(
                event_key=key, family=p["family"], period=p["period"],
                subject=p["subject"], line=p["threshold"],
                resolution_source="POLYMARKET_US:%s" % p["league"].upper(),
                settle_window=(day.isoformat(),
                               (day + timedelta(days=2)).isoformat()),
                void_rule=terms.get("rule", "ESTABLISHED") if terms
                else HYPOTHESIS["label"],
                # a half-point line on an integer underlying cannot push
                tie_rule=A.TIE_IMPOSSIBLE)
            sport = (r.get("sport") or p["league"]).upper()
            yes = A.Contract(A.POLYMARKET_US, slug, A.YES, spec,
                             A.over_under_payoff(space, "OVER", p["threshold"],
                                                 void_payout=vp,
                                                 postponed_payout=pp),
                             sport=sport)
            no = A.Contract(A.POLYMARKET_US, slug, A.NO, spec,
                            A.over_under_payoff(space, "UNDER", p["threshold"],
                                                void_payout=vp,
                                                postponed_payout=pp),
                            sport=sport)
            contracts += [yes, no]
            at = _aware(r.get("observed_at"))
            offers = _levels(_j(r.get("offers")))
            # On Polymarket US the short side of a market IS listed: buying
            # NO is the venue's own BUY_SHORT order, filled against the
            # long bids at (1 - bid). One instrument, declared as such.
            no_asks = A.synthesize_no_asks(
                no, _levels(_j(r.get("bids"))),
                declared_single_instrument={(A.POLYMARKET_US, slug)})
            books += [A.Book(A.POLYMARKET_US, slug, A.YES, tuple(offers), at),
                      A.Book(A.POLYMARKET_US, slug, A.NO, tuple(no_asks), at)]
    return {"contracts": contracts, "books": books, "spaces": spaces,
            "skipped": skipped, "markets": len(parsed),
            "void_terms_established": bool(vt)}


def _closeness(rec) -> float:
    eco = rec.get("economics") or {}
    try:
        return float(eco.get("worst_case_net_profit"))
    except (TypeError, ValueError):
        return float("-inf")


def census(rows, now: datetime, *, void_terms=None, **kw) -> dict:
    """ONE CENSUS PASS over recorded rows. Pure. Every considered structure
    is in exactly one of `opportunities` / `refusals`."""
    u = build_universe(rows, void_terms=void_terms)
    opps, refs, cen = A.pair_scanner(
        u["contracts"], u["books"], now, outcome_spaces=u["spaces"],
        max_age_s=kw.get("max_age_s", MAX_AGE_S),
        max_skew_s=kw.get("max_skew_s", MAX_SKEW_S))
    vt = dict(ESTABLISHED_VOID_TERMS if void_terms is None else void_terms)

    def established(rec) -> bool:
        return bool(vt) and all((A.POLYMARKET_US, f) in vt
                                for f in _families(rec))
    proven, refused, conditional = [], list(refs), 0
    for rec in opps:
        if established(rec):
            proven.append(rec)
            continue
        # proven ONLY under the declared hypothesis: a conditional
        # candidate, refused until the venue's terms are established
        conditional += 1
        rec = dict(rec, verdict=A.REFUSED, conditional_on=HYPOTHESIS["label"],
                   reasons=[{"code": VOID_TERMS_NOT_ESTABLISHED,
                             "detail": "proven only if both legs settle 50-50 "
                                       "on a cancellation or postponement; "
                                       "the venue's terms are not read"}]
                   + list(rec.get("reasons") or []))
        refused.append(rec)
    for rec in refused:
        codes = A.reason_codes(rec)
        if not established(rec) and VOID_TERMS_NOT_ESTABLISHED not in codes:
            rec["reasons"] = list(rec.get("reasons") or []) + [
                {"code": VOID_TERMS_NOT_ESTABLISHED,
                 "detail": "venue cancellation / postponement terms not "
                           "read; also refused for the reasons above"}]
    refused.sort(key=_closeness, reverse=True)
    summary = A.census_of(proven + refused)
    summary.update(
        markets_read=u["markets"], skipped=u["skipped"],
        conditional_candidates=conditional,
        contracts=len(u["contracts"]), outcome_spaces=len(u["spaces"]),
        same_market_pairs_not_considered=cen.get(
            "same_market_pairs_not_considered", 0),
        hypothesis=HYPOTHESIS["label"],
        void_terms_established=u["void_terms_established"])
    fresh = sum(1 for b in u["books"] if b.side == A.YES and b.observed_at
                and (now - b.observed_at).total_seconds() <= MAX_AGE_S)
    return {"opportunities": proven, "refusals": refused, "census": summary,
            "books_fresh": fresh}


def _families(rec) -> list:
    out = []
    for leg in ((rec.get("inputs") or {}).get("legs") or []):
        for alt in leg if isinstance(leg, list) else [leg]:
            p = parse_slug(alt.get("market_id")) if isinstance(alt, dict) \
                else None
            if p:
                out.append(p["family"])
    return out or [None]


# ═════════════════════════════════════════════════════════════════════
# I/O: read, record, collaborate (each write declared as ADRIANA)
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    return all([await conn.fetchval("SELECT to_regclass($1)", t)
                for t in ("adriana_arb_scans", "adriana_arb_opportunities",
                          "adriana_arb_refusals")])


async def read_rows(conn, *, now: float, window_s: float = BOOK_WINDOW_S,
                    limit: int = MAX_MARKETS) -> list:
    """The newest recorded book of each market observed in the window, with
    its catalogue sport. Read only."""
    rows = await conn.fetch(
        "WITH latest AS (SELECT DISTINCT ON (us_market_slug) obs_id, "
        "       us_market_slug, observed_at, offers, bids, error "
        "  FROM paper_book_observations "
        " WHERE observed_at >= to_timestamp($1) "
        " ORDER BY us_market_slug, observed_at DESC) "
        "SELECT l.*, (SELECT p.sports_type FROM us_premap p "
        "              WHERE p.market_slug = l.us_market_slug "
        "                AND p.sports_type IS NOT NULL LIMIT 1) AS sport "
        "  FROM latest l ORDER BY l.observed_at DESC LIMIT $2",
        now - window_s, limit)
    return [dict(r) for r in rows]


def _id(prefix: str, *parts) -> str:
    return "%s-%s" % (prefix, hashlib.sha256("|".join(
        str(p) for p in parts).encode()).hexdigest()[:20])


def _legs(rec) -> list:
    return [[{k: alt.get(k) for k in ("venue", "market_id", "side")}
             for alt in (leg if isinstance(leg, list) else [leg])]
            for leg in ((rec.get("inputs") or {}).get("legs") or [])]


def _venues(rec) -> list:
    return sorted({alt["venue"] for leg in _legs(rec) for alt in leg
                   if alt.get("venue")})


def _event_key(rec) -> str | None:
    return (rec.get("inputs") or {}).get("event_key")


async def record(conn, result: dict, *, started: float, finished: float,
                 scan_id: str, status: str, why: str | None) -> dict:
    """One transaction, declared as ADRIANA: the scan, its opportunities and
    the ranked refusals. Idempotent on the scan id."""
    PA.assert_may(AGENT, "write.arb_records")
    c = result["census"]
    refs = result["refusals"][:MAX_RECORDED_REFUSALS]
    async with conn.transaction():
        await PA.act_as(conn, AGENT)
        made = await conn.execute(
            "INSERT INTO adriana_arb_scans (scan_id, started_at, finished_at, "
            " status, why, engine_version, venues, markets_read, books_fresh, "
            " structures_considered, opportunities, refusals_total, "
            " refusals_recorded, by_verdict, by_kind, by_code, limits, "
            " authority) VALUES ($1, to_timestamp($2), to_timestamp($3), $4, "
            " $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14::jsonb, "
            " $15::jsonb, $16::jsonb, $17::jsonb, $18::jsonb) "
            "ON CONFLICT (scan_id) DO NOTHING",
            scan_id, started, finished, status, why,
            "%s+%s" % (VERSION, A.VERSION),
            json.dumps(venue_support(result), default=str),
            int(c.get("markets_read") or 0), int(result.get("books_fresh") or 0),
            len(result["opportunities"]) + len(result["refusals"]),
            len(result["opportunities"]), len(result["refusals"]), len(refs),
            json.dumps(c.get("by_verdict") or {}),
            json.dumps(c.get("by_structure_kind") or {}),
            json.dumps({"by_code": c.get("by_refusal_code") or {},
                        "by_primary_code": c.get("by_primary_refusal_code")
                        or {},
                        "skipped": c.get("skipped") or {},
                        "conditional_candidates": c.get(
                            "conditional_candidates", 0),
                        "same_market_pairs_not_considered": c.get(
                            "same_market_pairs_not_considered", 0)}),
            json.dumps({"book_window_s": BOOK_WINDOW_S,
                        "max_markets": MAX_MARKETS, "max_age_s": MAX_AGE_S,
                        "max_skew_s": MAX_SKEW_S,
                        "max_recorded_refusals": MAX_RECORDED_REFUSALS,
                        "hypothesis": HYPOTHESIS["label"]}),
            json.dumps(AUTHORITY))
        if not made.endswith("1"):
            return {"ok": True, "created": False, "scan_id": scan_id}
        opp_ids = []
        for rec in result["opportunities"]:
            eco = rec["economics"]
            oid = _id("adr-opp", scan_id, json.dumps(_legs(rec)))
            plan = A.plan_execution(rec) if len(_legs(rec)) == 2 else None
            await conn.execute(
                "INSERT INTO adriana_arb_opportunities (opportunity_id, "
                " scan_id, structure_kind, event_key, venues, legs, verdict, "
                " max_qty, min_payout_usd, total_cost_usd, net_profit_usd, "
                " edge_per_set_usd, books, economics, leg_plan, "
                " evidence_refs, decided_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,"
                " $7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,$15::jsonb,"
                " $16::jsonb, to_timestamp($17)) ON CONFLICT DO NOTHING",
                oid, scan_id, rec["structure_kind"], _event_key(rec),
                _venues(rec), json.dumps(_legs(rec)), rec["verdict"],
                int(eco["qty"]), Decimal(str(eco["floor_payout_per_set"])),
                Decimal(str(eco["total_cost"])),
                Decimal(str(eco["worst_case_net_profit"])),
                # the engine names the per-set edge `edge_per_contract`
                # (one contract per leg per matched set): the same number
                Decimal(str(eco.get("edge_per_set",
                                    eco.get("edge_per_contract")))),
                json.dumps((rec.get("inputs") or {}).get("books") or []),
                json.dumps(dict(eco, claim_pair=rec.get("claim_pair"))
                           if rec.get("claim_pair") else eco, default=str),
                json.dumps(None if plan is None else {
                    "state": plan.state, "target_qty": plan.target_qty,
                    "leg_a": {"venue": plan.leg_a.venue,
                              "market_id": plan.leg_a.market_id,
                              "side": plan.leg_a.side},
                    "leg_b": {"venue": plan.leg_b.venue,
                              "market_id": plan.leg_b.market_id,
                              "side": plan.leg_b.side},
                    "rule": "thinner, then less reliable leg first",
                    "mode": "SHADOW"}, default=str),
                json.dumps([{"kind": "adriana_arb_scans", "id": scan_id}]),
                finished)
            opp_ids.append(oid)
        for i, rec in enumerate(refs):
            codes = [x for x in A.reason_codes(rec) if x] or ["UNCLASSIFIED"]
            await conn.execute(
                "INSERT INTO adriana_arb_refusals (refusal_id, scan_id, "
                " structure_kind, event_key, venues, legs, codes, "
                " primary_code, detail, decided_at) VALUES ($1,$2,$3,$4,$5,"
                " $6::jsonb,$7,$8,$9::jsonb, to_timestamp($10)) "
                "ON CONFLICT DO NOTHING",
                _id("adr-ref", scan_id, i, json.dumps(_legs(rec))), scan_id,
                rec.get("structure_kind") or "PAIR", _event_key(rec),
                _venues(rec) or [A.POLYMARKET_US], json.dumps(_legs(rec)),
                codes, codes[0], json.dumps({
                    "reasons": rec.get("reasons"),
                    "economics": rec.get("economics"),
                    "conditional_on": rec.get("conditional_on"),
                    "books": (rec.get("inputs") or {}).get("books"),
                    "skew_s": (rec.get("inputs") or {}).get("skew_s"),
                    "payoff_table": rec.get("payoff_table"),
                    # (claim-first scans) the canonical claims, the basis
                    # and the topology read off the routes actually used
                    "claim_pair": rec.get("claim_pair")}, default=str),
                finished)
    return {"ok": True, "created": True, "scan_id": scan_id,
            "opportunity_ids": opp_ids, "refusals_recorded": len(refs)}


def venue_support(result: dict) -> dict:
    """Per venue: SUPPORTED (books recorded and read) or UNAVAILABLE with
    the reason. Never an empty success."""
    out = {A.POLYMARKET_US: {
        "status": "SUPPORTED" if result["census"].get("markets_read")
        else "UNAVAILABLE",
        "why": None if result["census"].get("markets_read") else
        "NO_RECORDED_BOOK_IN_THE_WINDOW",
        "source": "paper_book_observations (recorded, read-only)",
        "fee_schedule": "bettor_fee_schedule (dated, per order)"}}
    for v, why in VENUES_NOT_RECORDED.items():
        out[v] = {"status": "UNAVAILABLE", "why": why, "source": None}
    return out


async def collaborate(conn, *, scan_id: str, opportunity_ids: list,
                      result: dict, now: float) -> dict:
    """Records only: hand-offs per new opportunity, one daily review
    request to Audrey, one open task per structural blocker."""
    from . import agent_memory as M
    out = {"handoffs": 0, "review_requests": 0, "tasks": 0}
    for oid in opportunity_ids:
        ref = [{"kind": "adriana_arb_opportunities", "id": oid}]
        h = await M.record_message(
            conn, from_agent=AGENT, to_agent=R.ARCHER, message_kind="HANDOFF",
            subject_type="adriana_arb_opportunities", subject_id=oid,
            summary="Proven after costs in SHADOW: review the executability "
                    "of both legs (depth, leg order, partial-fill recovery).",
            evidence_refs=ref, created_at=now)
        k = await M.record_message(
            conn, from_agent=AGENT, to_agent=R.KAREN,
            message_kind="REVIEW_REQUEST",
            subject_type="adriana_arb_opportunities", subject_id=oid,
            summary="Challenge this structure: settlement, payoff in every "
                    "outcome, freshness, depth and costs.",
            evidence_refs=ref, created_at=now)
        out["handoffs"] += int(bool(h.get("created")))
        out["review_requests"] += int(bool(k.get("created")))
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    c = result["census"]
    a = await M.record_message(
        conn, from_agent=AGENT, to_agent=R.AUDREY,
        message_kind="REVIEW_REQUEST", subject_type="adriana_census_day",
        subject_id=day,
        summary=("Daily arbitrage census for audit: %d structures, %d proven, "
                 "%d refused, %d conditional on unread settlement terms. "
                 "Kalshi books are not recorded." % (
                     len(result["opportunities"]) + len(result["refusals"]),
                     len(result["opportunities"]), len(result["refusals"]),
                     c.get("conditional_candidates", 0))),
        evidence_refs=[{"kind": "adriana_arb_scans", "id": scan_id}],
        created_at=now)
    out["review_requests"] += int(bool(a.get("created")))
    blockers = []
    if not (ESTABLISHED_VOID_TERMS):
        blockers.append(("adriana-task-void-terms",
                         "Establish Polymarket US cancellation / postponement "
                         "payouts for totals and spreads from recorded venue "
                         "terms",
                         {"blocker": VOID_TERMS_NOT_ESTABLISHED,
                          "why": "every structure stays REFUSED until the "
                                 "venue's void terms are read and recorded"}))
    blockers.append(("adriana-task-kalshi-books",
                     "Record Kalshi order books (read-only) so cross-venue "
                     "pairs can be evaluated",
                     {"blocker": "NO_KALSHI_BOOK_SOURCE",
                      "why": VENUES_NOT_RECORDED[A.KALSHI]}))
    for tid, title, spec in blockers:
        t = await R.create_task(
            conn, assignee=AGENT, created_by=AGENT,
            kind="ADRIANA_BLOCKER_V1", title=title, spec=spec,
            evidence=[{"kind": "adriana_arb_scans", "id": scan_id}],
            task_id=tid, now=now)
        if t.get("created"):
            # her own blocker: it waits on a dependency outside her desk
            # (venue terms, a book source), it is not work handed to her
            await R.task_event(conn, tid, kind="WAITING_ON_DEPENDENCY",
                               actor=AGENT, detail=spec, status="WAITING",
                               now=now)
        out["tasks"] += int(bool(t.get("created")))
    return out


async def workroom_posts(conn, *, limit: int = 3) -> list:
    """#agent-workroom lines from her records (latest census pass and any
    new opportunity), once each: [(key, text)]."""
    out = []
    r = await conn.fetchrow(
        "SELECT scan_id, finished_at, structures_considered, opportunities, "
        "       refusals_total, by_code FROM adriana_arb_scans "
        " WHERE finished_at > now() - interval '1 hour' "
        " ORDER BY finished_at DESC LIMIT 1")
    if r is not None:
        bc = _j(r["by_code"]) or {}
        top = sorted((bc.get("by_primary_code") or {}).items(),
                     key=lambda kv: -kv[1])[:3]
        out.append(("census:%s" % r["scan_id"], (
            "Adriana · arbitrage census %s · %d structures · %d proven after "
            "costs · %d refused (%s)\nStage: SHADOW census; nothing is "
            "ordered, sized or approved by this message." % (
                r["scan_id"], r["structures_considered"], r["opportunities"],
                r["refusals_total"],
                ", ".join("%s %d" % kv for kv in top) or "no refusal"))))
    for o in await conn.fetch(
            "SELECT opportunity_id, structure_kind, event_key, max_qty, "
            "       net_profit_usd FROM adriana_arb_opportunities "
            " WHERE decided_at > now() - interval '1 hour' "
            " ORDER BY decided_at DESC LIMIT $1", limit):
        out.append(("opportunity:%s" % o["opportunity_id"], (
            "Adriana · %s on %s · %d sets · $%.2f worst-case net after costs "
            "(SHADOW)\nNext: Archer reviews executability, Karen challenges. "
            "Nothing is ordered." % (o["structure_kind"], o["event_key"],
                                    o["max_qty"],
                                    float(o["net_profit_usd"])))))
    return out[:limit]


def assert_no_authority() -> bool:
    return A.assert_no_authority() and not any(AUTHORITY.get(k) for k in (
        "submit", "cancel", "credentials", "capital"))
