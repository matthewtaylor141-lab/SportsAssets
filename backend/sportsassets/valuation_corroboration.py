"""INDEPENDENT MULTI-BOOK CORROBORATION OF A PINNAPI (WEBSOCKET) VALUATION.

THE PROBLEM. A PinnAPI raw-websocket read is Pinnacle and nothing else, so the
valuation it produces honestly carries `outcome_books = 1`, and
`bettor_external_shadow` refuses it OUTCOME_DEPTH_BELOW_FLOOR
(`MIN_OUTCOME_BOOKS = 2`). The floor is NOT lowered and the thin-outcome check
is NOT weakened by anything here (owner decision).

WHAT THIS DOES INSTEAD. PinnAPI stays the primary probability and reference.
A recent INDEPENDENT multi-book observation -- an odds-API (the-odds-api.com/v4)
read of the SAME event -- may satisfy the >=2-book requirement, and only when
it matches the WS valuation exactly on: event, outcome (the complete outcome
set, which is also the 2-way/3-way settlement interpretation), period
(full game), line (none), market family (h2h / full-game moneyline) and the
settlement rule. The corroborating evidence is persisted SEPARATELY and
explicitly (migration 195, `valuation_corroboration`, plus a compact block in
the valuation's reference evidence). The PinnAPI read's own `outcome_books`
stays 1 on the record; the corroborating count sits beside it under its own
name, and never replaces it.

THE COUNT IS NOT REINVENTED. The corroborating bookmaker count is computed by
the caller-supplied `count_fn`, which the collector binds to
`ext_pinnacle_loop.pinnacle_h2h` -- the SAME function that computes
`outcome_books` for odds-API reads -- applied to the observation reduced to
its exact-identity, currently-stamped, one-entry-per-bookmaker h2h markets.

THE AGE BOUND (`max_age_s`) IS NOT INVENTED EITHER. The collector passes
`ext_pinnacle_loop.PINNACLE_MAX_AGE_S` (30 s). In the odds-API path the book
count arrives in the SAME read as the probability, so the evidence behind
`outcome_books` is as old as that read, which the 30 s rule bounds; and the
PinnAPI path's own independent-book check (`pinnapi_primary.book_depth`,
called with `max_age_s=PINNACLE_MAX_AGE_S`) already applied that very bound
per book. Corroboration evidence may therefore be no older than the evidence
it stands in for. Each counted book's own provider stamp (its h2h market's
`last_update`, else the bookmaker's -- the stamp `book_depth` reads) must lie
in [decision_at - max_age_s, decision_at]; a future stamp is a clock
disagreement, never freshness.

Pure apart from the module-level read budget. No socket, database, venue
client or order here; the collector makes the bounded read and persists.
"""
from __future__ import annotations

from collections import deque
import math
import time

from . import pinnapi_primary as P

VERSION = "WS_OUTCOME_DEPTH_CORROBORATION_V1"

#: The refusals, each specific. Any one of them leaves the valuation with
#: OUTCOME_DEPTH_BELOW_FLOOR as well; none of them is ever manufactured away.
R_UNAVAILABLE = "CORROBORATION_UNAVAILABLE"
R_NOT_CURRENT = "CORROBORATION_NOT_CURRENT"
R_FUTURE = "CORROBORATION_FUTURE_STAMPED"
R_IDENTITY = "CORROBORATION_IDENTITY_MISMATCH"
R_SAME_SOURCE = "CORROBORATION_SAME_SOURCE"
R_BELOW_FLOOR = "CORROBORATION_BELOW_FLOOR"
R_BUDGET = "CORROBORATION_READ_BUDGET"
REFUSALS = (R_UNAVAILABLE, R_NOT_CURRENT, R_FUTURE, R_IDENTITY,
            R_SAME_SOURCE, R_BELOW_FLOOR, R_BUDGET)

