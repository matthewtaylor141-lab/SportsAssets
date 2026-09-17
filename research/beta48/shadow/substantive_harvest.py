#!/usr/bin/env python3
"""HARVEST THE SUBSTANTIVE CAPTURE. Offline. Contacts nothing.

IDENTITY COMES OFF THE ROWS, NOT OFF A BOARD FILE. `book_metrics` already knows
how to weight market-wise and event-wise; what it needs is a slug -> event map,
and the previous capture built that afterwards from a separate board snapshot.
When the reconstruction missed, the rows could no longer say which contest they
belonged to and the event-weighted figures quietly became market-weighted ones
over whatever subset had survived. Here the map is read from the rows'
own frozen `EVENT_ID`, so it cannot go missing between capture and analysis,
and `EVENT_IDENTITY_COVERAGE` reports how many rows carried one.

WHAT THIS REFUSES TO PRODUCE. No fill rate, no realized spread, no expectancy.
The execution hierarchy is not collapsed anywhere in this file:

    TOUCH  !=  TRADE_EVIDENCE  !=  COUNTERFACTUAL_FILL  !=  ACTUAL_BETTOR_FILL

An unknown fill status stays UNKNOWN. It does not become NOT_FILLED, which
would be a claim, and it does not become FILLED, which would be a fabrication.
"""
import argparse
import json
from pathlib import Path

import book_metrics as BM
import tick_semantics as TS

NOT_IDENTIFIED = "NOT_IDENTIFIED"

EXECUTION_EVIDENCE_HIERARCHY = ("TOUCH", "TRADE_EVIDENCE",
                                "COUNTERFACTUAL_FILL", "ACTUAL_BETTOR_FILL")
HIERARCHY_IS_NOT_COLLAPSED = True
TOUCH_IS_NOT_A_FILL = (
    "our quote being at the touch says the price existed, not that anybody "
    "traded against us there")
VOLUME_CHANGE_IS_NOT_OUR_FILL = (
    "a change in cumulative shares is evidence somebody traded; it is not "
    "evidence that WE did")
UNKNOWN_IS_NOT_NOT_FILLED = (
    "UNKNOWN is the absence of an observation; NOT_FILLED would be an "
    "observation we never made")

PUBLIC_TICK_FILL_IDENTIFICATION = "NO"
ACTUAL_BETTOR_FILL_RATE = NOT_IDENTIFIED
REALIZED_MAKER_ECONOMICS = "NOT_ESTABLISHED"
COUNTERFACTUAL_FILL_IDENTIFICATION_STATUS = "NOT_ATTEMPTED_IN_THIS_HARVEST"

SUPPORT_MIN_OBS_PER_MARKET = 200
SUPPORT_MIN_MARKETS = 6
SUPPORT_MIN_EVENTS = 3


def read_rows(path):
    rows, errors = [], []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        (errors if r.get("kind") == "TICK_ERROR" else rows).append(r)
    return rows, errors


def event_map(rows):
    """slug -> EVENT_ID, taken from the rows' own frozen identity."""
    out = {}
    for r in rows:
        eid = r.get("EVENT_ID", NOT_IDENTIFIED)
        slug = r.get("MARKET_SLUG") or r.get("slug")
        if eid and eid != NOT_IDENTIFIED and slug:
            out[slug] = eid
    return out


def identity_coverage(rows):
    """How much of the capture can name its own contest, field by field."""
    total = len(rows)
    if not total:
        return {"ROWS": 0, "EVENT_IDENTITY_COVERAGE": NOT_IDENTIFIED,
                "EVENT_IDENTITY_VALID": "NO",
                "WHY": "no rows"}
    per_field = {}
    for f in ("EVENT_ID", "MARKET_ID", "MARKET_SLUG", "GAME_START", "SPORT",
              "LEAGUE", "MARKET_SIDES", "REQUEST_UTC", "RECEIPT_UTC",
              "SAMPLING_SEQUENCE"):
        have = sum(1 for r in rows
                   if r.get(f) not in (None, NOT_IDENTIFIED, ""))
        per_field[f] = {"PRESENT": have, "SHARE": have / float(total)}
    eid = per_field["EVENT_ID"]["PRESENT"]
    slugs = {r.get("MARKET_SLUG") or r.get("slug") for r in rows}
    evs = {r.get("EVENT_ID") for r in rows
           if r.get("EVENT_ID") not in (None, NOT_IDENTIFIED)}
    return {
        "ROWS": total,
        "PER_FIELD": per_field,
        "EVENT_IDENTITY_COVERAGE": eid / float(total),
        "ROWS_WITH_EVENT_IDENTITY": eid,
        "ROWS_WITHOUT_EVENT_IDENTITY": total - eid,
        "DISTINCT_MARKETS": len(slugs),
        "DISTINCT_EVENTS": len(evs),
        "MARKETS_PER_EVENT": (len(slugs) / float(len(evs)) if evs
                              else NOT_IDENTIFIED),
        "EVENT_IDENTITY_VALID": "YES" if eid == total and evs else "NO",
        "IDENTITY_READ_FROM": "THE_ROWS_OWN_FROZEN_FIELDS",
        "NOT_RECONSTRUCTED_FROM_A_BOARD_FILE": True,
        "NO_TITLE_HEURISTICS": True,
    }


