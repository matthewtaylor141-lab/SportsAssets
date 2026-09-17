#!/usr/bin/env python3
"""THE SUBSTANTIVE PUBLIC-BOOK CAPTURE. GET only. No credential. No order path.

WHAT THIS RUN IS FOR. One trustworthy prospective dataset characterising
executable public book state on a predeclared roster: spreads, top-of-book and
ladder depth, BBO and mid changes, price improvements, depth changes,
move-through events, quote persistence at sampled observations, event identity,
within-event market correlation, revisit cadence, and whatever trade evidence
the public feed actually carries.

WHAT IT IS NOT FOR, and no field here can be read as either: it does not prove
profitability and it cannot identify a BETTOR fill. A public snapshot shows the
book, not our order in it.

THE RATE IS THE CONFIRMED ONE AND IS NOT RAISED. 0.25 rps, 4.0 s between
requests, enforced by the same GlobalPacer the confirmation ran on. The roster
is six markets, so each market is revisited about every 24 s. Over 90 minutes
that is ~1,350 requests and ~225 observations per market. The scheduler is the
frozen fair rotation with an advancing start index, so no market is starved and
none is systematically sampled first.

THE ROSTER IS READ FROM A FILE FROZEN BEFORE THE FIRST SAMPLED GET. Selection
happens in `substantive_select`, is written to disk, and is re-read here. This
module cannot add, drop or reorder a market, so a roster cannot be revised
after seeing how the markets behaved.

IDENTITY TRAVELS ON EVERY ROW. The frozen identity block for each slug is
passed into `collect.tick_capture` and copied onto every tick. The previous
public capture reconstructed identity downstream and lost it.
"""
import argparse
import json
import time
from pathlib import Path

import collect as C
import rate_pilot as RP

NOT_IDENTIFIED = "NOT_IDENTIFIED"

RATE_RPS = "0.25"
REQUEST_INTERVAL_S = 4.0
CAPTURE_MINUTES = 90
CAPTURE_SECONDS = CAPTURE_MINUTES * 60
EVENTS = 3
MARKETS_PER_EVENT = 2
MARKETS = EVENTS * MARKETS_PER_EVENT
EXPECTED_REQUESTS = 1350
EXPECTED_OBS_PER_MARKET = 225
NOMINAL_REVISIT_S = REQUEST_INTERVAL_S * MARKETS          # 24.0

RATE_IS_THE_CONFIRMED_ONE = "0.25_RPS_RESEARCH_COLLECTOR_OPERATIONAL_VALIDATION"
RATE_MAY_NOT_BE_RAISED = (
    "0.25 rps is the only rate this programme has operationally validated; a "
    "faster one has never been tested and is not tested here")
METHOD = "GET"
CREDENTIAL = "NONE"
ORDER_PATH_EXISTS = False
MIRROR_LIVE = False

THIS_IS = "PROSPECTIVE_PUBLIC_BOOK_STATE_CHARACTERIZATION"
THIS_IS_NOT = ("BETTOR_FILL_IDENTIFICATION", "PROFITABILITY_EVIDENCE",
               "REALIZED_MAKER_ECONOMICS", "MICRO_LIVE_AUTHORIZATION")
PUBLIC_TICK_FILL_IDENTIFICATION = "NO"
ACTUAL_BETTOR_FILL_RATE = NOT_IDENTIFIED
REALIZED_MAKER_ECONOMICS = "NOT_ESTABLISHED"


