"""THE SOFTWARE-REDS REPLAY HARNESS (RC6.3b root-cause audit, lane H0).

THE INSTRUMENT. The approved-judge packet of RC6.3b (release ec8b892d,
pm-acceptance-ec8b892dcf18387601aa668469c10f70c6e05bbc, read 2026-10-10
09:54Z) failed the gate software_reds_zero: SOFTWARE_RED_FIRST_LOSSES_IN_
LAST_HOUR, by_class SOFTWARE 84 / ECONOMIC 14 / EXTERNAL 6 / UNCLASSIFIED 0
over 104 provider events, software_by_code NORMALIZED PINNAPI_PRIMARY_NO_
EXACT_FIXTURE 35, NORMALIZED FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE 1,
FAIR_VALUE QUOTE_STALE_ON_ARRIVAL 38, FAIR_VALUE PROBABILITY_EVIDENCE_STALE
10. The root-cause audit exported the census's INPUTS for that hour from
the production read replica (research-sql 38055963121, SELECT only: the
furthest ext_candidate_outcomes row per provider event, the linked
external_valuations, the paper_decisions on them, for both instants the gate
was read at) -- tests/fixtures/rc63_sw_reds_104_events.json. This module
replays them through coverage_first_loss.census with the taxonomy of the
checked-out source and through the gate's own evidence derivation, so every
claim about the 84 is a number this file reproduces, and every lane that
moves the 84 has an instrument that says by how much.

THE CORRECTED NARRATIVE (replacing two sentences of the audit):

  * NOT "the fix touches none of pinnapi_*/paper_derek". The 84 are written
    by three code paths and two of them are exactly those: 36 by the PinnAPI
    primary / feed read recorded on the collector's ledger
    (pinnapi_primary.R_NO_EXACT = PINNAPI_PRIMARY_NO_EXACT_FIXTURE 35,
    pinnapi_feed.R_NO_CHANGE_TIME = FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE
    1), 38 by the collector's arrival gate (ext_pinnacle_loop.R_QUOTE_STALE_
    ON_ARRIVAL) on the ledger, and 10 by paper decisions (derek_policy.
    R_STALE = PROBABILITY_EVIDENCE_STALE, written by DEREK_ENTRY_POLICY_V2,
    PINNACLE_EXPLORATION_PAPER and PINNACLE_COMPLETED_GAME_PAPER). FEED-1
    edits pinnapi_*; PAPER-1 edits paper_derek and the paper pass.
  * NOT "at most 39" (84 - 10 - 35). The floor pure code reaches WITHOUT
    COLL-1 is 42 to 66: PAPER-1 is -10 and certain on this hour (every one
    of the 10 also reached EV, G6); FEED-1 is -8 measured (MLS only, the
    21:07Z natural experiment) and at most -32 (the 32 NO_EXACT events 96 h
    or more from their start; the 3 inside 96 h likely stay); the 38
    QUOTE_STALE events are COLL-1's and are not counted; software_reds_zero
    is not reachable by code alone (owner floor: AGE_UNKNOWN 1 and the 33
    quiet-line stale rows under C1, the stale residual under C2, the census
    semantics of the 10 under C3).

THE GUARDS. UNCLASSIFIED == 0 on the packet hour and on every row of the
hourly readback; no RC6.3b code (PMUS_ROUTE_BOOK_*, KALSHI_SHADOW_*, the
capability-review codes) is a first loss in the hour; the Xavier entry codes
classify as the one taxonomy says (ECONOMIC for no protective price, SOFTWARE
at ENTER_PASS for a held read that cannot price); the earliest stage wins,
so the "second wave" the critic warns of -- SOFTWARE codes further down the
chain surfacing once an earlier one is removed -- is measured here, not
guessed: 13 XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT decisions sit in
the hour, every one masked by an earlier-stage code, and none of the 10
PAPER-1 events falls to one when its stale decision goes.

THE GATE'S ORDER IS PART OF ITS EVIDENCE. first_loss_of_event takes an
event's earliest-stage decision and, between two decisions at that stage,
the first in record order -- DECISIONS_SQL's ORDER BY decided_at. The
statement does not select decided_at, so the export carries it
(decided_at_epoch) and the row's decision_id beside the statement's own
columns, the replay sorts a valuation's decisions by them (an export
without them is refused), and every cell an exact decided_at tie leaves to
the gate's own read order is named per row, not resolved silently. The
first export of the hourly series (research-sql 38069842557) ordered a
valuation's decisions by strategy instead: its SOFTWARE totals, by_class
and UNCLASSIFIED were order-invariant on every row, its per-code split was
not on 14 of the 24 (the re-export in the gate's order, research-sql
38074718533, moved them; one code, NO_QUALIFIED_PINNACLE_PROBABILITY,
leaves the table and NO_QUALIFIED_MODEL enters it). The packet-hour
fixture was captured without
the order columns; it carries no such tie, so its replay is exact in any
order, which is proved here rather than assumed.

THE PER-LANE EXPECTATIONS are xfail until each lane lands and captures its
readback hour with research/rc63_sw_reds_census.sql; the capture file's
presence flips the case (strict: a passing expectation must be unmarked).

Read-only against coverage_first_loss, refusal_taxonomy_table and
bettor_external_shadow. No threshold, gate or rule is read or moved. ALL
DATA IS THE RECORDED PRODUCTION CAPTURE OR SYNTHETIC; nothing is written.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import sys

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import coverage_first_loss as FL
from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.capital_readiness import feeds as FEEDS

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND / "tools"))
import sw_reds_hourly_census as T  # noqa: E402

FIX = BACKEND / "tests" / "fixtures" / "rc63_sw_reds_104_events.json"
SQL = REPO / "research" / "rc63_sw_reds_census.sql"
HOURLY = REPO / "research" / "rc63_sw_reds_hourly_census.json"
#: the lanes' readback captures (the hourly table of the hour after each
#: lane landed, `sw_reds_hourly_census.py --log <run log> --out-json`);
#: absent until the lane lands
CAPTURES = {
    "PAPER-1": BACKEND / "tests" / "fixtures" /
    "rc63_sw_reds_hourly_after_paper1.json",
    "FEED-1": BACKEND / "tests" / "fixtures" /
    "rc63_sw_reds_hourly_after_feed1.json",
}

PACKET_NOW = 1791626074.8284767           # completion.json computed_at
PACKET_NOW_B = 1791626141.534181          # capital_readiness.json computed_at
#: the gate's evidence in the packet, verbatim (completion.json
#: data.gate_evidence.software_reds_zero)
PACKET_GATE = {
    "value": False,
    "reason": "SOFTWARE_RED_FIRST_LOSSES_IN_LAST_HOUR",
    "evidence": {
        "software": 84,
        "by_class": {"SOFTWARE": 84, "ECONOMIC": 14, "EXTERNAL": 6,
                     "UNCLASSIFIED": 0},
        "window_s": 3600.0,
        "software_by_code": [
            {"stage": "NORMALIZED", "code": "PINNAPI_PRIMARY_NO_EXACT_FIXTURE",
             "events": 35},
            {"stage": "NORMALIZED",
             "code": "FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE", "events": 1},
            {"stage": "FAIR_VALUE", "code": "QUOTE_STALE_ON_ARRIVAL",
             "events": 38},
            {"stage": "FAIR_VALUE", "code": "PROBABILITY_EVIDENCE_STALE",
             "events": 10}],
        "software_codes_truncated": False,
        "source": "coverage_first_loss.read (1 h)"}}
#: RC6.3b workers live (deploys_sportsassets-workers.json finishedAt); the
#: workers write the ledger and the paper decisions
RC63B_WORKERS_LIVE = dt.datetime(2026, 10, 10, 3, 52, 40, 73058,
                                 tzinfo=dt.timezone.utc).timestamp()
#: the codes RC6.3b's lanes added that must never be a first loss here
RC63B_NEW_PREFIXES = ("PMUS_ROUTE_BOOK_", "KALSHI_SHADOW_")
CAPABILITY_REVIEW_CODES = frozenset(
    c for c, (_cls, _fam, stage) in TT.TABLE.items()
    if stage == "OUT_OF_FUNNEL" and ("REVIEW" in c))

STALE = "PROBABILITY_EVIDENCE_STALE"
NO_EXACT = "PINNAPI_PRIMARY_NO_EXACT_FIXTURE"
NOT_YET_POSTED = "PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED"
AGE_UNKNOWN = "FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE"
OLDER = "FEED_QUOTE_OLDER_THAN_LIMIT"
QUOTE_STALE = "QUOTE_STALE_ON_ARRIVAL"
XAVIER_CANNOT_PRICE = "XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT"
XAVIER_CANNOT_PROTECT = "XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE"


def _doc():
    return json.loads(FIX.read_text())


def _inputs(window="A"):
    return T.window_inputs(_doc()["windows"][window])


def _by_code(got, cls=None):
    return [(r["stage"], r["code"], r["events"]) for r in
            got["totals"]["by_code"] if cls is None or r["class"] == cls]


def _reconciles(agg):
    lost = sum(v for v in agg["first_loss"].values() if v)
    return lost + (agg["entered"] or 0) + agg["unavailable"] == \
        agg["provider_events"]


def _first_losses(events, vals, decs):
    """{provider_event_id: first loss} the way census links the records."""
    by_key, by_slug, dec_by_val = {}, {}, {}
    for v in vals:
        by_key.setdefault(str(v["event_key"]), []).append(v)
        if v.get("us_market_slug"):
            by_slug.setdefault(str(v["us_market_slug"]), []).append(v)
    for d in decs:
        dec_by_val.setdefault(d["valuation_id"], []).append(d)
    out = {}
    for ev in events:
        seen = {v["id"]: v for v in by_key.get(str(ev["provider_event_id"]),
                                               ())}
        for s in list(ev.get("slugs") or []) + [ev.get("us_market_slug")]:
            for v in by_slug.get(str(s), ()) if s else ():
                seen.setdefault(v["id"], v)
        vs = list(seen.values())
        ds = [d for v in vs for d in dec_by_val.get(v["id"], ())]
        out[ev["provider_event_id"]] = (
            FL.first_loss_of_event(ev, vs, ds, valuations_read=True,
                                   decisions_read=True), ev, vs, ds)
    return out


def _hours_to_start(ev) -> float:
    start = dt.datetime.fromisoformat(
        ev["commence_time"].replace("Z", "+00:00")).timestamp()
    return (start - float(ev["last_cycle_at"])) / 3600.0


# ═════════════════════════════════════════════════════════════════════
# 1 · THE FIXTURE IS THE PACKET HOUR, AND THE REPLAY REPRODUCES THE GATE
# ═════════════════════════════════════════════════════════════════════

def test_the_fixture_is_the_packet_hour_as_recorded():
    doc = _doc()
    assert "38055963121" in doc["meta"]["captured"]
    assert doc["meta"]["release_running"].startswith("ec8b892d")
    assert doc["meta"]["windows_identical"] is True
    a, b = doc["windows"]["A"], doc["windows"]["B"]
    assert (a["now"], a["since"], a["until"]) == (
        PACKET_NOW, PACKET_NOW - 3600, PACKET_NOW + 1)
    assert (b["now"], b["since"], b["until"]) == (
        PACKET_NOW_B, PACKET_NOW_B - 3600, PACKET_NOW_B + 1)
    # the two gate reads (completion, capital_readiness) saw the same rows
    strip = lambda evs: json.dumps(evs, sort_keys=True)  # noqa: E731
    assert strip(a["events"]) == strip(b["events"])
    assert len(a["events"]) == 104
    # every record carries exactly the census's columns, nothing else
    for rec in a["events"]:
        assert set(rec) == {
            "sport_key", "provider_event_id", "family", "rows", "reach",
            "stage", "outcome", "first_refusal", "codes", "us_market_slug",
            "slugs", "home", "away", "commence_time", "last_cycle_at",
            "vals", "decs"}, sorted(rec)
        for v in rec["vals"]:
            assert set(v) == {"id", "event_key", "us_market_slug",
                              "record_purpose", "refusals", "admissible"}
        for d in rec["decs"]:
            assert set(d) == {"valuation_id", "verdict", "refusal",
                              "refusals", "strategy"}
    # one collector cycle wrote the furthest row of every event
    assert {e["last_cycle_at"] for e in a["events"]} == {1791625426.276969}
    events, vals, decs = _inputs("A")
    assert (len(events), len(vals), len(decs)) == (104, 42, 126)
    # the hour's competitions (the packet's by_competition)
    import collections
    assert collections.Counter(e["sport_key"] for e in events) == {
        "americanfootball_ncaaf": 62, "soccer_usa_mls": 30,
        "soccer_mexico_ligamx": 10, "baseball_mlb": 2}


@pytest.mark.parametrize("window,now", [("A", PACKET_NOW),
                                        ("B", PACKET_NOW_B)])
def test_the_replay_reproduces_the_packet_gate_evidence_exactly(
        window, now, monkeypatch):
    """Through the production gate itself: gate_software_reds_zero over a
    census read that returns the replay (the read is the only thing
    replaced; the derivation of the evidence is the gate's own)."""
    events, vals, decs = _inputs(window)
    seen = {}

    async def read(conn, *, since, until):
        seen.update(since=since, until=until)
        return dict(status="OK", why=None, **FL.census(events, vals, decs))

    monkeypatch.setattr(FL, "read", read)
    got = asyncio.run(FEEDS.gate_software_reds_zero(None, {"now": now}))
    assert seen == {"since": now - FEEDS.FIRST_LOSS_WINDOW_S,
                    "until": now + 1.0}
    assert got == PACKET_GATE, json.dumps(got, indent=1)
    # and the tool's own derivation is the gate's
    assert T.gate_evidence(FL.census(events, vals, decs)) == \
        PACKET_GATE["evidence"]


def test_the_census_reconciles_and_the_other_classes_are_the_packets():
    """by_class is the packet's. The gate names no ECONOMIC code; the split
    of the 14 ECONOMIC by code below is the capture's decision order (9 of
    the 14 are a tie at EV with another ECONOMIC code, named by
    order_ambiguity in the invariance test), the EXTERNAL rows are not
    order-dependent (ledger rows, one code each)."""
    events, vals, decs = _inputs()
    got = FL.census(events, vals, decs)
    t = got["totals"]
    assert t["provider_events"] == 104
    assert t["entered"] == 0 and t["unavailable"] == 0
    assert _reconciles(t)
    for agg in got["by_competition"].values():
        assert _reconciles(agg)
    assert _by_code(got, "ECONOMIC") == [("EV", "BELOW_MIN_GROSS_EDGE", 14)]
    assert sum(n for _s, _c, n in _by_code(got, "ECONOMIC")) == 14
    assert {a["stage"] for a in T.order_ambiguity(events, vals, decs)} == {
        "EV"}
    for f, ev, _vs, _ds in _first_losses(events, vals, decs).values():
        if f["class"] == FL.EXTERNAL:
            assert f["source"] == "ext_candidate_outcomes"
    assert _by_code(got, "EXTERNAL") == [
        ("NORMALIZED", "PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE",
         3),
        ("MAPPED", "NO_VENUE_CONTRACT_FOR_EVENT", 3)]
    assert _by_code(got, "UNCLASSIFIED") == []
    assert got["valuations_linked"] == {"by_event_key": 23,
                                        "by_venue_contract": 1}
    assert t["first_loss"] == {
        "PROVIDER": 0, "NORMALIZED": 39, "MAPPED": 3, "SETTLEMENT": 0,
        "MODEL": 0, "FAIR_VALUE": 48, "BOOK": 0, "EV": 14, "ENTER_PASS": 0}
    assert t["by_class"] == PACKET_GATE["evidence"]["by_class"]


def test_every_code_the_hour_recorded_is_classified_by_the_two_tables():
    """UNCLASSIFIED == 0 is a guard, not a coincidence: every code in the
    hour -- ledger codes, valuation refusals, decision refusals, not only
    the first losses -- is known to the taxonomy or the evaluability table.
    """
    events, vals, decs = _inputs()
    codes = set()
    for ev in events:
        codes.update(ev["codes"] or [])
        if ev["first_refusal"]:
            codes.add(ev["first_refusal"])
    for v in vals:
        codes.update(v["refusals"] or [])
    for d in decs:
        codes.update(d["refusals"] or [])
        if d["refusal"]:
            codes.add(d["refusal"])
    assert len(codes) == 25
    for c in sorted(codes):
        k = FL.classify(c)
        assert k["class"] in (RT.SOFTWARE, RT.ECONOMIC, FL.EXTERNAL), (c, k)
        assert RT.lookup(c) is not None, c


# ═════════════════════════════════════════════════════════════════════
# 2 · THE CORRECTED NARRATIVE, AS NUMBERS
# ═════════════════════════════════════════════════════════════════════

def test_the_eighty_four_are_written_by_three_paths_two_of_them_pinnapi_and_paper():  # noqa: E501
    """Replaces "touches none of pinnapi_*/paper_derek"."""
    from sportsassets import pinnapi_feed, pinnapi_primary
    from sportsassets.agents import derek_policy
    from sportsassets.workers import ext_pinnacle_loop as L
    assert pinnapi_primary.R_NO_EXACT == NO_EXACT
    assert pinnapi_feed.R_NO_CHANGE_TIME == AGE_UNKNOWN
    assert L.R_QUOTE_STALE_ON_ARRIVAL == QUOTE_STALE
    assert derek_policy.R_STALE == STALE
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    sw = [(f, ev, ds) for f, ev, _vs, ds in fl.values()
          if f["class"] == RT.SOFTWARE]
    assert len(sw) == 84
    by_source = {}
    for f, _ev, _ds in sw:
        by_source.setdefault(f["source"], {}).setdefault(f["code"], 0)
        by_source[f["source"]][f["code"]] += 1
    assert by_source == {
        "ext_candidate_outcomes": {NO_EXACT: 35, AGE_UNKNOWN: 1,
                                   QUOTE_STALE: 38},
        "paper_decisions": {STALE: 10}}
    # the 10 are paper decisions of the three strategies, Derek among them
    strategies = {d["strategy"] for f, _ev, ds in sw if f["code"] == STALE
                  for d in ds if STALE in (d["refusals"] or [])}
    assert strategies == {"DEREK_ENTRY_POLICY_V2", "PINNACLE_EXPLORATION_PAPER",
                          "PINNACLE_COMPLETED_GAME_PAPER"}
    # the two feed codes are the ledger row's FIRST refusal, written by the
    # PinnAPI read before any venue contract (no slug): the naming gap at
    # the lane's probability stage, the age gap at its freshness stage
    for f, ev, _ds in sw:
        if f["code"] in (NO_EXACT, AGE_UNKNOWN):
            assert ev["first_refusal"] == f["code"]
            assert not ev["us_market_slug"] and ev["reach"] < 4
            assert ev["stage"] == ("1_PROBABILITY" if f["code"] == NO_EXACT
                                   else "2_FRESHNESS")
            assert ext.ledger_stage_of(f["code"]) == ev["stage"]
        if f["code"] == QUOTE_STALE:
            assert ev["first_refusal"] == QUOTE_STALE
            assert ev["stage"] == "2_FRESHNESS" and ev["us_market_slug"]


def test_the_quiet_line_and_the_five_no_exact_beside_the_stale_are_as_counted():  # noqa: E501
    """C1's 34 (33 QUOTE_STALE with the feed's age code beside + the one
    AGE_UNKNOWN-only event) and COLL-1's 5 that also need FEED-1."""
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    stale = [ev for f, ev, _vs, _ds in fl.values() if f["code"] == QUOTE_STALE]
    assert len(stale) == 38
    import collections
    beside = collections.Counter(
        tuple(c for c in ev["codes"] if c != QUOTE_STALE) for ev in stale)
    assert beside == {(AGE_UNKNOWN,): 24, (OLDER,): 9, (NO_EXACT,): 5}
    assert 24 + 9 + 1 == 34


def test_the_corrected_floor_is_42_to_66_not_at_most_39():
    """Replaces "at most 39"."""
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    no_exact = [ev for f, ev, _vs, _ds in fl.values() if f["code"] == NO_EXACT]
    assert len(no_exact) == 35
    inside = [ev for ev in no_exact if _hours_to_start(ev) < 96]
    beyond = [ev for ev in no_exact if _hours_to_start(ev) >= 96]
    assert (len(inside), len(beyond)) == (3, 32)
    import collections
    assert collections.Counter(ev["sport_key"] for ev in no_exact) == {
        "americanfootball_ncaaf": 16, "soccer_usa_mls": 15,
        "soccer_mexico_ligamx": 3, "baseball_mlb": 1}
    paper1, feed1_measured, feed1_max = 10, 8, len(beyond)
    assert 84 - paper1 - feed1_max == 42
    assert 84 - paper1 - feed1_measured == 66
    # the audit's 39 needs every NO_EXACT event to leave, the 3 inside 96 h
    # included, and counts nothing of COLL-1's 38
    assert 84 - paper1 - len(no_exact) == 39
    assert 39 < 42


# ═════════════════════════════════════════════════════════════════════
# 3 · THE LANES' PROJECTIONS ON THIS HOUR (pass today: arithmetic on the
#     recorded rows) AND THEIR EXPECTATIONS (xfail until each lane lands)
# ═════════════════════════════════════════════════════════════════════

def _without_stale_decisions(decs):
    return [d for d in decs if STALE not in (d.get("refusals") or [])
            and d.get("refusal") != STALE]


def test_paper1_projection_is_minus_ten_software_plus_ten_economic():
    """G6: every one of the 10 PROBABILITY_EVIDENCE_STALE events also
    reached EV by another strategy's decision, so removing the doomed
    decisions (PAPER-1 writes none) moves exactly 10 from SOFTWARE at
    FAIR_VALUE to ECONOMIC at EV, and no other SOFTWARE row changes."""
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    stale = [(ev, ds) for f, ev, _vs, ds in fl.values() if f["code"] == STALE]
    assert len(stale) == 10
    for ev, ds in stale:
        others = _without_stale_decisions(ds)
        assert others, ev["provider_event_id"]
        st, _c = FL._earliest(
            [c for d in others for c in (d["refusals"] or [d["refusal"]])],
            mapped=True, default="ENTER_PASS")
        assert st == "EV", (ev["provider_event_id"], st)
    before = FL.census(events, vals, decs)["totals"]
    after = FL.census(events, vals, _without_stale_decisions(decs))["totals"]
    assert after["by_class"] == {"SOFTWARE": 74, "ECONOMIC": 24,
                                 "EXTERNAL": 6, "UNCLASSIFIED": 0}
    assert _by_code({"totals": after}, "SOFTWARE") == [
        ("NORMALIZED", NO_EXACT, 35), ("NORMALIZED", AGE_UNKNOWN, 1),
        ("FAIR_VALUE", QUOTE_STALE, 38)]
    assert _by_code({"totals": after}, "ECONOMIC") == [
        ("EV", "BELOW_MIN_GROSS_EDGE", 24)]
    assert before["by_class"]["SOFTWARE"] - after["by_class"]["SOFTWARE"] == 10
    assert _reconciles(after)


def test_the_second_wave_is_latent_in_the_hour_and_measured():
    """The critic: SOFTWARE codes further down the chain surface once an
    earlier one is removed. In this hour 13 XAVIER_HELD_READ_CANNOT_PRICE_
    THIS_CONTRACT decisions (SOFTWARE at ENTER_PASS) sit on 8 events -- 3 of
    the 10 PAPER-1 events and 5 already ECONOMIC at EV -- every one masked
    by an earlier-stage code; none of the 10 falls to one when its stale
    decision goes, because each has an EV-stage decision beside."""
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    xav = [d for d in decs if XAVIER_CANNOT_PRICE in (d["refusals"] or [])]
    assert len(xav) == 13
    assert {d["strategy"] for d in xav} == {"PINNACLE_EXPLORATION_PAPER"}
    carriers = {pid for pid, (_f, _ev, _vs, ds) in fl.items()
                if any(XAVIER_CANNOT_PRICE in (d["refusals"] or [])
                       for d in ds)}
    assert len(carriers) == 8
    classes = {pid: fl[pid][0]["class"] for pid in carriers}
    assert sorted(classes.values()) == ["ECONOMIC"] * 5 + ["SOFTWARE"] * 3
    assert {(fl[pid][0]["stage"], fl[pid][0]["code"]) for pid in carriers} \
        == {("EV", "BELOW_MIN_GROSS_EDGE"), ("FAIR_VALUE", STALE)}
    assert all(fl[pid][0]["code"] != XAVIER_CANNOT_PRICE for pid in carriers)
    # after PAPER-1: the 3 SOFTWARE carriers are ECONOMIC at EV, not
    # SOFTWARE at ENTER_PASS
    after = _first_losses(events, vals, _without_stale_decisions(decs))
    for pid in carriers:
        f = after[pid][0]
        assert (f["stage"], f["class"]) == ("EV", "ECONOMIC"), (pid, f)
    # the latent wave: with the EV decisions gone too, the earliest stage
    # on those events would be the Xavier code, SOFTWARE at ENTER_PASS
    only_xav = [d for d in decs if XAVIER_CANNOT_PRICE in (d["refusals"]
                                                            or [])]
    latent = _first_losses(events, vals, only_xav)
    for pid in carriers:
        f = latent[pid][0]
        assert (f["stage"], f["code"], f["class"]) == (
            "ENTER_PASS", XAVIER_CANNOT_PRICE, "SOFTWARE")


def test_feed1_projection_no_exact_events_leave_software_as_external_or_priced():  # noqa: E501
    """A NO_EXACT event that FEED-1 proves absent at its start is recorded
    PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED (EXTERNAL by the evaluability
    table, at NORMALIZED); one it retains is priced and leaves NORMALIZED
    altogether. Projected on this hour: the 32 events 96 h or more out ->
    EXTERNAL, SOFTWARE 84 -> 52; with PAPER-1, 42."""
    k = FL.classify(NOT_YET_POSTED)
    assert k["class"] == FL.EXTERNAL
    assert "EXTERNAL_DEPENDENCY" in k["evidence"]
    assert FL.chain_stage(NOT_YET_POSTED, lane_stage="1_PROBABILITY") == \
        "NORMALIZED"
    events, vals, decs = _inputs()
    fl = _first_losses(events, vals, decs)
    projected = []
    for ev in events:
        f = fl[ev["provider_event_id"]][0]
        if f["code"] == NO_EXACT and _hours_to_start(ev) >= 96:
            ev = dict(ev, first_refusal=NOT_YET_POSTED,
                      codes=[NOT_YET_POSTED if c == NO_EXACT else c
                             for c in ev["codes"]])
        projected.append(ev)
    got = FL.census(projected, vals, decs)["totals"]
    assert got["by_class"] == {"SOFTWARE": 52, "ECONOMIC": 14,
                               "EXTERNAL": 38, "UNCLASSIFIED": 0}
    assert ("NORMALIZED", NO_EXACT, 3) in _by_code({"totals": got},
                                                   "SOFTWARE")
    assert ("NORMALIZED", NOT_YET_POSTED, 32) in _by_code({"totals": got},
                                                          "EXTERNAL")
    both = FL.census(projected, vals, _without_stale_decisions(decs))
    assert both["totals"]["by_class"]["SOFTWARE"] == 42
    # a retained event prices: it leaves NORMALIZED (the next stage, not a
    # class) -- the ledger row would carry a venue contract and reach 4
    priced = dict(projected[0], first_refusal=QUOTE_STALE,
                  codes=[QUOTE_STALE], stage="2_FRESHNESS", reach=4,
                  us_market_slug="aec-x", slugs=["aec-x"])
    f = FL.first_loss_of_event(priced, [], [], valuations_read=True,
                               decisions_read=True)
    assert f["stage"] != "NORMALIZED"


def _capture_rows(lane):
    doc = json.loads(CAPTURES[lane].read_text())
    rows = doc["rows"]
    assert rows, lane
    return rows


@pytest.mark.xfail(not CAPTURES["PAPER-1"].exists(), strict=True,
                   reason="PAPER-1 (rc63/paper-no-doomed-decisions) has not "
                          "landed: no readback capture at %s"
                          % CAPTURES["PAPER-1"].name)
def test_paper1_expectation_no_doomed_decision_is_a_first_loss():
    """After PAPER-1: no hour has a SOFTWARE first loss at FAIR_VALUE from a
    paper decision (PROBABILITY_EVIDENCE_STALE), and the events that
    reached a decision are ENTERED or judged on their economics (EV /
    ENTER_PASS): ECONOMIC takes what the doomed decisions held (+10 on the
    packet hour's shape)."""
    for row in _capture_rows("PAPER-1"):
        assert row["unclassified"] == 0
        for r in row["by_code"]:
            assert r["code"] != STALE, row["k"]
            if r["source"] == "paper_decisions":
                assert r["stage"] in ("EV", "ENTER_PASS") or \
                    r["class"] != RT.SOFTWARE, (row["k"], r)


@pytest.mark.xfail(not CAPTURES["FEED-1"].exists(), strict=True,
                   reason="FEED-1 (rc63/feed-retention-absence-evidence) has "
                          "not landed: no readback capture at %s"
                          % CAPTURES["FEED-1"].name)
def test_feed1_expectation_no_exact_events_leave_software():
    """After FEED-1: no hour has PINNAPI_PRIMARY_NO_EXACT_FIXTURE as a
    SOFTWARE first loss; the former NO_EXACT events are EXTERNAL
    (PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED, proved absent) or priced
    (past NORMALIZED)."""
    for row in _capture_rows("FEED-1"):
        assert row["unclassified"] == 0
        codes = {(r["code"], r["class"]) for r in row["by_code"]}
        assert (NO_EXACT, RT.SOFTWARE) not in codes, row["k"]
        if (NOT_YET_POSTED, FL.EXTERNAL) in codes:
            (r,) = [r for r in row["by_code"] if r["code"] == NOT_YET_POSTED]
            assert r["stage"] == "NORMALIZED"


# ═════════════════════════════════════════════════════════════════════
# 4 · TAXONOMY GUARDS: THE XAVIER ENTRY CODES AND THE EARLIEST STAGE
# ═════════════════════════════════════════════════════════════════════

def test_the_xavier_entry_codes_classify_as_the_one_taxonomy_says():
    from sportsassets.agents import paper_explore as PE
    assert PE.R_XAVIER_CANNOT_PRICE == XAVIER_CANNOT_PRICE
    assert PE.R_XAVIER_CANNOT_PROTECT == XAVIER_CANNOT_PROTECT
    k = FL.classify(XAVIER_CANNOT_PROTECT)
    assert (k["class"], k["family"], k["taxonomy_stage"]) == (
        RT.ECONOMIC, "PRICE", "RISK_ADMISSION")
    assert FL.chain_stage(XAVIER_CANNOT_PROTECT, mapped=True) == "ENTER_PASS"
    k = FL.classify(XAVIER_CANNOT_PRICE)
    assert (k["class"], k["family"], k["taxonomy_stage"]) == (
        RT.SOFTWARE, "CAPABILITY", "RISK_ADMISSION")
    assert FL.chain_stage(XAVIER_CANNOT_PRICE, mapped=True) == "ENTER_PASS"
    # neither is an external dependency: the census never files them
    # EXTERNAL
    assert ext.EVALUABILITY_OF.get(XAVIER_CANNOT_PRICE) != \
        ext.EXTERNAL_DEPENDENCY
    assert ext.EVALUABILITY_OF.get(XAVIER_CANNOT_PROTECT) != \
        ext.EXTERNAL_DEPENDENCY


def _event(eid="e1", slug="aec-cfb-a-b-2026-10-10"):
    return {"sport_key": "americanfootball_ncaaf", "family": "football",
            "provider_event_id": eid, "outcome": "REFUSED",
            "stage": "2_FRESHNESS", "reach": 4, "first_refusal": QUOTE_STALE,
            "codes": [QUOTE_STALE, AGE_UNKNOWN], "us_market_slug": slug,
            "slugs": [slug]}


def _val(i, eid="e1", slug="aec-cfb-a-b-2026-10-10"):
    return {"id": i, "event_key": eid, "us_market_slug": slug,
            "record_purpose": "CALIBRATION_ONLY", "admissible": False,
            "refusals": ["VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
                         "NO_ACTION_HAS_POSITIVE_NET_EDGE"]}


def _dec(vid, strategy, *codes):
    return {"valuation_id": vid, "verdict": "REFUSE", "strategy": strategy,
            "refusal": codes[0], "refusals": list(codes)}


def test_the_earliest_stage_wins_and_the_second_wave_surfaces_when_it_goes():
    """One event, one FAIR_VALUE-stage SOFTWARE decision and one ENTER_PASS-
    stage SOFTWARE decision: the first loss is the earlier stage. Remove the
    earlier one and the later SOFTWARE code IS the first loss (the second
    wave); with an EV-stage ECONOMIC decision beside, EV wins over
    ENTER_PASS and the event is ECONOMIC."""
    ev, vals = _event(), [_val(1)]
    stale = _dec(1, "DEREK_ENTRY_POLICY_V2", STALE)
    xav = _dec(1, "PINNACLE_EXPLORATION_PAPER", XAVIER_CANNOT_PRICE)
    ev_dec = _dec(1, "PINNACLE_COMPLETED_GAME_PAPER", "BELOW_MIN_GROSS_EDGE")
    got = FL.census([ev], vals, [xav, stale])
    (row,) = got["totals"]["by_code"]
    assert (row["stage"], row["code"], row["class"], row["source"]) == (
        "FAIR_VALUE", STALE, RT.SOFTWARE, "paper_decisions")
    # the wave: the stale decision gone, the Xavier code is the loss
    got = FL.census([ev], vals, [xav])
    (row,) = got["totals"]["by_code"]
    assert (row["stage"], row["code"], row["class"]) == (
        "ENTER_PASS", XAVIER_CANNOT_PRICE, RT.SOFTWARE)
    assert got["totals"]["by_class"]["SOFTWARE"] == 1
    # an economics decision beside takes it: EV before ENTER_PASS
    got = FL.census([ev], vals, [xav, ev_dec])
    (row,) = got["totals"]["by_code"]
    assert (row["stage"], row["code"], row["class"]) == (
        "EV", "BELOW_MIN_GROSS_EDGE", RT.ECONOMIC)
    # and the ECONOMIC Xavier refusal alone is ECONOMIC at ENTER_PASS
    got = FL.census([ev], vals, [_dec(1, "PINNACLE_EXPLORATION_PAPER",
                                      XAVIER_CANNOT_PROTECT)])
    (row,) = got["totals"]["by_code"]
    assert (row["stage"], row["code"], row["class"]) == (
        "ENTER_PASS", XAVIER_CANNOT_PROTECT, RT.ECONOMIC)
    # no decision at all: the valuation's earliest code, EXTERNAL at BOOK
    got = FL.census([ev], vals, [])
    (row,) = got["totals"]["by_code"]
    assert (row["stage"], row["class"], row["source"]) == (
        "BOOK", FL.EXTERNAL, "external_valuations")


# ═════════════════════════════════════════════════════════════════════
# 5 · THE RC6.3b GUARD AND THE HOURLY READBACK
# ═════════════════════════════════════════════════════════════════════

def _is_rc63b_code(code) -> bool:
    c = str(code or "")
    return c.startswith(RC63B_NEW_PREFIXES) or c in CAPABILITY_REVIEW_CODES


def test_the_rc63b_codes_are_known_and_classified():
    assert CAPABILITY_REVIEW_CODES >= {
        "NO_GENUINE_GROUNDED_REVIEW", "REVIEW_PERSONA_REFUSED",
        "REVIEW_MODEL_ANSWER_NOT_USED", "REVIEW_MODEL_UNAVAILABLE",
        "REVIEW_PERSONA_INTERRUPTED", "REVIEW_PERSONA_ERROR"}
    route = [c for c in TT.TABLE if c.startswith("PMUS_ROUTE_BOOK_")]
    shadow = [c for c in TT.TABLE if c.startswith("KALSHI_SHADOW_")]
    assert len(route) >= 9 and len(shadow) >= 20
    for c in route + shadow + sorted(CAPABILITY_REVIEW_CODES):
        assert RT.classify(c)["classified"], c


def test_no_rc63b_code_is_a_first_loss_in_the_packet_hour():
    """The release under test (ec8b892d) carries route-book, kalshi-shadow
    and the capability review; none of their codes reached a first loss --
    nor any record -- in the hour."""
    events, vals, decs = _inputs()
    got = FL.census(events, vals, decs)
    assert not [r for r in got["totals"]["by_code"]
                if _is_rc63b_code(r["code"])]
    everywhere = set()
    for ev in events:
        everywhere.update(ev["codes"] or [])
    for v in vals:
        everywhere.update(v["refusals"] or [])
    for d in decs:
        everywhere.update(d["refusals"] or [])
    assert not {c for c in everywhere if _is_rc63b_code(c)}


def _hourly():
    doc = json.loads(HOURLY.read_text())
    assert doc["version"] == T.VERSION
    assert doc["census_version"] == FL.VERSION
    assert doc["taxonomy_version"] == RT.VERSION
    # replayed from an export that carries the gate's decision order
    assert doc["source"]["kind"] == "research-sql run log"
    assert doc["source"]["decision_order"] == T.DECISION_ORDER
    return doc


def test_the_hourly_readback_has_24_rows_none_unclassified_and_the_packet_hour_is_84():  # noqa: E501
    """The readback attached to the RC6.3b packet: 24 hourly windows ending
    at `now`, `now` - 1 h, ... (research/rc63_sw_reds_census.sql, run
    against the production read replica), each replayed through the census;
    UNCLASSIFIED == 0 on every row and the packet hour's row IS the packet.
    """
    doc = _hourly()
    rows = doc["rows"]
    assert len(rows) == 24 and [r["k"] for r in rows] == list(range(24))
    now, hours = T.sql_parameters(SQL.read_text())
    assert hours == 24
    for r in rows:
        assert abs(r["now"] - (now - r["k"] * 3600)) < 1e-6
        assert abs(r["now"] - r["since"]) == FEEDS.FIRST_LOSS_WINDOW_S
        assert abs(r["until"] - r["now"] - 1.0) < 1e-6
        assert r["unclassified"] == 0 and r["by_class"]["UNCLASSIFIED"] == 0
        assert r["software"] == r["by_class"]["SOFTWARE"]
        sw_rows = [c for c in r["by_code"] if c["class"] == RT.SOFTWARE]
        assert sum(c["events"] for c in sw_rows) == r["software"]
        # the gate names at most SOFTWARE_BY_CODE_MAX codes (evidence only)
        # and says so; the full list is the census's by_code
        assert r["software_codes_truncated"] is \
            (len(sw_rows) > FEEDS.SOFTWARE_BY_CODE_MAX)
        assert r["software_by_code"] == [
            {"stage": c["stage"], "code": c["code"], "events": c["events"]}
            for c in sw_rows][:FEEDS.SOFTWARE_BY_CODE_MAX]
        assert sum(v for v in r["first_loss"].values() if v) + \
            (r["entered"] or 0) + r["unavailable"] == r["events"]
        assert not [c for c in r["by_code"] if _is_rc63b_code(c["code"])]
        # the cells the gate's tie-break leaves to chance are named per
        # row, and every named cell is a cell of the row
        assert r["order_ambiguous_events"] == len(r["order_ambiguity"])
        named = {(c["stage"], c["code"], c["class"])
                 for c in r["order_ambiguous_cells"]}
        assert named == {(c["stage"], c["code"], c["class"])
                         for a in r["order_ambiguity"] for c in a["cells"]}
        for a in r["order_ambiguity"]:
            assert a["decided_at_epoch"] is not None
            assert len(a["codes"]) >= 2
        assert (r["k"] in doc["order_ambiguous_rows"]) is \
            bool(r["order_ambiguous_events"])
    (packet,) = [r for r in rows if abs(r["now"] - PACKET_NOW) < 1e-6]
    assert packet["by_class"] == PACKET_GATE["evidence"]["by_class"]
    assert packet["software_by_code"] == \
        PACKET_GATE["evidence"]["software_by_code"]
    assert packet["software_codes_truncated"] is False
    assert packet["events"] == 104
    # the packet hour's SOFTWARE cells are the gate's: no SOFTWARE cell of
    # that row is a tie
    assert not [c for c in packet["order_ambiguous_cells"]
                if c["class"] == RT.SOFTWARE]
    # the series is the day the packet hour sits in, not a repeat of it:
    # the cap the gate names its codes under is reached in some hours
    assert any(r["software_codes_truncated"] for r in rows)
    assert len({(c["stage"], c["code"]) for r in rows
                for c in r["by_code"] if c["class"] == RT.SOFTWARE}) > 4
    # the first full RC6.3b hour (the workers write the ledger and the
    # decisions) carries none of the release's new codes either
    first = min((r for r in rows if r["since"] >= RC63B_WORKERS_LIVE),
                key=lambda r: r["since"])
    assert first["events"] > 0
    assert not [c for c in first["by_code"] if _is_rc63b_code(c["code"])]
    assert first["unclassified"] == 0


def test_the_research_file_is_the_generators_and_read_only():
    """research/rc63_sw_reds_census.sql is exactly what the tool generates
    from the production statements for its own `now` and `hours`, so the
    export cannot drift from what the gate reads; it passes the research-sql
    workflow's read-only guard; its windows are the gate's."""
    text = SQL.read_text()
    now, hours = T.sql_parameters(text)
    assert hours == 24
    assert text == T.hourly_census_sql(now, hours)
    assert T.mutating_keywords(text) == []
    for ln in text.splitlines():
        if ln.lstrip().startswith("\\"):
            assert ln.lstrip().startswith("\\echo"), ln
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith("--") and not ln.startswith("\\"))
    assert body.strip().upper().startswith("WITH")
    # the production statements are embedded line for line (re-indented,
    # their $n parameters bound), the ledger stage and reach expressions
    # among them
    from sportsassets.agents import coverage_integrity as C
    lines = [ln.strip() for ln in text.splitlines()]
    assert C.LEDGER_STAGE_EXPR in text
    for sql in (C.REACH_SQL, FL.VALUATIONS_SQL, FL.DECISIONS_SQL):
        for ln in sql.strip().splitlines():
            if "$" in ln or not ln.strip():
                continue
            assert any(ln.strip() in L for L in lines), ln
    assert "$" not in "\n".join(ln for ln in text.splitlines()
                                if not ln.startswith("--"))
    assert "p.now - (k + 1) * 3600 AS since" in text
    assert "p.now - k * 3600 + 1 AS until" in text
    assert "LIMIT %d" % FL.MAX_EVENTS in text
    assert "LIMIT %d" % FL.MAX_VALUATIONS in text
    # the fixture's instants are the file's own documented provenance
    assert "38055963121" in text and "rc63_sw_reds_104_events.json" in text