#: Where the corroborating observation came from.
SRC_STORED_DISCOVERY = "STORED_DISCOVERY_READ"     # the WS seed's odds-API read
SRC_THIS_CYCLE = "THIS_CYCLE_ODDS_READ"            # the periodic cycle's own read
SRC_BOUNDED_READ = "BOUNDED_SINGLE_EVENT_READ"     # read for this WS evaluation

#: The only provider whose observation may corroborate a PinnAPI read. PinnAPI
#: itself is the SAME source; anything unrecognised is not evidence.
CORROBORATING_PROVIDER = P.LEGACY_PROVIDER
MARKET = "h2h"
PERIOD_FULL_GAME = "FULL_GAME"
PINNACLE = "pinnacle"
COUNT_FUNCTION = "ext_pinnacle_loop.pinnacle_h2h"
MAX_AGE_BASIS = "PINNACLE_MAX_AGE_S"
OUTCOME_BOOKS_BASIS = (
    "outcome_books is the PinnAPI read's own count (Pinnacle alone, 1). "
    "corroborating_outcome_books is a SEPARATE independent odds-API "
    "observation's count, computed by %s; it satisfies the floor only when "
    "qualified, and it is never written into outcome_books" % COUNT_FUNCTION)


def _stamp(book, market):
    """The book's provider observation instant: the stamp book_depth reads."""
    return P.epoch(market.get("last_update") or book.get("last_update"))


def _family_of(sport_key):
    key = str(sport_key or "")
    for fam in ("baseball", "soccer"):
        if key.startswith(fam + "_"):
            return fam
    return None


