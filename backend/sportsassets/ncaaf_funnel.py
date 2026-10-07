"""THE NCAAF FUNNEL: EVERY LOSS NAMED, PER EVENT (closeout).

Production 2026-10-06: 108 venue cfb events in seven days (11,120
contracts: 804 winner, 4,449 spread, 5,635 totals), 37 matched to a PinnAPI
fixture, 3 entered -- and no per-event account of the other 71, so "mapped
N -> evaluated 0" could not be explained. This builds the funnel stage by
stage with the exact reason for every loss:

  PROVIDER_EVENT          the provider's NCAA fixtures (PinnAPI native
                          discovery receipts, pinnapi_discovery.watch)
  FIXTURE_IDENTIFIED      the receipt parsed (not NORMALIZATION_FAILURE)
  VENUE_EVENT_FOUND       MATCHED to exactly one venue event (else the
                          receipt's own state: PARTICIPANT_MISMATCH with its
                          venue candidates, TIME_MISMATCH with its offset,
                          AMBIGUOUS, DUPLICATE_CANDIDATES, NO_VENUE_COUNTERPART)
  VENUE_CONTRACTS         the venue event lists contracts (us_premap)
  ONTOLOGY_MAPPED         at least one contract is a full-game winner or a
                          proven line family (bettor_market_family)
  SETTLEMENT_SUPPORTED    at least one valuation / decision of it carries no
                          SETTLEMENT_COMPATIBILITY refusal
  FAIR_VALUE_AVAILABLE    a valuation with a probability
  CURRENT_BOOK            a valuation with an executable price (or a
                          decision that read a book)
  PROBABILITY_FRESH       a probability inside the unchanged 30 s rule
  ECONOMICALLY_EVALUATED  a paper decision
  APPROVED / REFUSED      a decision ENTER, else the refusal codes

The venue side is reported apart: every venue cfb event no provider fixture
matched, with why (named by a mismatching provider receipt, else the
provider holds no fixture for it). A loss at a stage names the most frequent
code of that stage's taxonomy stage the event recorded, else the most
frequent code it recorded at all, else NO_RECORD. Pure apart from `read`.
READ ONLY; nothing here writes, sizes or orders.
"""
from __future__ import annotations

import collections
import json
import time

VERSION = "NCAAF_FUNNEL_V1"
VENUE_LEAGUE = "cfb"
WINDOW_BACK_H = 6
WINDOW_AHEAD_D = 7
VALUATION_LOOKBACK_H = 24
FRESH_LIMIT_S = 30.0          # the existing Pinnacle rule, never widened

STAGES = ("PROVIDER_EVENT", "FIXTURE_IDENTIFIED", "VENUE_EVENT_FOUND",
          "VENUE_CONTRACTS", "ONTOLOGY_MAPPED", "SETTLEMENT_SUPPORTED",
          "FAIR_VALUE_AVAILABLE", "CURRENT_BOOK", "PROBABILITY_FRESH",
          "ECONOMICALLY_EVALUATED", "APPROVED")
#: the refusal taxonomy's stages that explain a loss at each funnel stage
TAXONOMY_STAGES = {
    "VENUE_CONTRACTS": ("VENUE_MAPPING",),
    "ONTOLOGY_MAPPED": ("MARKET_FAMILY",),
    "SETTLEMENT_SUPPORTED": ("SETTLEMENT_COMPATIBILITY",),
    "FAIR_VALUE_AVAILABLE": ("PROBABILITY", "INGESTION", "EVENT_IDENTITY"),
    "CURRENT_BOOK": ("VENUE_BOOK",),
    "PROBABILITY_FRESH": ("FRESHNESS",),
    "ECONOMICALLY_EVALUATED": ("AGENT_EVALUATION",),
    "APPROVED": ("EV", "RISK_ADMISSION", "ORDER"),
}
MATCHED = "MATCHED"
NORMALIZATION_FAILURE = "NORMALIZATION_FAILURE"
R_NO_PROVIDER_FIXTURE = "PROVIDER_HOLDS_NO_FIXTURE_FOR_THE_VENUE_EVENT"
R_NAMED_BY_MISMATCH = "PROVIDER_FIXTURE_NAMES_IT_BUT_IDENTITY_MISMATCHES"
R_NO_RECORD = "NO_RECORD_AT_THIS_STAGE"
R_NOT_VALUED = "COLLECTOR_RECORDED_NO_VALUATION_OR_DECISION_IN_24H"
R_WATCH_UNAVAILABLE = "PROVIDER_DISCOVERY_WATCH_NOT_RECORDED"
WINNER_TYPES = ("football_team_full_game_winner",)


def _stage_of(code) -> str | None:
    from . import refusal_taxonomy as RT
    row = RT.lookup(code)
    return None if row is None else row[2]


def _norm(code):
    from . import refusal_taxonomy as RT
    return RT.normalize(code)


def ontology_ok(sports_type) -> bool:
    from . import bettor_market_family as MF
    st = str(sports_type or "")
    if st in WINNER_TYPES:
        return True
    fam = MF.venue_line_family(st)
    if fam.get("refusal"):
        return False
    return bool(MF.family_status(fam["sport"], fam["family"]).get("proven"))