def test_the_research_file_exports_the_gates_decision_order():
    """The `dec` rows carry DECISIONS_SQL's order (decided_at) and the
    row's key: the two order columns appended to the production
    statement's select list are the ONLY edit to it, the statement's head
    is pinned, and the export's own row order puts a valuation's decisions
    in the gate's order so the raw research log reads as the gate read."""
    text = SQL.read_text()
    exported = T.decisions_export_sql()
    # the one edit: remove the appended columns and the production
    # statement is back, byte for byte
    assert exported.replace(",\n           " + T.DECISION_ORDER_COLUMNS,
                            "", 1) == FL.DECISIONS_SQL
    assert FL.DECISIONS_SQL.strip().startswith(T.DECISIONS_SELECT + "\n")
    assert "decided_at" not in FL.DECISIONS_SQL.split("FROM")[0]
    assert T.DECISION_ORDER_COLUMNS == (
        "extract(epoch FROM decided_at)::float8 AS decided_at_epoch, "
        "decision_id")
    assert T.DECISION_ORDER == "decided_at_epoch, decision_id"
    # in the file: once, inside the dc LATERAL, right after the pinned head
    assert text.count(T.DECISION_ORDER_COLUMNS) == 1
    dc = text[text.index("dc AS ("):text.index("SELECT j FROM (")]
    assert T.DECISIONS_SELECT + ",\n" in dc
    assert T.DECISION_ORDER_COLUMNS in dc
    assert "ORDER BY decided_at\n" in dc
    # the statement's ORDER BY still names the table column, not an alias
    assert "AS decided_at," not in text and "AS decided_at\n" not in text
    # the export's row order: valuation, then decided_at, then the id
    tail = text[text.index("SELECT j FROM ("):]
    assert "lpad(valuation_id::text, 12, '0')" in tail
    assert ("to_char(to_timestamp(decided_at_epoch) AT TIME ZONE 'UTC',\n"
            "                   'YYYY-MM-DD HH24:MI:SS.US') || ' ' || "
            "decision_id") in tail
    assert "coalesce(strategy, '')" not in tail
    # and the file says what it carries and what it supersedes
    assert "38069842557" in text and "superseded" in text


