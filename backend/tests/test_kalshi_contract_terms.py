"""Kalshi game contract terms (the venue's rulebook) bound to the canonical
claim layer. Production 088af82 refused every Kalshi alias UNKNOWN_STATES:
the market text never states overtime or (MLB/NBA/NHL) the tie payout; the
series rulebook does. Bound only from recorded source bytes whose live hash
still matches, only for entire-game contracts, market text first."""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import kalshi_contract_terms as KCT
from sportsassets import settlement_rule_registry as SR
from sportsassets.workers import kalshi_market_data as W

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / KCT.SOURCE_DIR
SAMPLES = {json.loads(x)["ticker"]: json.loads(x) for x in (
    SRC / "market_rules_samples_2026-10-07.jsonl").read_text().splitlines()}
SERIES = {json.loads(x)["ticker"]: json.loads(x) for x in (
    SRC / "series_contract_terms_2026-10-07.jsonl").read_text().splitlines()}
MLB = "KXMLBGAME-26OCT071600CLECWS-CLE"
NFL = "KXNFLGAME-26OCT19WASSF-WAS"
NHL = "KXNHLGAME-26OCT13BOSSJ-SJ"
WNBA = "KXWNBAGAME-26OCT09GSLV-LV"


def flat(s):
    return re.sub(r"\s+", " ", s)


def test_recorded_hashes_and_clauses_are_the_source_bytes():
    for name, rb in KCT.RULEBOOKS.items():
        pdf = (SRC / "pdf" / ("%s.pdf" % name)).read_bytes()
        assert hashlib.sha256(pdf).hexdigest() == rb["pdf_sha256"], name
        text = flat((SRC / "text" / ("%s.txt" % name)).read_text())
        for k, clause in rb["clauses"].items():
            assert flat(clause) in text, (name, k)
    for line in (SRC / "SOURCES.sha256").read_text().splitlines():
        h, rel = line.split()
        assert hashlib.sha256((SRC / rel).read_bytes()).hexdigest() == h


def test_every_mapped_game_series_names_a_recorded_rulebook():
    for t, s in SERIES.items():
        assert KCT.rulebook_of(s["contract_terms_url"]) in KCT.RULEBOOKS, t
    assert KCT.rulebook_of("https://evil.example/contract_terms/X.pdf") is None
    assert KCT.fetch_rulebook("https://evil.example/x.pdf")["status"] == \
        "REFUSED_URL"


def test_entire_game_contracts_only():
    for t, m in SAMPLES.items():
        assert KCT.entire_game(m["rules_primary"]), t
    assert not KCT.entire_game(
        "If Miami wins the first half of the New Orleans vs Miami Pro "
        "Basketball game originally scheduled for Oct 8, 2026, then ...")
    assert not KCT.entire_game(
        "If Cleveland wins the 1st 5 innings of the Cleveland vs Chicago WS "
        "professional baseball game originally scheduled for Oct 7, 2026")
    assert not KCT.entire_game("Will Cleveland win?")


def test_the_market_text_clauses_the_shared_parser_misses():
    m = SAMPLES[NFL]
    c = KCT.market_clauses(m["rules_primary"], m["rules_secondary"])
    assert c["draw_rule"][0] == SR.SCALAR_0_50
    m = SAMPLES[MLB]
    c = KCT.market_clauses(m["rules_primary"], m["rules_secondary"])
    assert c["postponement_window_hours"][0] == 48.0
    assert c["postponement_payout"][0] == SR.LAST_FAIR_PRICE
    m = SAMPLES[WNBA]
    c = KCT.market_clauses(m["rules_primary"], m["rules_secondary"])
    assert c["postponement_window_hours"][0] == 48.0


def _bound(ticker, *, sha="RECORDED", tie=False):
    m = SAMPLES[ticker]
    rb = KCT.rulebook_of(SERIES[ticker.split("-")[0]]["contract_terms_url"])
    ev = SR.kalshi_rule_evidence(m)
    obs = KCT.RULEBOOKS[rb]["pdf_sha256"] if sha == "RECORDED" else sha
    return KCT.bind(ev, rules_primary=m["rules_primary"],
                    rules_secondary=m["rules_secondary"], rulebook=rb,
                    observed_sha256=obs, has_tie_strike=tie)


def _claims(ev_a, ev_b, *, sport="BASEBALL", league="MLB"):
    fx = CC.Fixture(event_key="T:%s" % league, sport=sport, league=league,
                    start_epoch=1.0, outcome_kind="TWO_WAY", home="H",
                    away="A")

    def inst(mid, side, subj, ev):
        return CC.Instrument(
            venue="KALSHI", market_id=mid, side=side, subject=subj,
            settlement=dict(ev["settlement"]), settlement_status="PROVEN",
            mapping_status="ESTABLISHED", asks=(), observed_at=None,
            book_basis=None, sport=sport)
    a = inst("K-A", "YES", "AWAY", ev_a)
    b = inst("K-H", "NO", "HOME", ev_b)
    built = CC.build_claims(fx, [a, b])
    return a, b, built