def support(rows, errors, plan=None):
    """Did the run collect what it declared it would?"""
    by_slug = {}
    for r in rows:
        by_slug.setdefault(r.get("MARKET_SLUG") or r.get("slug"), 0)
        by_slug[r.get("MARKET_SLUG") or r.get("slug")] += 1
    evs = {r.get("EVENT_ID") for r in rows
           if r.get("EVENT_ID") not in (None, NOT_IDENTIFIED)}
    obs = sorted(by_slug.values())
    enough = (len(by_slug) >= SUPPORT_MIN_MARKETS
              and len(evs) >= SUPPORT_MIN_EVENTS
              and bool(obs) and obs[0] >= SUPPORT_MIN_OBS_PER_MARKET)
    return {
        "OBSERVATIONS": len(rows),
        "OBSERVATION_ERRORS": len(errors),
        "MARKETS": len(by_slug),
        "EVENTS": len(evs),
        "OBSERVATIONS_PER_MARKET": by_slug,
        "MIN_OBSERVATIONS_PER_MARKET": obs[0] if obs else 0,
        "MAX_OBSERVATIONS_PER_MARKET": obs[-1] if obs else 0,
        "FLOORS": {"OBS_PER_MARKET": SUPPORT_MIN_OBS_PER_MARKET,
                   "MARKETS": SUPPORT_MIN_MARKETS,
                   "EVENTS": SUPPORT_MIN_EVENTS},
        "SAMPLING_SUPPORT_SUFFICIENT": "YES" if enough else "NO",
        "PLANNED": (plan or {}).get("EXPECTED_REQUESTS", NOT_IDENTIFIED),
    }


def missingness(rows, errors):
    """What was not observed, named rather than dropped."""
    by_status = {}
    for e in errors:
        k = str(e.get("status") or e.get("error") or "UNKNOWN")
        by_status[k] = by_status.get(k, 0) + 1
    no_bid = sum(1 for r in rows if r.get("BID") == NOT_IDENTIFIED)
    no_ask = sum(1 for r in rows if r.get("ASK") == NOT_IDENTIFIED)
    return {
        "FAILED_READS": len(errors),
        "FAILED_READS_BY_STATUS": by_status,
        "ROWS_WITH_NO_BID": no_bid,
        "ROWS_WITH_NO_ASK": no_ask,
        "ONE_SIDED_IS_NOT_ZERO_DEPTH": (
            "a missing side is an unobserved side, not a side with no size"),
        "CENSORING_AT_THE_WINDOW_EDGES": (
            "a run in progress when the capture stopped is right-censored; "
            "its observed span is a lower bound on its true span"),
        "TRUNCATED_RUNS_ARE_NOT_DROPPED": True,
    }


def execution_evidence(rows):
    """Touch, trade evidence, and everything the public feed cannot settle."""
    touches = sum(1 for r in rows
                  if r.get("BID") != NOT_IDENTIFIED
                  or r.get("ASK") != NOT_IDENTIFIED)
    trade_rows = [r for r in rows if r.get("TRADE_OCCURRED") is True]
    unknown = sum(1 for r in rows
                  if r.get("TRADE_OCCURRED") == NOT_IDENTIFIED)
    diag = TS.diagnose(rows)
    return {
        "EXECUTION_EVIDENCE_HIERARCHY": list(EXECUTION_EVIDENCE_HIERARCHY),
        "HIERARCHY_IS_NOT_COLLAPSED": HIERARCHY_IS_NOT_COLLAPSED,
        "TOUCH_OBSERVATIONS": touches,
        "TOUCH_IS_NOT_A_FILL": TOUCH_IS_NOT_A_FILL,
        "TRADE_EVIDENCE_OBSERVATIONS": len(trade_rows),
        "TRADE_EVIDENCE_IS": "SOMEBODY_TRADED_NOT_THAT_WE_DID",
        "VOLUME_CHANGE_IS_NOT_OUR_FILL": VOLUME_CHANGE_IS_NOT_OUR_FILL,
        "TRADE_STATUS_UNKNOWN_OBSERVATIONS": unknown,
        "UNKNOWN_IS_NOT_NOT_FILLED": UNKNOWN_IS_NOT_NOT_FILLED,
        "SHARES_TRADED_EXECUTION_SEMANTICS": diag.get(
            "SHARES_TRADED_EXECUTION_SEMANTICS", NOT_IDENTIFIED),
        "SHARES_TRADED_DIAGNOSTIC_ONLY": True,
        "COUNTERFACTUAL_FILL_IDENTIFICATION_STATUS":
            COUNTERFACTUAL_FILL_IDENTIFICATION_STATUS,
        "PUBLIC_TICK_FILL_IDENTIFICATION": PUBLIC_TICK_FILL_IDENTIFICATION,
        "ACTUAL_BETTOR_FILL_RATE": ACTUAL_BETTOR_FILL_RATE,
        "REALIZED_MAKER_ECONOMICS": REALIZED_MAKER_ECONOMICS,
    }