def _hour_rows(decs, *, k=0, now=PACKET_NOW, events=None, vals=None):
    """Export rows of one synthetic hour: one mapped event, one valuation,
    the decisions given (each carrying the order columns unless stripped)."""
    events = events if events is not None else [_event()]
    vals = vals if vals is not None else [_val(1)]
    rows = [{"kind": "hour", "k": k, "now": now, "since": now - 3600,
             "until": now + 1}]
    rows += [{"kind": "ev", "k": k, "row": e} for e in events]
    rows += [{"kind": "val", "k": k, "row": v} for v in vals]
    rows += [{"kind": "dec", "k": k, "row": d} for d in decs]
    return rows


def _odec(vid, strategy, decided_at, decision_id, *codes):
    return dict(_dec(vid, strategy, *codes), decided_at_epoch=decided_at,
                decision_id=decision_id)


def _sw_cells(row):
    return [(c["stage"], c["code"], c["events"]) for c in row["by_code"]
            if c["class"] == RT.SOFTWARE]


def test_the_replay_takes_the_gates_order_from_the_export_not_the_files():
    """Two decisions of one valuation at the same stage (FAIR_VALUE) with
    different SOFTWARE codes: the gate's read returns them ORDER BY
    decided_at and first_loss_of_event takes the first, so the earlier
    decision's code is the first loss -- whatever order the export's rows
    come in, and whatever the strategies' names sort like (the first
    export's mistake: DEREK_ENTRY_POLICY_V2 sorted before PINNACLE_*)."""
    t0 = PACKET_NOW - 1800.0
    # DEREK decided LATER but sorts FIRST by strategy name; the file lists
    # it first too
    derek = _odec(1, "DEREK_ENTRY_POLICY_V2", t0 + 2.0, "dec-b", STALE)
    pinn = _odec(1, "PINNACLE_EXPLORATION_PAPER", t0 + 1.0, "dec-a", OLDER)
    assert FL.chain_stage(STALE, mapped=True) == "FAIR_VALUE"
    assert FL.chain_stage(OLDER, mapped=True) == "FAIR_VALUE"
    for file_order in ([derek, pinn], [pinn, derek]):
        hours = T.hours_from_rows(_hour_rows(file_order))
        (h,) = hours.values()
        assert [d["decision_id"] for d in h["decisions"]] == ["dec-a",
                                                              "dec-b"]
        assert [d["decided_at_epoch"] for d in h["decisions"]] == \
            [t0 + 1.0, t0 + 2.0]
        (row,) = T.hourly_table(hours)
        assert _sw_cells(row) == [("FAIR_VALUE", OLDER, 1)], file_order
        assert row["order_ambiguous_events"] == 0
        assert row["order_ambiguous_cells"] == []
    # the gate's own derivation on the same inputs in the gate's order
    want = T.gate_evidence(FL.census([_event()], [_val(1)], [pinn, derek]))
    assert want["software_by_code"] == [
        {"stage": "FAIR_VALUE", "code": OLDER, "events": 1}]
    # and the OTHER order (the first export's) gives the other split: the
    # defect the order columns exist to remove
    other = T.gate_evidence(FL.census([_event()], [_val(1)], [derek, pinn]))
    assert other["software_by_code"] == [
        {"stage": "FAIR_VALUE", "code": STALE, "events": 1}]
    assert other["by_class"] == want["by_class"]
    # valuations: by id, whatever the file's order
    vals = [_val(2), _val(1)]
    hours = T.hours_from_rows(_hour_rows([pinn], vals=vals))
    assert [v["id"] for v in hours[0]["valuations"]] == [1, 2]
    # two valuations of one event: the lower id's decision wins a same-stage
    # tie across valuations, by the gate's valuation order, not by time
    late_on_1 = _odec(1, "PINNACLE_EXPLORATION_PAPER", t0 + 9.0, "dec-1",
                      OLDER)
    early_on_2 = _odec(2, "DEREK_ENTRY_POLICY_V2", t0 + 1.0, "dec-2", STALE)
    hours = T.hours_from_rows(_hour_rows([early_on_2, late_on_1],
                                         vals=[_val(2), _val(1)]))
    (row,) = T.hourly_table(hours)
    assert _sw_cells(row) == [("FAIR_VALUE", OLDER, 1)]
    assert row["order_ambiguous_events"] == 0