def assess(*, reference, wanted, selection, observation, count_fn,
           counted_books, min_books, max_age_s, at, contract, settlement_of):
    """One verdict on one observation, at the decision instant `at`.

    reference    the PinnAPI quote's `reference_input` (provider, feed event,
                 clocks, epoch, discovery identity)
    wanted       the WS valuation's complete outcome labels (dict or iterable)
    selection    the outcome the contract prices
    observation  {"provider", "event", "received_at", "source", "sport_key"}
                 or None
    count_fn     ext_pinnacle_loop.pinnacle_h2h (the odds-API path's counter)
    counted_books  the books count_fn counts (ext_pinnacle_loop.SHARP_BOOKS);
                 used only to say WHICH books were counted and whether an
                 identity exclusion could have mattered -- never to count
    contract     the valuation's contract (period, line, market, slug, rule)
    settlement_of  family -> the book's settlement rule for an h2h read
    """
    ref = dict(reference or {})
    wanted = [str(w) for w in (wanted or ())]
    obs = dict(observation or {})
    ev = obs.get("event") if isinstance(obs.get("event"), dict) else None
    out = {
        "version": VERSION,
        "qualified": False, "refusal": None, "why": None,
        "decision_at": at,
        "min_outcome_books": min_books,
        "max_age_s": max_age_s, "max_age_basis": MAX_AGE_BASIS,
        "outcome_books_basis": OUTCOME_BOOKS_BASIS,
        "pinnapi": {
            "provider": ref.get("provider"),
            "outcome_books": 1,
            "source_change_ms": ref.get("source_change_ms"),
            "received_ms": ref.get("received_ms"),
            "epoch": ref.get("epoch"),
            "runtime_id": ref.get("runtime_id"),
            "feed_event_id": ref.get("feed_event_id"),
            "market_key": ref.get("market_key"),
            "raw_odds": ref.get("raw_odds"),
            # filled by bettor_external_shadow.evaluate from the one de-vig
            "probability_of_selection": None, "probability": None},
        "corroborating": {
            "provider": obs.get("provider"), "source": obs.get("source"),
            "event_id": (ev or {}).get("id"),
            "received_at": obs.get("received_at"),
            "observed_at": None, "age_s": None,
            "outcome_books": None, "books": [], "book_stamps": {},
            "outcome_books_any_age": None,
            "count_function": COUNT_FUNCTION, "excluded": []},
        "identity": {
            "matched": False, "mismatches": [],
            "pinnapi_feed_event_id": ref.get("feed_event_id"),
            "discovery_event_id": ref.get("discovery_event_id"),
            "corroborating_event_id": (ev or {}).get("id"),
            "outcome": selection, "outcome_set": sorted(wanted),
            "period": (contract or {}).get("period"),
            "market": (contract or {}).get("market"),
            "line": (contract or {}).get("line"),
            "us_market_slug": (contract or {}).get("us_market_slug"),
            "settlement_rule": (contract or {}).get("settlement_rule"),
            "how_matched": None},
        "corroborating_outcome_books": None,
    }

    def refuse(code, why):
        out.update(qualified=False, refusal=code, why=why)
        return out

    if P.epoch(at) is None or P.epoch(max_age_s) is None or max_age_s < 0:
        return refuse(R_UNAVAILABLE, "decision clock or age bound is invalid")
    if ev is None:
        return refuse(R_UNAVAILABLE, "no independent observation is held "
                      "for this event")
    if obs.get("provider") == P.PROVIDER or obs.get("provider") == ref.get("provider"):
        return refuse(R_SAME_SOURCE, "the observation is the PinnAPI read's "
                      "own provider; a source cannot corroborate itself")
    if obs.get("provider") != CORROBORATING_PROVIDER:
        out["identity"]["mismatches"].append(
            {"element": "provider", "got": obs.get("provider"),
             "want": CORROBORATING_PROVIDER})
        return refuse(R_IDENTITY, "unrecognised corroborating provider")

    # ── EXACT IDENTITY, ELEMENT BY ELEMENT ───────────────────────────
    mism = out["identity"]["mismatches"]
    c = dict(contract or {})
    family = ref.get("family")
    # the event: the odds-API event the PinnAPI read was matched from
    for el, got, want in (
            ("event_id", ev.get("id"), ref.get("discovery_event_id")),
            ("event_id_vs_contract", ev.get("id"), c.get("event_key")),
            ("home_team", ev.get("home_team"), ref.get("discovery_home")),
            ("away_team", ev.get("away_team"), ref.get("discovery_away"))):
        if got is None or want is None or str(got) != str(want):
            mism.append({"element": "event", "field": el, "got": got,
                         "want": want})
    s_got, s_want = P.epoch(ev.get("commence_time")), P.epoch(ref.get("discovery_start"))
    if s_got is None or s_want is None or s_got != s_want:
        mism.append({"element": "event", "field": "commence_time",
                     "got": ev.get("commence_time"),
                     "want": ref.get("discovery_start")})
    obs_family = _family_of(ev.get("sport_key") or obs.get("sport_key"))
    if obs_family is None or obs_family != family:
        mism.append({"element": "event", "field": "sport_family",
                     "got": obs_family, "want": family})
    # the WS side: the full-game moneyline, no line, the contract's market
    from . import pinnapi_feed as F
    if ref.get("market_key") != F.FULL_GAME_MONEYLINE_KEY:
        mism.append({"element": "period", "field": "pinnapi_market_key",
                     "got": ref.get("market_key"),
                     "want": F.FULL_GAME_MONEYLINE_KEY})
    if str(c.get("period") or "") != PERIOD_FULL_GAME:
        mism.append({"element": "period", "field": "contract_period",
                     "got": c.get("period"), "want": PERIOD_FULL_GAME})
    if c.get("line") is not None:
        mism.append({"element": "line", "field": "contract_line",
                     "got": c.get("line"), "want": None})
    if c.get("market") != MARKET:
        mism.append({"element": "market", "field": "contract_market",
                     "got": c.get("market"), "want": MARKET})
    if selection is None or str(selection) not in wanted:
        mism.append({"element": "outcome", "field": "selection",
                     "got": selection, "want": sorted(wanted)})
    # the settlement interpretation of an odds-API h2h read of this family
    rule = (settlement_of or {}).get(obs_family)
    if rule is None or rule != c.get("settlement_rule"):
        mism.append({"element": "settlement", "field": "settlement_rule",
                     "got": rule, "want": c.get("settlement_rule")})
    if mism:
        return refuse(R_IDENTITY, "the observation is not the WS valuation's "
                      "exact event/period/line/market/settlement: %s"
                      % sorted({m["element"] for m in mism}))

    # ── THE MARKETS: exact identity only, one entry per bookmaker ──────
    want_set = set(wanted)
    exact_any_age, exact_current = [], []
    future_keys, stale_keys, seen = set(), set(), set()
    excluded = out["corroborating"]["excluded"]
    non_pinnacle_offered = False
    for b in ev.get("bookmakers") or []:
        key = b.get("key") if isinstance(b, dict) else None
        if not key:
            continue
        for m in b.get("markets") or []:
            mk = str(m.get("key") or "")
            outcomes = [o for o in (m.get("outcomes") or []) if isinstance(o, dict)]
            names = {str(o.get("name")) for o in outcomes}
            why = None
            if mk.startswith(MARKET + "_") and mk != "h2h_lay":
                why = "period"            # h2h_h1, h2h_q1, h2h_1st_5_innings ...
            elif mk != MARKET:
                why = "market"            # spreads, totals, h2h_lay ...
            elif any(o.get("point") is not None for o in outcomes):
                why = "line"
            elif names != want_set:
                why = ("settlement" if names ^ want_set == {"Draw"}
                       else "outcome")
            if why:
                excluded.append({"book": key, "market": mk, "element": why})
                continue
            if key in seen:
                excluded.append({"book": key, "market": mk,
                                 "element": "duplicate_book_entry"})
                continue
            seen.add(key)
            if key != PINNACLE:
                non_pinnacle_offered = True
            entry = {"key": key, "last_update": m.get("last_update")
                     or b.get("last_update"), "markets": [
                         {"key": MARKET, "last_update": m.get("last_update")
                          or b.get("last_update"), "outcomes": outcomes}]}
            exact_any_age.append(entry)
            stamp = _stamp(b, m)
            out["corroborating"]["book_stamps"][key] = (
                m.get("last_update") or b.get("last_update"))
            if stamp is None:
                stale_keys.add(key)
            elif at - stamp < 0:
                future_keys.add(key)
            elif at - stamp > max_age_s:
                stale_keys.add(key)
            else:
                exact_current.append((entry, stamp))

    def count(books):
        got = count_fn({"id": ev.get("id"), "home_team": ev.get("home_team"),
                        "away_team": ev.get("away_team"),
                        "commence_time": ev.get("commence_time"),
                        "bookmakers": books},
                       received_at=obs.get("received_at"))
        if not got:
            return 0
        n = (got.get("depth") or {}).get(str(selection))
        return int(n) if isinstance(n, int) and not isinstance(n, bool) else 0

    countable = set(counted_books or ())
    n_any = count(exact_any_age)
    out["corroborating"]["outcome_books_any_age"] = n_any
    if n_any < min_books:
        identity_exclusions = [x for x in excluded
                               if x["element"] != "duplicate_book_entry"
                               and x["book"] in countable]
        if identity_exclusions:
            mism.extend({"element": x["element"], "field": "market",
                         "book": x["book"], "got": x["market"]}
                        for x in identity_exclusions)
            return refuse(R_IDENTITY, "the observation's books that could "
                          "corroborate quote a different %s"
                          % sorted({x["element"] for x in identity_exclusions}))
        if not non_pinnacle_offered:
            return refuse(R_SAME_SOURCE, "the observation offers Pinnacle "
                          "alone -- the same book as the PinnAPI read")
        return refuse(R_BELOW_FLOOR, "the independent observation itself has "
                      "%d book(s) on %r by %s, below %d"
                      % (n_any, selection, COUNT_FUNCTION, min_books))

    current = [e for e, _ in exact_current]
    n_now = count(current)
    if n_now < min_books:
        if future_keys:
            return refuse(R_FUTURE, "book(s) %s are stamped after the decision "
                          "instant; a future stamp is not freshness"
                          % sorted(future_keys))
        return refuse(R_NOT_CURRENT, "only %d book(s) stamped within %ss "
                      "(%s) of the decision; stale: %s"
                      % (n_now, max_age_s, MAX_AGE_BASIS, sorted(stale_keys)))

    # Counted books on the selection, and the OLDEST of their stamps governs.
    counted = [(e, s) for e, s in exact_current if e["key"] in countable]
    if len(counted) != n_now:
        return refuse(R_UNAVAILABLE, "the counted books (%d) disagree with "
                      "%s (%d); nothing is certified on a disagreement"
                      % (len(counted), COUNT_FUNCTION, n_now))
    oldest = min(s for _, s in counted)
    out["corroborating"].update(
        observed_at=oldest, age_s=round(at - oldest, 3),
        outcome_books=n_now, books=sorted(e["key"] for e, _ in counted))
    out["corroborating_outcome_books"] = n_now
    out["identity"].update(
        matched=True,
        how_matched=(
            "odds-API event id %r is the discovery event the PinnAPI read "
            "was matched from (feed event %r); home/away/commence_time and "
            "sport family equal; market 'h2h' (full game, no line) with the "
            "identical outcome set %s on every counted book; settlement %s"
            % (ev.get("id"), ref.get("feed_event_id"), sorted(wanted),
               c.get("settlement_rule"))))
    if not (0 <= out["corroborating"]["age_s"] <= max_age_s):
        return refuse(R_NOT_CURRENT, "governing stamp outside the bound")
    out.update(qualified=True, refusal=None,
               why=("%d independent books on %r within %.1fs/%ss (%s)"
                    % (n_now, selection, at - oldest, max_age_s,
                       MAX_AGE_BASIS)))
    return out


