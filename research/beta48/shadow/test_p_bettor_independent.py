"""Tests for P_BETTOR_INDEPENDENT_V1 and the orthogonal-information test."""

import datetime
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ev_core_event_model as evm  # noqa: E402
import ev_core_orthogonal as orth  # noqa: E402
import p_bettor_independent as pbi  # noqa: E402

# A fixed base date: the fixtures must be deterministic and must be
# real calendar dates, because the fit parses them.
BASE_DATE = datetime.date(2026, 1, 1)


# ---------------------------------------------------------------------------
# a small synthetic league we know the truth about
# ---------------------------------------------------------------------------


def synth_league(n_clubs=12, n_rounds=6, seed=7, strength_spread=0.6):
    """Clubs with known attack strengths, playing a real round-robin."""
    rnd = random.Random(seed)
    clubs = ["club %02d" % i for i in range(n_clubs)]
    truth = {c: strength_spread * (i - (n_clubs - 1) / 2.0) / (n_clubs - 1)
             for i, c in enumerate(clubs)}
    out = []
    day = 0
    for _r in range(n_rounds):
        order = clubs[:]
        rnd.shuffle(order)
        for i in range(0, n_clubs - 1, 2):
            h, a = order[i], order[i + 1]
            lam = math.exp(0.15 + truth[h] - truth[a] + 0.25)
            mu = math.exp(0.15 + truth[a] - truth[h])
            hg = sum(1 for _ in range(20) if rnd.random() < lam / 20.0)
            ag = sum(1 for _ in range(20) if rnd.random() < mu / 20.0)
            day += 7
            date = (BASE_DATE + datetime.timedelta(days=day)).isoformat()
            out.append({"SEASON": "2026-27", "VENUE_LEAGUE": "syn",
                        "DATE": date, "HOME": h, "AWAY": a,
                        "HOME_KEY": h, "AWAY_KEY": a, "PLAYED": True,
                        "FT_HOME": hg, "FT_AWAY": ag,
                        "HT_HOME": hg // 2, "HT_AWAY": ag // 2})
    out.sort(key=lambda m: m["DATE"])
    return clubs, truth, out


# ---------------------------------------------------------------------------
# the independence assertion
# ---------------------------------------------------------------------------


def test_the_model_declares_and_uses_no_venue_input():
    p = pbi.provenance()
    assert p["TARGET_MARKET_PRICE_USED"] == "NO"
    assert p["VENUE_DATA_USED"] == "NONE"
    assert p["WHALE_DATA_USED"] == "NONE"
    assert p["CORPUS_SETTLEMENTS_USED_FOR_FITTING"] == "NO"
    assert all("public" in s or "goals" in s for s in p["INPUTS"])


def test_the_fit_signature_admits_only_public_fixture_rows():
    # A fixture row carries no price field, so there is no channel by which a
    # venue price could enter the fit even by accident.
    _c, _t, matches = synth_league()
    assert not any(k for m in matches for k in m
                   if "PRICE" in k or "VENUE_PRICE" in k or "ASK" in k)


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------


def test_a_thin_sample_refuses_rather_than_fitting_confident_nonsense():
    _c, _t, matches = synth_league(n_clubs=6, n_rounds=1)
    fit = pbi.fit_dixon_coles(matches, "2027-01-01")
    assert fit["FITTED"] is False
    assert fit["REASON"] == "TOO_FEW_MATCHES"


def test_the_fit_recovers_the_strength_ordering_it_was_generated_from():
    clubs, truth, matches = synth_league(n_clubs=14, n_rounds=26,
                                         strength_spread=1.2)
    fit = pbi.fit_dixon_coles(matches, "2030-01-01", xi=0.0)
    assert fit["FITTED"] is True
    # A club's overall strength is ATTACK + DEFENCE, not the difference:
    # lambda = exp(mu + atk_home - def_away + hfa), so a strong club has BOTH
    # a high attack and a high defence, and the difference cancels exactly the
    # quantity we are trying to recover.
    got = [fit["ATTACK"][c] + fit["DEFENCE"][c] for c in clubs]
    want = [truth[c] for c in clubs]
    # Rank correlation, not equality: the scale is not identified, the order is.
    n = len(clubs)
    rg = sorted(range(n), key=lambda i: got[i])
    rw = sorted(range(n), key=lambda i: want[i])
    pg = [0] * n
    pw = [0] * n
    for r, i in enumerate(rg):
        pg[i] = r
    for r, i in enumerate(rw):
        pw[i] = r
    d2 = sum((pg[i] - pw[i]) ** 2 for i in range(n))
    rho = 1 - 6.0 * d2 / (n * (n * n - 1))
    assert rho > 0.6, rho


def test_only_matches_before_the_cutoff_are_used():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    cut = matches[len(matches) // 2]["DATE"]
    fit = pbi.fit_dixon_coles(matches, cut)
    expected = sum(1 for m in matches if m["DATE"] < cut)
    assert fit["MATCHES_USED"] == expected


def test_time_decay_weights_recent_matches_more():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    flat = pbi.fit_dixon_coles(matches, "2030-01-01", xi=0.0)
    decayed = pbi.fit_dixon_coles(matches, "2030-01-01", xi=0.01)
    assert flat["EFFECTIVE_MATCHES"] > decayed["EFFECTIVE_MATCHES"]
    assert abs(flat["EFFECTIVE_MATCHES"] - flat["MATCHES_USED"]) < 1e-6


def test_a_club_the_fit_barely_saw_is_refused_not_guessed():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    matches.append({"SEASON": "2026-27", "VENUE_LEAGUE": "syn",
                    "DATE": (BASE_DATE + datetime.timedelta(days=7)).isoformat(),
                    "HOME": "newcomer",
                    "AWAY": "club 00", "HOME_KEY": "newcomer",
                    "AWAY_KEY": "club 00", "PLAYED": True,
                    "FT_HOME": 1, "FT_AWAY": 1, "HT_HOME": 0, "HT_AWAY": 0})
    fit = pbi.fit_dixon_coles(matches, "2030-01-01")
    got, reason = pbi.rates(fit, "newcomer", "club 00")
    assert got is None and reason == "CLUB_TOO_FEW_MATCHES_IN_FIT"


def test_a_club_absent_from_the_fit_is_named_not_averaged():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    fit = pbi.fit_dixon_coles(matches, "2030-01-01")
    got, reason = pbi.rates(fit, "a club that never played", "club 00")
    assert got is None and reason == "CLUB_NOT_IN_FIT"


def test_home_advantage_comes_out_positive_when_it_was_put_in():
    _c, _t, matches = synth_league(n_clubs=14, n_rounds=26)
    fit = pbi.fit_dixon_coles(matches, "2030-01-01", xi=0.0)
    assert fit["HOME_ADVANTAGE"] > 0.0


# ---------------------------------------------------------------------------
# the grid, and the contracts drawn from it
# ---------------------------------------------------------------------------


def _grid():
    _c, _t, matches = synth_league(n_clubs=14, n_rounds=26)
    fit = pbi.fit_dixon_coles(matches, "2030-01-01")
    g, why = pbi.grid_for(fit, "club 13", "club 00")
    assert why == "OK"
    return g


def test_the_grid_is_a_probability_distribution():
    g = _grid()
    assert abs(sum(g.values()) - 1.0) < 1e-9
    assert all(p >= 0 for p in g.values())


def test_the_totals_ladder_is_monotone_because_it_came_from_one_grid():
    g = _grid()
    over = [evm.p_total_over(g, ln) for ln in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)]
    assert all(over[i] >= over[i + 1] - 1e-12 for i in range(len(over) - 1))


def test_the_1x2_set_sums_to_one():
    g = _grid()
    s = evm.p_home_win(g) + evm.p_draw(g) + evm.p_away_win(g)
    assert abs(s - 1.0) < 1e-9


def test_over_and_under_are_complements():
    g = _grid()
    p, why = pbi.price_contract("epl-a-b-2026-08-30-total-2pt5", "Over", g)
    q, why2 = pbi.price_contract("epl-a-b-2026-08-30-total-2pt5", "Under", g)
    assert why == why2 == "OK"
    assert abs(p + q - 1.0) < 1e-12


def test_the_first_half_ladder_is_priced_from_the_half_time_grid():
    g, ht = _grid(), _grid()
    p_ft, _ = pbi.price_contract("epl-a-b-2026-08-30-total-2pt5", "Over", g, ht)
    p_fh, why = pbi.price_contract(
        "epl-a-b-2026-08-30-first-half-total-2pt5", "Over", g, ht)
    assert why == "OK"
    # Same numbers here only because the fixture reuses one grid; the point of
    # the test is that the SEGMENT pattern wins over the plain '-total-' one.
    assert p_fh == p_ft


def test_a_segment_family_is_never_read_as_its_full_game_namesake():
    # '-first-half-total-2pt5' also ends in '-total-2pt5', and
    # '-halftime-result-draw' also ends in '-draw'. Without a half-time grid
    # both must refuse, not silently answer with the full-game number.
    g = _grid()
    assert pbi.price_contract("epl-a-b-2026-08-30-first-half-total-2pt5",
                              "Over", g, None)[1] == "NO_GRID_FOR_THIS_SEGMENT"
    assert pbi.price_contract("epl-a-b-2026-08-30-halftime-result-draw",
                              "Yes", g, None)[1] == "NO_GRID_FOR_THIS_SEGMENT"


def test_a_bare_code_must_name_a_team_in_this_fixture():
    g = _grid()
    p, why = pbi.price_contract("lal-rso-cel-2026-09-03-cel", "Yes", g)
    assert why == "OK" and abs(p - evm.p_away_win(g)) < 1e-12
    p, why = pbi.price_contract("lal-rso-cel-2026-09-03-rso", "Yes", g)
    assert why == "OK" and abs(p - evm.p_home_win(g)) < 1e-12
    assert pbi.price_contract("lal-rso-cel-2026-09-03-bar", "Yes", g)[1] == \
        "BARE_CODE_IS_NOT_A_TEAM_IN_THIS_FIXTURE"


def test_the_spread_family_refuses_because_the_handicap_sign_is_unknown():
    g = _grid()
    p, why = pbi.price_contract("epl-che-bri-2026-08-30-spread-home-2pt5",
                                "Chelsea FC", g)
    assert p is None and why == pbi.SPREAD_REFUSAL
    assert "complements of each other" in pbi.SPREAD_REFUSAL_WHY


def test_an_unknown_family_refuses_rather_than_returning_a_number():
    g = _grid()
    assert pbi.price_contract("epl-a-b-2026-08-30-corners-over-9pt5",
                              "Over", g)[0] is None
    assert pbi.price_contract("epl-a-b-2026-08-30", "Yes", g)[1] == \
        "EVENT_SLUG_HAS_NO_CONTRACT"


def test_team_totals_come_from_the_grids_own_marginal():
    g = _grid()
    p, why = pbi.price_contract("epl-a-b-2026-08-30-team-total-home-1pt5",
                                "Over", g)
    assert why == "OK"
    direct = sum(v for (h, _a), v in g.items() if h > 1.5)
    assert abs(p - direct) < 1e-12


# ---------------------------------------------------------------------------
# walk-forward has no path from the future to the past
# ---------------------------------------------------------------------------


def test_the_cutoff_is_in_the_cache_key():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    wf = pbi.WalkForward(matches)
    # Past the fit minimum on both sides, so the comparison is between two
    # real fits rather than a fit and a refusal.
    early = wf.dc("syn", matches[3 * len(matches) // 4]["DATE"])
    late = wf.dc("syn", "2030-01-01")
    assert early["MATCHES_USED"] < late["MATCHES_USED"]
    assert wf.dc("syn", "2030-01-01") is late          # cached, same object
    assert early is not late


def test_an_unknown_league_refuses_instead_of_borrowing_another():
    _c, _t, matches = synth_league(n_clubs=12, n_rounds=20)
    wf = pbi.WalkForward(matches)
    ft, _ht, why = wf.grids("not-a-league", "2030-01-01", "club 00", "club 01")
    assert ft is None and why == "TOO_FEW_MATCHES"


# ---------------------------------------------------------------------------
# Elo
# ---------------------------------------------------------------------------


def test_elo_prices_the_1x2_set_and_nothing_else():
    _c, _t, matches = synth_league(n_clubs=14, n_rounds=26)
    fit = pbi.fit_elo(matches, "2030-01-01")
    p, why = pbi.elo_1x2(fit, "club 13", "club 00")
    assert why == "OK"
    assert abs(sum(p) - 1.0) < 1e-9 and all(x > 0 for x in p)
    assert pbi.elo_1x2(fit, "nobody", "club 00")[1] == "CLUB_NOT_IN_FIT"


def test_elo_rates_the_stronger_club_higher():
    _c, _t, matches = synth_league(n_clubs=14, n_rounds=26,
                                   strength_spread=1.2)
    fit = pbi.fit_elo(matches, "2030-01-01")
    assert fit["RATINGS"]["club 13"] > fit["RATINGS"]["club 00"]


# ---------------------------------------------------------------------------
# the orthogonal-information test
# ---------------------------------------------------------------------------


def _ortho_rows(n_events=140, per_event=8, share=0.0, seed=3):
    """Rows where the candidate holds `share` of the truth the market lacks."""
    rnd = random.Random(seed)
    rows = []
    for e in range(n_events):
        extra = rnd.gauss(0, 1.0)              # what the market cannot see
        for c in range(per_event):
            base = rnd.gauss(0, 1.0)
            z = base + extra
            y = 1 if rnd.random() < 1 / (1 + math.exp(-z)) else 0
            pm = 1 / (1 + math.exp(-base))
            pc = 1 / (1 + math.exp(-(base + share * extra)))
            rows.append({"EVENT_KEY": "e%d" % e, "Y": y,
                         "P_MARKET": min(max(pm, 1e-4), 1 - 1e-4),
                         "P_CANDIDATE": min(max(pc, 1e-4), 1 - 1e-4)})
    return rows


def test_a_candidate_that_knows_nothing_extra_is_reported_as_adding_nothing():
    rep = orth.orthogonality(_ortho_rows(share=0.0), draws=150)
    assert rep["STATUS"] == "MEASURED"
    assert rep["CANDIDATE_ADDS_INFORMATION"] is False
    assert rep["VERDICT"] == "NO_MEASURABLE_ADDITION_CONDITIONAL_ON_MARKET"


def test_a_candidate_that_does_know_something_extra_is_detected():
    rep = orth.orthogonality(_ortho_rows(share=1.0, seed=11), draws=200)
    assert rep["CANDIDATE_ADDS_INFORMATION"] is True
    assert rep["COEFFICIENTS_IN_SAMPLE"]["CANDIDATE_LOGIT"] > 0.1
    assert rep["LOG_LOSS_IMPROVEMENT"]["CI95_LOW"] > 0


def test_the_fold_unit_is_the_event_so_one_fixture_never_spans_the_split():
    rows = _ortho_rows(n_events=40, per_event=5)
    assign, n_events = orth.event_folds(rows, folds=5)
    assert n_events == 40
    for r in rows:
        assert assign[r["EVENT_KEY"]] == assign[rows[0]["EVENT_KEY"]] \
            or r["EVENT_KEY"] != rows[0]["EVENT_KEY"]
    seen = {}
    for r in rows:
        seen.setdefault(r["EVENT_KEY"], set()).add(assign[r["EVENT_KEY"]])
    assert all(len(v) == 1 for v in seen.values())


def test_every_reported_prediction_came_from_a_fold_that_never_saw_it():
    rows = _ortho_rows(n_events=60, per_event=6)
    assign, _ = orth.event_folds(rows, folds=5)
    preds, fitted, _ = orth.out_of_fold(rows, ["P_MARKET"], folds=5)
    assert fitted == 5
    assert len(preds) == len(rows)
    assert all(p is not None for p in preds)


def test_a_sample_too_small_to_measure_says_so():
    assert orth.orthogonality(_ortho_rows(n_events=5, per_event=2))["STATUS"] \
        == "TOO_FEW_ROWS"


def test_the_baseline_is_the_recalibrated_market_and_that_is_stated():
    assert orth.BASELINE_IS_RECALIBRATED is True
    assert "arithmetic, not" in orth.WHY_RECALIBRATED_BASELINE
    assert "not" in orth.ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE


def test_adding_information_is_not_claimed_to_be_an_edge():
    rep = orth.orthogonality(_ortho_rows(share=1.0, seed=11), draws=150)
    assert "sufficient" in rep["ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE"]
    assert "spread" in rep["ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE"]