def test_an_export_without_the_order_columns_is_refused_not_replayed():
    """The first export's rows (no decided_at_epoch / decision_id) raise
    UnorderedExport: the replay never applies another order in the gate's
    place. A row missing either column is refused the same."""
    plain = [_dec(1, "DEREK_ENTRY_POLICY_V2", STALE),
             _dec(1, "PINNACLE_EXPLORATION_PAPER", OLDER)]
    with pytest.raises(T.UnorderedExport, match="decided_at_epoch"):
        T.hours_from_rows(_hour_rows(plain))
    half = [_odec(1, "DEREK_ENTRY_POLICY_V2", PACKET_NOW, "d1", STALE),
            dict(_dec(1, "PINNACLE_EXPLORATION_PAPER", OLDER),
                 decided_at_epoch=PACKET_NOW - 1.0)]
    with pytest.raises(T.UnorderedExport, match="PINNACLE_EXPLORATION"):
        T.hours_from_rows(_hour_rows(half))
    assert issubclass(T.UnorderedExport, ValueError)
    # the first export's row shape (research-sql 38069842557): exactly the
    # five statement columns, so that log is refused by this tool
    with pytest.raises(T.UnorderedExport):
        T.hours_from_rows([
            {"kind": "hour", "k": 0, "now": PACKET_NOW,
             "since": PACKET_NOW - 3600, "until": PACKET_NOW + 1},
            {"kind": "dec", "k": 0, "row": {
                "refusal": "BELOW_MIN_GROSS_EDGE", "verdict": "REFUSE",
                "refusals": ["BELOW_MIN_GROSS_EDGE"],
                "strategy": "PINNACLE_COMPLETED_GAME_PAPER",
                "valuation_id": 67840}}])
    # an hour with no decisions at all is not an unordered export
    hours = T.hours_from_rows(_hour_rows([]))
    assert hours[0]["decisions"] == []