def rounds_for(seconds=CAPTURE_SECONDS, markets=MARKETS,
               interval_s=REQUEST_INTERVAL_S):
    """Passes that fit in the window. The wall-clock deadline still governs.

    Rounds are an upper bound so the loop cannot run past the declaration if
    the venue answers faster than the pacer's floor; the deadline is what
    actually ends the capture.
    """
    per_round = markets * interval_s
    return max(1, int(seconds // per_round) + 1)


def plan(selection, seconds=CAPTURE_SECONDS):
    """The declared design, written before the first sampled GET."""
    slugs = list(selection.get("MARKET_SLUGS") or [])
    return {
        "THIS_IS": THIS_IS,
        "THIS_IS_NOT": list(THIS_IS_NOT),
        "RATE_RPS": RATE_RPS,
        "REQUEST_INTERVAL_S": REQUEST_INTERVAL_S,
        "RATE_IS_THE_CONFIRMED_ONE": RATE_IS_THE_CONFIRMED_ONE,
        "RATE_MAY_NOT_BE_RAISED": RATE_MAY_NOT_BE_RAISED,
        "CAPTURE_MINUTES": CAPTURE_MINUTES,
        "CAPTURE_SECONDS": seconds,
        "EVENTS_TARGET": EVENTS,
        "MARKETS_PER_EVENT": MARKETS_PER_EVENT,
        "MARKETS_TARGET": MARKETS,
        "MARKETS_SELECTED": len(slugs),
        "MARKET_SLUGS": slugs,
        "EXPECTED_REQUESTS": EXPECTED_REQUESTS,
        "EXPECTED_OBSERVATIONS_PER_MARKET": EXPECTED_OBS_PER_MARKET,
        "NOMINAL_REVISIT_S": NOMINAL_REVISIT_S,
        "ROUNDS_UPPER_BOUND": rounds_for(seconds, max(1, len(slugs))),
        "SCHEDULER": "FAIR_ROTATION_ADVANCING_START_INDEX",
        "SCHEDULER_IS_THE_VALIDATED_ONE": True,
        "EVENT_SELECTION_FROZEN": selection.get("EVENT_SELECTION_FROZEN"),
        "ROSTER_READ_FROM_A_FROZEN_FILE": True,
        "IDENTITY_TRAVELS_WITH_EVERY_ROW": C.IDENTITY_TRAVELS_WITH_THE_ROW,
        "METHOD": METHOD,
        "CREDENTIAL": CREDENTIAL,
        "ORDER_PATH_EXISTS": "NO" if not ORDER_PATH_EXISTS else "YES",
        "mirror_live": MIRROR_LIVE,
        "PUBLIC_TICK_FILL_IDENTIFICATION": PUBLIC_TICK_FILL_IDENTIFICATION,
        "ACTUAL_BETTOR_FILL_RATE": ACTUAL_BETTOR_FILL_RATE,
        "REALIZED_MAKER_ECONOMICS": REALIZED_MAKER_ECONOMICS,
    }


def may_capture(selection):
    """The roster gate. A short roster is a WAIT, never a substitution."""
    slugs = list(selection.get("MARKET_SLUGS") or [])
    ok = (selection.get("EVENT_SELECTION_FROZEN") == "YES"
          and selection.get("CAPTURE_MAY_START") == "YES"
          and len(slugs) == MARKETS)
    return {
        "CAPTURE_MAY_START": "YES" if ok else "NO",
        "WHY_NOT": (None if ok else
                    "the frozen roster is %d markets, not %d; the capture "
                    "waits rather than substituting a stale future"
                    % (len(slugs), MARKETS)),
        "MARKETS_SELECTED": len(slugs),
        "MARKETS_REQUIRED": MARKETS,
    }


def capture(outdir, selection, http, seconds=CAPTURE_SECONDS, pacer=None):
    """Run the declared capture. Contacts the venue; writes rows as they land."""
    gate = may_capture(selection)
    if gate["CAPTURE_MAY_START"] != "YES":
        raise SystemExit("CAPTURE_MAY_START = NO: %s" % gate["WHY_NOT"])

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    slugs = list(selection["MARKET_SLUGS"])
    identity_of = dict(selection.get("IDENTITY_OF") or {})
    pacer = pacer or RP.GlobalPacer(float(RATE_RPS))

    p = plan(selection, seconds)
    (out / "capture_plan.json").write_text(json.dumps(p, indent=1,
                                                      sort_keys=True,
                                                      default=str))
    (out / "selection.json").write_text(json.dumps(selection, indent=1,
                                                   sort_keys=True,
                                                   default=str))
    started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    res = C.tick_capture(out, slugs, pacer, http,
                         rounds=p["ROUNDS_UPPER_BOUND"],
                         identity_of=identity_of, deadline_s=seconds)
    ended = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    res.update(p)
    res.update({
        "FIRST_VENUE_GET_TIME": started,
        "LAST_VENUE_GET_TIME": ended,
        "VENUE_REQUESTS": res.get("TICK_ROWS", 0) + res.get("TICK_ERRORS", 0),
        "OBSERVATIONS_PER_MARKET_NOMINAL": (
            res.get("ROUNDS_COMPLETED", 0)),
    })
    (out / "capture_result.json").write_text(json.dumps(res, indent=1,
                                                        sort_keys=True,
                                                        default=str))
    return res


def render(res):
    keys = ("THIS_IS", "RATE_RPS", "REQUEST_INTERVAL_S", "CAPTURE_MINUTES",
            "MARKETS_SELECTED", "ROUNDS_COMPLETED", "STOPPED_ON", "TICK_ROWS",
            "TICK_ERRORS", "VENUE_REQUESTS", "DURATION_S",
            "FIRST_VENUE_GET_TIME", "LAST_VENUE_GET_TIME",
            "EVENT_SELECTION_FROZEN", "IDENTITY_TRAVELS_WITH_EVERY_ROW",
            "PUBLIC_TICK_FILL_IDENTIFICATION", "ACTUAL_BETTOR_FILL_RATE",
            "REALIZED_MAKER_ECONOMICS", "mirror_live")
    return "\n".join("%-42s = %s" % (k, res.get(k, NOT_IDENTIFIED))
                     for k in keys)


def _cli():                                                   # pragma: no cover
    import httpx
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=CAPTURE_SECONDS)
    a = ap.parse_args()
    sel = json.loads(Path(a.selection).read_text())
    with httpx.Client(headers={"accept": "application/json"}) as http:
        res = capture(a.out, sel, http, a.seconds)
    print(render(res))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