def harvest(outdir):
    d = Path(outdir)
    rows, errors = read_rows(d / "ticks.jsonl")
    plan = _maybe(d / "capture_plan.json")
    sel = _maybe(d / "selection.json")
    ev_of = event_map(rows)
    cov = identity_coverage(rows)
    sup = support(rows, errors, plan)
    metrics = BM.book_metrics(rows, event_of=ev_of)
    ex = execution_evidence(rows)

    valid = (cov["EVENT_IDENTITY_VALID"] == "YES"
             and sup["SAMPLING_SUPPORT_SUFFICIENT"] == "YES")
    return {
        "CAPTURE_PLAN": plan,
        "EVENT_SELECTION": {k: sel.get(k) for k in (
            "SELECTION_STATUS", "EVENT_SELECTION_FROZEN", "FROZEN_AT",
            "EVENT_IDS", "EVENT_NAMES", "GAME_STARTS", "MARKET_IDS",
            "MARKET_SLUGS", "SELECTION_REASON", "EVENTS_QUALIFYING",
            "SELECTION_SALT")} if sel else NOT_IDENTIFIED,
        "EVENT_IDENTITY_COVERAGE": cov,
        "SUPPORT": sup,
        "BOOK_METRICS": metrics,
        "EXECUTION_EVIDENCE": ex,
        "MISSINGNESS": missingness(rows, errors),

        "CAPTURE_PIPELINE_FUNCTIONAL": ("YES" if rows and not
                                        _mostly_errors(rows, errors) else "NO"),
        "MARKET_CHARACTERIZATION_VALID": "YES" if valid else "NO",
        "EVENT_IDENTITY_VALID": cov["EVENT_IDENTITY_VALID"],
        "SAMPLING_SUPPORT_SUFFICIENT": sup["SAMPLING_SUPPORT_SUFFICIENT"],
        "PUBLIC_BOOK_STATE_CHARACTERIZATION": "YES" if valid else "NO",
        "PUBLIC_TICK_FILL_IDENTIFICATION": PUBLIC_TICK_FILL_IDENTIFICATION,
        "ACTUAL_BETTOR_FILL_RATE": ACTUAL_BETTOR_FILL_RATE,
        "REALIZED_MAKER_ECONOMICS": REALIZED_MAKER_ECONOMICS,
        "THESE_THREE_ARE_NOT_FAILURES_OF_THIS_CAPTURE": (
            "PUBLIC_TICK_FILL_IDENTIFICATION", "ACTUAL_BETTOR_FILL_RATE",
            "REALIZED_MAKER_ECONOMICS"),
        "MICRO_LIVE_AUTHORIZED": "NO",
        "mirror_live": False,
    }


def _mostly_errors(rows, errors):
    return len(errors) > len(rows)


def _maybe(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                         # noqa: BLE001
        return {}


def render(h):
    L = []
    for k in ("CAPTURE_PIPELINE_FUNCTIONAL", "MARKET_CHARACTERIZATION_VALID",
              "EVENT_IDENTITY_VALID", "SAMPLING_SUPPORT_SUFFICIENT",
              "PUBLIC_BOOK_STATE_CHARACTERIZATION",
              "PUBLIC_TICK_FILL_IDENTIFICATION", "ACTUAL_BETTOR_FILL_RATE",
              "REALIZED_MAKER_ECONOMICS", "MICRO_LIVE_AUTHORIZED"):
        L.append("%-44s = %s" % (k, h[k]))
    return "\n".join(L)


def _cli():                                                   # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    h = harvest(a.dir)
    Path(a.out).write_text(json.dumps(h, indent=1, sort_keys=True,
                                      default=str))
    print(render(h))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