def loss_reason(stage: str, codes) -> str:
    """The code that names a loss at `stage` (see the module docstring)."""
    cs = [c for c in (_norm(x) for x in (codes or ())) if c]
    want = TAXONOMY_STAGES.get(stage) or ()
    at = collections.Counter(c for c in cs if _stage_of(c) in want)
    if at:
        return at.most_common(1)[0][0]
    allc = collections.Counter(cs)
    return allc.most_common(1)[0][0] if allc else R_NO_RECORD


def event_progress(*, contracts: list, valuations: list,
                   decisions: list) -> dict:
    """How far ONE matched venue event got, and the reason it stopped.
    `contracts` [{sports_type}], `valuations` [{probability, age_s,
    executable_price, refusals}], `decisions` [{verdict, refusal, refusals,
    book_obs_id, pin_qualified}]. Pure."""
    codes = []
    for v in valuations:
        codes += list(v.get("refusals") or ())
    for d in decisions:
        if d.get("refusal"):
            codes.append(d["refusal"])
        codes += list(d.get("refusals") or ())

    def no_set(cs):
        return not any(_stage_of(c) == "SETTLEMENT_COMPATIBILITY"
                       for c in (_norm(x) for x in (cs or ())) if c)
    flags = [
        ("VENUE_CONTRACTS", bool(contracts)),
        ("ONTOLOGY_MAPPED", any(ontology_ok(c.get("sports_type"))
                                for c in contracts)),
        ("SETTLEMENT_SUPPORTED",
         any(no_set(v.get("refusals")) for v in valuations)
         or any(no_set([d.get("refusal")] + list(d.get("refusals") or ()))
                for d in decisions)),
        ("FAIR_VALUE_AVAILABLE",
         any(v.get("probability") is not None for v in valuations)),
        ("CURRENT_BOOK",
         any(v.get("executable_price") is not None for v in valuations)
         or any(d.get("book_obs_id") is not None for d in decisions)),
        ("PROBABILITY_FRESH",
         any(v.get("probability") is not None and v.get("age_s") is not None
             and 0 <= float(v["age_s"]) <= FRESH_LIMIT_S
             for v in valuations)
         or any(str(d.get("pin_qualified")).lower() == "true"
                for d in decisions)),
        ("ECONOMICALLY_EVALUATED", bool(decisions)),
        ("APPROVED", any(d.get("verdict") == "ENTER" for d in decisions)),
    ]
    reached = "VENUE_EVENT_FOUND"
    for stage, ok in flags:
        if not ok:
            if stage == "SETTLEMENT_SUPPORTED" and not valuations \
                    and not decisions:
                return {"reached": reached, "lost_at": stage,
                        "reason": R_NOT_VALUED, "codes": []}
            return {"reached": reached, "lost_at": stage,
                    "reason": loss_reason(stage, codes),
                    "codes": collections.Counter(
                        c for c in (_norm(x) for x in codes) if c
                    ).most_common(5)}
        reached = stage
    return {"reached": reached, "lost_at": None, "reason": None,
            "codes": []}