# ── THE BOUNDED SINGLE-EVENT READ BUDGET ───────────────────────────────
#
# A WS change for an event whose stored odds-API read is older than the bound
# may trigger ONE single-event odds-API read. Each read is metered by the
# provider (request x market x region: 1 market x 3 regions here, so ~3
# credits; the actual figure is recorded from the response's
# `x-requests-last` header, never assumed). Three budgets, all in-process:
#
#   PER_EVENT_MIN_INTERVAL_S  at most one read per event per 60 s
#   GLOBAL_PER_MINUTE         at most 3 reads in any rolling 60 s
#   GLOBAL_PER_HOUR           at most 30 reads in any rolling hour
#
# Worst case 30/h x 24 = 720 reads/day, ~2.2k credits/day at 3 credits, beside
# the collector's ~7.7k/day (METERED_BUDGET_CHANGE). A refused read is
# recorded as CORROBORATION_READ_BUDGET; it is never queued or retried.
PER_EVENT_MIN_INTERVAL_S = 60.0
GLOBAL_PER_MINUTE = 3
GLOBAL_PER_HOUR = 30
READ_TIMEOUT_S = 4.0
_EVENT_CAP = 2048


class ReadBudget:
    def __init__(self, *, per_event_s=PER_EVENT_MIN_INTERVAL_S,
                 per_minute=GLOBAL_PER_MINUTE, per_hour=GLOBAL_PER_HOUR,
                 clock=time.time):
        self.per_event_s, self.per_minute, self.per_hour = (
            per_event_s, per_minute, per_hour)
        self.clock = clock
        self.last_by_event: dict = {}
        self.reads: deque = deque()

    def acquire(self, event_key) -> dict:
        now = float(self.clock())
        while self.reads and now - self.reads[0] > 3600.0:
            self.reads.popleft()
        minute = sum(1 for t in self.reads if now - t <= 60.0)
        last = self.last_by_event.get(str(event_key))
        state = {"at": now, "event_key": str(event_key),
                 "per_event_min_interval_s": self.per_event_s,
                 "global_per_minute": self.per_minute,
                 "global_per_hour": self.per_hour,
                 "reads_last_minute": minute,
                 "reads_last_hour": len(self.reads),
                 "last_read_for_event_at": last}
        if last is not None and 0 <= now - last < self.per_event_s:
            return dict(state, ok=False, refusal=R_BUDGET,
                        limit="PER_EVENT_MIN_INTERVAL_S")
        if minute >= self.per_minute:
            return dict(state, ok=False, refusal=R_BUDGET,
                        limit="GLOBAL_PER_MINUTE")
        if len(self.reads) >= self.per_hour:
            return dict(state, ok=False, refusal=R_BUDGET,
                        limit="GLOBAL_PER_HOUR")
        self.reads.append(now)
        self.last_by_event[str(event_key)] = now
        if len(self.last_by_event) > _EVENT_CAP:
            for k in sorted(self.last_by_event,
                            key=self.last_by_event.get)[:len(self.last_by_event) - _EVENT_CAP]:
                del self.last_by_event[k]
        return dict(state, ok=True, refusal=None)