def test_a_decided_at_tie_is_named_not_resolved():
    """Two decisions of the deciding valuation at the earliest stage, with
    different codes and the SAME decided_at: the gate's ORDER BY decided_at
    did not order them, so the replay's cell is one of two and says so
    (order_ambiguity, the `*` in the table), while SOFTWARE / by_class /
    UNCLASSIFIED are the same under either. Ties that cannot decide a cell
    are not named: the same code twice, a tie at a later stage than the
    deciding one, a tie behind an ENTER, a tie across two valuations (the
    valuation order decides), two decisions a microsecond apart."""
    t0 = PACKET_NOW - 1800.0
    tie = [_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "dec-b", STALE),
           _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "dec-a", OLDER)]
    hours = T.hours_from_rows(_hour_rows(tie))
    (row,) = T.hourly_table(hours)
    assert row["order_ambiguous_events"] == 1
    assert row["order_ambiguous_cells"] == [
        {"stage": "FAIR_VALUE", "code": OLDER, "class": RT.SOFTWARE},
        {"stage": "FAIR_VALUE", "code": STALE, "class": RT.SOFTWARE}]
    (a,) = row["order_ambiguity"]
    assert a["provider_event_id"] == "e1" and a["stage"] == "FAIR_VALUE"
    assert sorted(a["codes"]) == sorted([STALE, OLDER])
    assert a["decided_at_epoch"] == t0
    # the replay shows the decision_id order (dec-a first: OLDER) and marks
    # the cell; the other order's cell is marked too (both are named)
    assert _sw_cells(row) == [("FAIR_VALUE", OLDER, 1)]
    md = T.render_markdown([row])
    assert "| 1* |" in md and T.AMBIGUOUS_LEGEND in md
    # invariant under the other resolution: the class totals
    flipped = FL.census([_event()], [_val(1)], list(reversed(
        hours[0]["decisions"])))["totals"]
    assert flipped["by_class"] == row["by_class"] == {
        "SOFTWARE": 1, "ECONOMIC": 0, "EXTERNAL": 0, "UNCLASSIFIED": 0}
    assert [(c["stage"], c["code"]) for c in flipped["by_code"]] == [
        ("FAIR_VALUE", STALE)]

    def ambiguous(decs, vals=None):
        hours = T.hours_from_rows(_hour_rows(decs, vals=vals))
        (row,) = T.hourly_table(hours)
        assert T.AMBIGUOUS_MARK not in T.render_markdown([row]) or \
            row["order_ambiguous_events"]
        return row["order_ambiguous_events"]

    # a microsecond apart: ordered, not tied
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0 + 1e-6, "b",
                            STALE),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            OLDER)]) == 0
    # the same code twice at the same instant: no cell depends on it
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "b", STALE),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            STALE)]) == 0
    # a tie at ENTER_PASS behind a FAIR_VALUE decision: the earlier stage
    # decides, the tie is masked (the second wave, not a cell today)
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0 + 5, "c", STALE),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            XAVIER_CANNOT_PRICE),
                      _odec(1, "PINNACLE_COMPLETED_GAME_PAPER", t0, "b",
                            XAVIER_CANNOT_PROTECT)]) == 0
    # a tie at the earliest stage with an EV decision beside: still a tie
    # (EV is later than FAIR_VALUE)
    assert ambiguous(tie + [_odec(1, "PINNACLE_COMPLETED_GAME_PAPER", t0,
                                  "c", "BELOW_MIN_GROSS_EDGE")]) == 1
    # an ENTER anywhere: the event is ENTERED, no first loss to tie
    enter = {"valuation_id": 1, "verdict": "ENTER", "refusal": None,
             "refusals": [], "strategy": "PINNACLE_COMPLETED_GAME_PAPER",
             "decided_at_epoch": t0, "decision_id": "c"}
    assert ambiguous(tie + [enter]) == 0
    # the same instant on two valuations of the event: the valuation order
    # (by id) decides, not the clock
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "b", STALE),
                      _odec(2, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            OLDER)], vals=[_val(1), _val(2)]) == 0
    # a tie on the SECOND valuation while the first holds the deciding
    # stage: not named
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "c", STALE),
                      _odec(2, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            OLDER),
                      _odec(2, "PINNACLE_COMPLETED_GAME_PAPER", t0, "b",
                            STALE)], vals=[_val(1), _val(2)]) == 0
    # ... but a tie on the first valuation at the deciding stage is, even
    # with an ordered second valuation
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "c", STALE),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            OLDER),
                      _odec(2, "PINNACLE_COMPLETED_GAME_PAPER", t0 + 1, "b",
                            STALE)], vals=[_val(1), _val(2)]) == 1
    # the wrapper is unwrapped before the codes are compared, exactly as the
    # census unwraps it: the lane wrapper carrying the same lane
    # probability-stage code as the plain decision is no tie ...
    assert ext.STAGE_OF.get("NO_QUALIFIED_MODEL") == "1_PROBABILITY"
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "b",
                            "NO_QUALIFIED_MODEL"),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE",
                            "NO_QUALIFIED_MODEL")]) == 0
    # ... one carrying a different one (NO_QUALIFIED_MODEL beside Derek's
    # NO_QUALIFIED_PINNACLE_PROBABILITY: the row-12 valuation 61944 shape
    # of the first export) is ...
    assert ambiguous([_odec(1, "DEREK_ENTRY_POLICY_V2", t0, "b",
                            "NO_QUALIFIED_PINNACLE_PROBABILITY"),
                      _odec(1, "PINNACLE_EXPLORATION_PAPER", t0, "a",
                            "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE",
                            "NO_QUALIFIED_MODEL",
                            "NO_QUALIFIED_PINNACLE_PROBABILITY")]) == 1