def test_real_mlb_rule_text_plus_the_verified_rulebook_fingerprints():
    ev = _bound(MLB)
    s = ev["settlement"]
    assert s["overtime_included"] is True
    assert s["draw_rule"] == SR.SCALAR_0_50
    assert s["postponement_window_hours"] == 48.0
    applied = {(a["term"], a["source"]) for a in
               ev["contract_terms"]["applied"]}
    assert ("overtime_included", "RULEBOOK:BASEBALLGAMEWIN") in applied
    a, b, built = _claims(ev, ev)
    # every state now priced: both aliases fingerprint (no UNKNOWN_STATES)
    assert a.fingerprint is not None and a.refusals == []
    assert b.fingerprint is not None and b.refusals == []
    # ...and they differ ONLY where each market settles at its OWN Kalshi
    # fair price (cancelled, or completed past the 48 h window): nothing
    # states the two fair prices sum to $1, so away YES and home NO stay
    # separate claims (fail closed); every result state is equal
    rec = CC.equivalence_receipt(
        CC.Fixture(event_key="T:MLB", sport="BASEBALL", league="MLB",
                   start_epoch=1.0, outcome_kind="TWO_WAY", home="H",
                   away="A"), a, b, built["states"])
    assert rec["verdict"] == "NOT_ESTABLISHED"
    assert "NEVER_COMPLETED" in rec["states_differing"]
    for st in rec["states"]:
        if st["state"] in rec["states_differing"]:
            assert "FP[" in str(st["a"]) and "FP[" in str(st["b"]), st
        else:
            assert st["equal"] is True, st
    assert {"HOME_WIN", "AWAY_WIN", "DRAW"} <= {
        st["state"] for st in rec["states"] if st["equal"]}


def test_a_changed_or_unverified_rulebook_applies_nothing():
    for sha, why in (("0" * 64, KCT.R_CHANGED), (None, KCT.R_UNVERIFIED)):
        ev = _bound(MLB, sha=sha)
        assert why in ev["contract_terms"]["refused"]
        assert ev["settlement"].get("overtime_included") is None
        a, _b, _ = _claims(ev, ev)
        assert a.fingerprint is None
        assert any(r.startswith("UNKNOWN_STATES") for r in a.refusals)


def test_hockey_ties_stay_unknown_because_the_rulebook_fixes_no_payout():
    ev = _bound(NHL)
    assert ev["settlement"]["overtime_included"] is True
    assert ev["settlement"].get("draw_rule") is None
    a, _b, _ = _claims(ev, ev, sport="HOCKEY", league="NHL")
    assert a.fingerprint is None
    assert any("DRAW" in r for r in a.refusals)


def test_a_listed_tie_strike_blocks_the_no_tie_strike_clause():
    ev = _bound(MLB, tie=True)
    assert ev["settlement"].get("draw_rule") is None


def test_market_text_wins_over_the_rulebook():
    m = SAMPLES[NFL]
    ev = SR.kalshi_rule_evidence(m)
    ev["settlement"] = dict(ev["settlement"], draw_rule="SOMETHING_ELSE")
    out = KCT.bind(ev, rules_primary=m["rules_primary"],
                   rules_secondary=m["rules_secondary"],
                   rulebook="FOOTBALLGAMEWIN",
                   observed_sha256=KCT.RULEBOOKS["FOOTBALLGAMEWIN"][
                       "pdf_sha256"], has_tie_strike=False)
    assert out["settlement"]["draw_rule"] == "SOMETHING_ELSE"


def test_the_certificate_fingerprint_covers_the_rulebook():
    plain = SR.kalshi_rule_evidence(SAMPLES[MLB])
    ev = _bound(MLB)
    assert ev["rules_sha256"] != plain["rules_sha256"]
    assert ev["rules_sha256"] != _bound(MLB, tie=True)["rules_sha256"]


def test_the_worker_refreshes_each_rulebook_at_most_every_six_hours():
    calls = []

    def fetch(url):
        calls.append(url)
        name = KCT.rulebook_of(url)
        return {"url": url, "rulebook": name, "status": "OK",
                "sha256": KCT.RULEBOOKS[name]["pdf_sha256"]}
    series = {t: {"contract_terms_url": s["contract_terms_url"]}
              for t, s in SERIES.items()}
    st = W.contract_terms_refresh(series, sorted(series), {}, now=1000.0,
                                  fetch=fetch)
    assert st["series"]["KXWNBAGAME"] == "BASKETBALLGAMEWIN"
    assert len(calls) == 4                       # one per distinct rulebook
    assert all(r["matches_recorded"] for r in st["rulebooks"].values())
    W.contract_terms_refresh(series, sorted(series), st, now=2000.0,
                             fetch=fetch)
    assert len(calls) == 4                       # cached
    W.contract_terms_refresh(series, sorted(series), st,
                             now=1000.0 + W.CONTRACT_TERMS_EVERY_S + 1,
                             fetch=fetch)
    assert len(calls) == 8


@pytest.mark.parametrize("ticker", sorted(SAMPLES))
def test_every_recorded_sample_binds_overtime(ticker):
    assert _bound(ticker)["settlement"]["overtime_included"] is True


def test_the_packaged_samples_are_the_recorded_source_records():
    from sportsassets.pm_bind import golden as G
    pkg = (pathlib.Path(G.__file__).resolve().parents[1] / "pm_evidence" /
           "data" / G.KT_SAMPLES).read_bytes()
    assert pkg == (SRC / "market_rules_samples_2026-10-07.jsonl").read_bytes()