def build(*, watch: dict | None, venue: dict, valuations: dict,
          decisions: dict, now: float) -> dict:
    """THE FUNNEL (pure). `venue` {event_slug: {"contracts": [...],
    "game_start": t}}, `valuations` / `decisions` {event_slug: [...]}."""
    counts = collections.Counter()
    losses = collections.defaultdict(collections.Counter)
    events = []
    w = watch or {}
    prov = list(w.get("provider") or [])
    matched = {}
    named_by = collections.defaultdict(list)
    if watch is None:
        counts["PROVIDER_EVENT"] = 0
    for r in prov:
        counts["PROVIDER_EVENT"] += 1
        st = r.get("state")
        if st == NORMALIZATION_FAILURE:
            losses["FIXTURE_IDENTIFIED"][r.get("reason") or st] += 1
            continue
        counts["FIXTURE_IDENTIFIED"] += 1
        slug = r.get("venue_event_slug")
        if st == MATCHED and slug:
            matched[slug] = r
            continue
        losses["VENUE_EVENT_FOUND"][st] += 1
        for cand in ([slug] if slug else []) + list(r.get("candidates")
                                                    or []):
            if cand:
                named_by[cand].append({"fixture_id": r.get("fixture_id"),
                                       "state": st, "home": r.get("home"),
                                       "away": r.get("away"),
                                       "start_offset_s":
                                           r.get("start_offset_s")})
    for slug, r in sorted(matched.items()):
        v = venue.get(slug)
        if v is None:
            # matched to a venue event outside this window / not cfb
            losses["VENUE_EVENT_FOUND"]["MATCHED_VENUE_EVENT_OUTSIDE_THE_"
                                        "CFB_WINDOW"] += 1
            continue
        counts["VENUE_EVENT_FOUND"] += 1
        prog = event_progress(contracts=v["contracts"],
                              valuations=valuations.get(slug, []),
                              decisions=decisions.get(slug, []))
        for st in STAGES[STAGES.index("VENUE_CONTRACTS"):]:
            if prog["lost_at"] == st:
                losses[st][prog["reason"]] += 1
                break
            counts[st] += 1
        events.append({"event_slug": slug,
                       "fixture_id": r.get("fixture_id"),
                       "game_start": v.get("game_start"),
                       "contracts": len(v["contracts"]), **prog})
    venue_unmatched = []
    for slug in sorted(set(venue) - set(matched)):
        why = (R_NAMED_BY_MISMATCH if named_by.get(slug)
               else R_WATCH_UNAVAILABLE if watch is None
               else R_NO_PROVIDER_FIXTURE)
        venue_unmatched.append({
            "event_slug": slug, "game_start": venue[slug].get("game_start"),
            "contracts": len(venue[slug]["contracts"]), "reason": why,
            "provider_receipts": named_by.get(slug, [])[:3]})
    stage_rows = []
    prev = None
    for st in STAGES:
        n = counts.get(st, 0)
        stage_rows.append({"stage": st, "count": n,
                           "lost_from_previous": (None if prev is None
                                                  else prev - n),
                           "losses": dict(losses.get(st) or {})})
        prev = n
    refused = sum(1 for e in events if e["lost_at"] == "APPROVED")
    # every loss from the previous stage is named (reconciliation)
    unexplained = [r["stage"] for r in stage_rows
                   if r["lost_from_previous"] not in (None, 0)
                   and sum(r["losses"].values()) != r["lost_from_previous"]]
    return {"version": VERSION, "computed_at": now,
            "league": VENUE_LEAGUE, "window": {
                "back_h": WINDOW_BACK_H, "ahead_d": WINDOW_AHEAD_D,
                "valuation_lookback_h": VALUATION_LOOKBACK_H},
            "watch": {"recorded": watch is not None,
                      "computed_at": w.get("computed_at"),
                      "pass_state": w.get("pass_state")},
            "stages": stage_rows, "refused": refused,
            "unexplained_losses": unexplained,
            "venue_side": {
                "venue_events": len(venue),
                "contracts": sum(len(v["contracts"]) for v in venue.values()),
                "matched": len(set(venue) & set(matched)),
                "unmatched": len(venue_unmatched),
                "unmatched_by_reason": dict(collections.Counter(
                    u["reason"] for u in venue_unmatched)),
                "unmatched_events": venue_unmatched[:150]},
            "events": events[:150],
            "label": "READ_ONLY_RESEARCH"}


VENUE_SQL = """
    SELECT event_slug, market_slug, sports_type,
           extract(epoch FROM game_start)::float8 AS game_start
      FROM us_premap
     WHERE team_league = $1
       AND game_start > now() - make_interval(hours => $2)
       AND game_start < now() + make_interval(days => $3)
       AND market_slug IS NOT NULL
"""
VALUATIONS_SQL = """
    SELECT us_market_slug, probability, age_s, executable_price, refusals
      FROM external_valuations
     WHERE us_market_slug = ANY($1::text[])
       AND decided_at > now() - make_interval(hours => $2)
     ORDER BY decided_at DESC LIMIT 20000
"""
DECISIONS_SQL = """
    SELECT us_market_slug, verdict, refusal, refusals, book_obs_id,
           pinnacle->>'qualified' AS pin_qualified
      FROM paper_decisions
     WHERE us_market_slug = ANY($1::text[])
       AND decided_at > now() - make_interval(hours => $2)
     ORDER BY decided_at DESC LIMIT 20000
"""


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def read(conn, *, now: float | None = None) -> dict:
    """The funnel from production rows (SELECT only)."""
    from .pinnapi_feed_runtime import DISCOVERY_WATCH_KEY
    now = time.time() if now is None else now
    w = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            DISCOVERY_WATCH_KEY)
    watch = _j(w) if w is not None else None
    venue: dict = {}
    slug_event = {}
    for r in await conn.fetch(VENUE_SQL, VENUE_LEAGUE, WINDOW_BACK_H,
                              WINDOW_AHEAD_D):
        e = venue.setdefault(r["event_slug"], {"contracts": [],
                                               "game_start": r["game_start"]})
        e["contracts"].append({"market_slug": r["market_slug"],
                               "sports_type": r["sports_type"]})
        slug_event[r["market_slug"]] = r["event_slug"]
    slugs = sorted(slug_event)
    vals: dict = collections.defaultdict(list)
    decs: dict = collections.defaultdict(list)
    if slugs:
        for r in await conn.fetch(VALUATIONS_SQL, slugs,
                                  VALUATION_LOOKBACK_H):
            vals[slug_event[r["us_market_slug"]]].append(dict(r))
        for r in await conn.fetch(DECISIONS_SQL, slugs,
                                  VALUATION_LOOKBACK_H):
            d = dict(r)
            d["refusals"] = _j(d.get("refusals")) or []
            if not isinstance(d["refusals"], list):
                d["refusals"] = []
            decs[slug_event[r["us_market_slug"]]].append(d)
    return build(watch=watch, venue=venue, valuations=vals,
                 decisions=decs, now=now)