BUDGET = ReadBudget()


def budget_reset() -> None:
    """For tests; never called in the loop."""
    global BUDGET
    BUDGET = ReadBudget()


def finite_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── PERSISTENCE (migration 195) ─────────────────────────────────────────

INSERT = """
    INSERT INTO valuation_corroboration
        (valuation_id, version, qualified, refusal, why,
         pinnapi_provider, pinnapi_outcome_books, pinnapi_probability,
         pinnapi_source_change_ms, pinnapi_received_ms, pinnapi_epoch,
         pinnapi_runtime_id,
         corroborating_provider, corroborating_source,
         corroborating_observed_at, corroborating_received_at,
         corroborating_outcome_books, corroborating_books,
         corroboration_age_s, max_age_s, max_age_basis, decision_at,
         discovery_event_id, feed_event_id, corroborating_event_id,
         outcome, period, market, line, us_market_slug, settlement_rule,
         identity, detail)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
            CASE WHEN $15::double precision IS NULL THEN NULL
                 ELSE to_timestamp($15) END,
            CASE WHEN $16::double precision IS NULL THEN NULL
                 ELSE to_timestamp($16) END,
            $17,$18::text[],$19,$20,$21,to_timestamp($22),
            $23,$24,$25,$26,$27,$28,$29,$30,$31,$32::jsonb,$33::jsonb)
"""