@pytest.mark.parametrize("window", ["A", "B"])
def test_the_fixture_windows_gate_evidence_is_invariant_to_decision_order(
        window):
    """The packet-hour capture carries no decided_at, so every valuation's
    decisions count as tied and order_ambiguity is the exact test of which
    cells depend on the lost order. It names 9 events, every one at EV
    between ECONOMIC codes (BELOW_MIN_GROSS_EDGE against CASH_WAIT_TOTAL_
    EXECUTABLE_EV_NOT_POSITIVE on 8, against GROSS_EDGE_CLEARS_THRESHOLD_
    BUT_FEES_CONSUME_IT on 1) and NO SOFTWARE cell -- so the gate's
    evidence (by_class, software, software_by_code: 84 / 14 / 6 / 0 and
    35 / 1 / 38 / 10) is the gate's whatever order the decisions are fed
    in, while the split of the 14 ECONOMIC by code is the capture's order,
    not a claim. Shown exactly, then by permutation."""
    import random
    events, vals, decs = _inputs(window)
    assert all(d.get("decided_at_epoch") is None for d in decs)
    amb = T.order_ambiguity(events, vals, decs)
    assert len(amb) == 9
    assert {a["stage"] for a in amb} == {"EV"}
    assert all(a["decided_at_epoch"] is None for a in amb)
    assert {c["class"] for a in amb for c in a["cells"]} == {RT.ECONOMIC}
    import collections
    assert collections.Counter(tuple(sorted(a["codes"])) for a in amb) == {
        ("BELOW_MIN_GROSS_EDGE",
         "CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE"): 8,
        ("BELOW_MIN_GROSS_EDGE",
         "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT"): 1}
    groups: dict = {}
    for d in decs:
        groups.setdefault(d["valuation_id"], []).append(d)
    assert sum(len(g) > 1 for g in groups.values()) >= 30
    want = PACKET_GATE["evidence"]

    def fed(order):
        got = FL.census(events, vals, order)
        econ = [(r["stage"], r["code"], r["events"]) for r in
                got["totals"]["by_code"] if r["class"] == RT.ECONOMIC]
        return T.gate_evidence(got), econ

    ev, econ = fed(decs)
    assert ev == want
    assert econ == [("EV", "BELOW_MIN_GROSS_EDGE", 14)]
    reversed_within = [d for g in groups.values() for d in reversed(g)]
    ev, econ_rev = fed(reversed_within)
    assert ev == want
    # the ECONOMIC split does move with the order (the 9 named events), the
    # ECONOMIC total never does
    assert econ_rev != econ
    assert sum(n for _s, _c, n in econ_rev) == 14
    assert {c for _s, c, _n in econ_rev} <= {
        "BELOW_MIN_GROSS_EDGE", "CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE",
        "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT"}
    by_strategy_desc = sorted(decs, key=lambda d: (d["valuation_id"],
                                                   d["strategy"]),
                              reverse=True)
    assert fed(by_strategy_desc)[0] == want
    rng = random.Random(63)
    for _ in range(25):
        shuffled = []
        for g in groups.values():
            g = list(g)
            rng.shuffle(g)
            shuffled.extend(g)
        rng.shuffle(shuffled)
        ev, econ_s = fed(shuffled)
        assert ev == want
        assert sum(n for _s, _c, n in econ_s) == 14
    # the fixture path of the tool reports the same: the 9 ECONOMIC ties
    # named, no SOFTWARE cell among them, nothing marked in the SOFTWARE
    # table
    row = T.census_row(events, vals, decs, k=0, now=0, since=0, until=1)
    assert row["order_ambiguous_events"] == 9
    assert {c["class"] for c in row["order_ambiguous_cells"]} == {
        RT.ECONOMIC}
    assert T.AMBIGUOUS_MARK not in T.render_markdown([row])
    assert row["software_by_code"] == want["software_by_code"]


