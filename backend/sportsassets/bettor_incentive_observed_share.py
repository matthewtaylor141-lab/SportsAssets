"""THE OBSERVATION DELIVERABLE: the captured terms against OUR ladders.

The opportunity script has only ever run on ladders from markets carrying
NO incentive programme -- boxing and college football out of a captured
tape. It says so in its own header, and that made it a demonstration that
the pipeline computes, not a reading of the programme we observed.

This module closes that: it reads the ladders the collector actually
persisted for the PROGRAMME's markets, and scores them against the terms
captured in the manifest, through the SAME `bettor_incentive_score` engine
the script uses. No second scorer, no re-derived reward formula.

WHAT IT IS NOT, STATED BEFORE THE NUMBERS.

  * It is a HYPOTHETICAL ORDER SHARE. A clip is inserted into a book we
    observed; it changes the denominator and not other people's behaviour,
    so the share is an upper reading at the observed depth.
  * NOTHING WAS EARNED. No order was placed in any of these markets, and
    `reward_gross_usd` is what the terms would have paid for that share --
    not money, not accrued, not receivable.
  * TWELVE MARKETS ARE ONE PROGRAMME AND ONE EVENT. They are not twelve
    independent observations, and the per-market rows must not be averaged
    into a portfolio claim.
  * Uptime is measured over the snapshots WE HOLD. The window's first 6.47
    hours were never observed, so a 100% uptime row means "in every
    snapshot we have", not "all day".
"""
from __future__ import annotations

import os

VERSION = "BETTOR_INCENTIVE_OBSERVED_SHARE_V1"

#: Where the image keeps the captured manifest and the scorer. Both are
#: copied narrowly by the Dockerfile; absence is reported, never guessed
#: around.
RESEARCH_DIR = os.environ.get(
    "BETTOR_INCENTIVE_RESEARCH_DIR", "/app/research/beta48")
MANIFEST_PATH = os.environ.get(
    "BETTOR_INCENTIVE_MANIFEST",
    os.path.join(RESEARCH_DIR, "acceptance", "incentive_manifest.json"))

#: Ladder snapshots read per market. Bounded: this is an analysis endpoint
#: behind the command session, not an export.
MAX_SNAPSHOTS_PER_MARKET = 400

LADDERS_SQL = """
    SELECT slug, at, payload
      FROM bettor_incentive_journal
     WHERE kind = 'LADDER' AND slug = ANY($1::text[])
     ORDER BY slug, at
"""

OBSERVED_SLUGS_SQL = """
    SELECT slug, count(*) AS frames,
           min(at) AS first_at, max(at) AS last_at
      FROM bettor_incentive_journal
     WHERE kind = 'LADDER' AND slug IS NOT NULL
     GROUP BY slug ORDER BY slug
"""


def _levels(raw) -> list:
    """A journal side -> [(price, qty)], or [] when unreadable.

    The journal stores the venue's own level objects. Keys are read
    defensively and a level that cannot be read as two numbers is DROPPED
    rather than defaulted to zero, which would invent depth.
    """
    out = []
    for lv in (raw or []):
        if isinstance(lv, dict):
            px = lv.get("price", lv.get("px", lv.get("p")))
            qty = lv.get("size", lv.get("qty", lv.get("q", lv.get("quantity"))))
        elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
            px, qty = lv[0], lv[1]
        else:
            continue
        try:
            out.append((float(px), float(qty)))
        except (TypeError, ValueError):
            continue
    return out


def _import_engine():
    """The SAME engine the script uses, or a named refusal."""
    import sys

    if RESEARCH_DIR not in sys.path:
        sys.path.insert(0, RESEARCH_DIR)
    try:
        import bettor_incentive_opportunity as opp  # noqa: WPS433
        import bettor_incentive_score as inc        # noqa: WPS433
    except Exception as exc:                        # noqa: BLE001
        return None, None, {"refusal": "SCORER_NOT_IN_IMAGE",
                            "error": type(exc).__name__,
                            "looked_in": RESEARCH_DIR,
                            "why": ("the analysis is not re-implemented "
                                    "here; without the engine it refuses")}
    return inc, opp, None