def insert_args(valuation_id, verdict):
    import json
    v = dict(verdict or {})
    pin = v.get("pinnapi") or {}
    cor = v.get("corroborating") or {}
    idt = v.get("identity") or {}

    def s(x):
        return None if x is None else str(x)
    epoch_ = pin.get("epoch")
    return (int(valuation_id), str(v.get("version") or VERSION),
            bool(v.get("qualified")), v.get("refusal"),
            s(v.get("why")),
            str(pin.get("provider") or ""), int(pin.get("outcome_books") or 0),
            finite_or_none(pin.get("probability_of_selection")),
            finite_or_none(pin.get("source_change_ms")),
            finite_or_none(pin.get("received_ms")),
            (None if epoch_ is None else int(epoch_)),
            s(pin.get("runtime_id")),
            s(cor.get("provider")), s(cor.get("source")),
            finite_or_none(cor.get("observed_at")),
            finite_or_none(cor.get("received_at")),
            cor.get("outcome_books"), list(cor.get("books") or []),
            finite_or_none(cor.get("age_s")),
            float(v.get("max_age_s")), str(v.get("max_age_basis")),
            float(v.get("decision_at")),
            s(idt.get("discovery_event_id")), s(idt.get("pinnapi_feed_event_id")),
            s(idt.get("corroborating_event_id")),
            s(idt.get("outcome")), s(idt.get("period")), s(idt.get("market")),
            finite_or_none(idt.get("line")), s(idt.get("us_market_slug")),
            s(idt.get("settlement_rule")),
            json.dumps(idt, default=str), json.dumps(v, default=str))