def test_the_tool_parses_a_run_log_and_a_fixture_alike(tmp_path):
    """A research-sql log line, a bare psql row and a plain JSON line all
    parse; the fixture window and the log rows (the fixture's decisions
    given the order columns an export carries, in the capture's order) give
    the same census, in whatever order the log lists the rows."""
    import random
    events, vals, decs = _inputs()
    since = PACKET_NOW - 3600
    ordered = [dict(d, decided_at_epoch=since + 1.0 + i,
                    decision_id="paper-%06d" % i)
               for i, d in enumerate(decs)]
    lines = ['research\tRun\t2026-10-10T16:00:00.0000000Z == header ==',
             'research\tRun\t2026-10-10T16:00:00.0000001Z  %s' % json.dumps(
                 {"kind": "hour", "k": 0, "now": PACKET_NOW,
                  "since": since, "until": PACKET_NOW + 1}),
             'research\tRun\t2026-10-10T16:00:00.0000002Z  (1 row)']
    body = []
    for ev in events:
        body.append(" " + json.dumps({"kind": "ev", "k": 0, "row": ev}))
    for v in vals:
        body.append(json.dumps({"kind": "val", "k": 0, "row": v}))
    for d in ordered:
        body.append('research\tRun\t2026-10-10T16:00:00.0000003Z %s'
                    % json.dumps({"kind": "dec", "k": 0, "row": d}))
    random.Random(7).shuffle(body)
    p = tmp_path / "run.log"
    p.write_text("\n".join(lines + body) + "\n")
    hours = T.hours_from_rows(T.parse_log(p))
    assert sorted(hours) == [0]
    # the replay's inputs are in the gate's order whatever the file's
    assert [d["decision_id"] for d in hours[0]["decisions"]] == \
        [d["decision_id"] for d in ordered]
    assert [v["id"] for v in hours[0]["valuations"]] == \
        sorted(v["id"] for v in vals)
    table = T.hourly_table(hours)
    assert len(table) == 1
    assert table[0]["by_class"] == PACKET_GATE["evidence"]["by_class"]
    assert table[0]["software_by_code"] == \
        PACKET_GATE["evidence"]["software_by_code"]
    # distinct instants: nothing is tied any more
    assert table[0]["order_ambiguous_events"] == 0
    md = T.render_markdown(table)
    assert "| 0 | 2026-10-10T08:54:34Z .. 2026-10-10T09:54:34Z | 104 | 84 " \
           "| 14 | 6 | 0 |" in md
    assert T.AMBIGUOUS_MARK not in md
    # the fixture path (no order columns) gives the same gate evidence and
    # names its ECONOMIC ties
    fixture = T.census_row(events, vals, decs, k=0, now=PACKET_NOW,
                           since=since, until=PACKET_NOW + 1)
    assert fixture["software_by_code"] == table[0]["software_by_code"]
    assert fixture["by_class"] == table[0]["by_class"]
    assert fixture["order_ambiguous_events"] == 9