async def observed_share(conn, *, clip=100.0, offsets=(0, 1),
                         et_date=None) -> dict:
    """Score the programme's captured terms against the ladders we hold."""
    inc, opp, refusal = _import_engine()
    out = {"version": VERSION, "clip": float(clip),
           "offsets_ticks": list(offsets),
           "manifest_path": MANIFEST_PATH,
           "nothing_was_earned": (
               "no order was placed in any of these markets. Every figure "
               "below is a hypothetical share at observed depth"),
           "one_programme_one_event": (
               "these markets belong to ONE programme and ONE event and are "
               "not independent observations"),
           }
    if refusal:
        out.update(ok=False, **refusal)
        return out
    try:
        loaded = opp.programs_from_manifest(MANIFEST_PATH, et_date=et_date)
    except Exception as exc:                                   # noqa: BLE001
        out.update(ok=False, refusal="MANIFEST_UNREADABLE",
                   error=type(exc).__name__)
        return out
    if not loaded.get("ok"):
        # The loader's OWN refusal, carried rather than flattened: a
        # manifest that did not freeze and a manifest that is missing are
        # different problems.
        out.update(ok=False, refusal="MANIFEST_DID_NOT_FREEZE",
                   why=loaded.get("why"), detail=loaded.get("detail"))
        return out
    # {market_slug: Program}, straight from the loader.
    by_slug = dict(loaded.get("programs") or {})
    out["programme_markets"] = sorted(by_slug)
    out["programme_ids"] = sorted({
        getattr(pr, "program_id", None) or "?" for pr in by_slug.values()})
    out["et_date"] = loaded.get("et_date")
    out["captured_terms"] = {
        s: {"program_id": getattr(pr, "program_id", None),
            "reward_pool": getattr(pr, "reward_pool", None),
            "discount_factor": getattr(pr, "discount_factor", None),
            "target_size": getattr(pr, "target_size", None),
            "period": getattr(pr, "period", None)}
        for s, pr in sorted(by_slug.items())}

    observed = [dict(r) for r in await conn.fetch(OBSERVED_SLUGS_SQL)]
    out["observed_markets"] = [r["slug"] for r in observed]

    # THE JOIN, REPORTED BOTH WAYS. A programme market we never observed
    # and an observed market outside the programme are different facts, and
    # silently intersecting them would hide either.
    matched = [s for s in out["observed_markets"] if s in by_slug]
    out["matched_markets"] = matched
    out["programme_markets_not_observed"] = sorted(
        s for s in by_slug if s and s not in set(out["observed_markets"]))
    out["observed_markets_not_in_programme"] = sorted(
        s for s in out["observed_markets"] if s not in by_slug)
    if not matched:
        out.update(
            ok=False, refusal="NO_OBSERVED_MARKET_IS_IN_THE_PROGRAMME",
            why=("the manifest's markets and the collected slugs do not "
                 "intersect, so there is no programme ladder to score. "
                 "Scoring the observed markets against these terms anyway "
                 "would be the scenario the script already labels, not the "
                 "deliverable"))
        return out

    rows = await conn.fetch(LADDERS_SQL, matched)
    snaps: dict = {}
    for r in rows:
        s = str(r["slug"])
        bucket = snaps.setdefault(s, [])
        if len(bucket) >= MAX_SNAPSHOTS_PER_MARKET:
            continue
        pay = r["payload"] or {}
        if isinstance(pay, str):
            import json as _json
            try:
                pay = _json.loads(pay)
            except Exception:                                  # noqa: BLE001
                continue
        bucket.append({"BID": _levels(pay.get("bids")),
                       "ASK": _levels(pay.get("offers"))})

    results = []
    for slug in matched:
        got = snaps.get(slug) or []
        if not got:
            results.append({"market": slug, "snapshots": 0,
                            "status": "NO_READABLE_LADDER_SNAPSHOT"})
            continue
        for off in offsets:
            try:
                m = opp.measure(got, by_slug[slug], clip=float(clip),
                                offset_ticks=int(off))
            except Exception as exc:                           # noqa: BLE001
                results.append({"market": slug, "offset_ticks": int(off),
                                "status": "MEASURE_FAILED",
                                "error": type(exc).__name__})
                continue
            m["status"] = ("HYPOTHETICAL_SHARE_ON_OBSERVED_PROGRAMME_LADDER")
            m["earned"] = 0.0
            results.append(m)
    out.update(ok=True, snapshots_per_market={k: len(v)
                                             for k, v in snaps.items()},
               snapshot_cap=MAX_SNAPSHOTS_PER_MARKET, results=results)
    out["earned_rewards_usd"] = 0.0
    out["hypothetical_share_note"] = (
        "reward_gross_usd is what the CAPTURED terms would have paid for "
        "the modelled share at the depth we observed. It is separate from "
        "earned_rewards_usd, which is zero and has always been zero")
    return out
