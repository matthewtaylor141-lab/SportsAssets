"""RC6 api-responsive: the ext_pinnacle cycle's reactive seed batches --
one fixture index per batch, names folded once, cold names folded on the
CPU lane before the batch, and an absence pass that answers exactly what the
scan answers.

THE EVIDENCE. The RC5 loop watchdog's persisted ring (research-sql
rc6_api-responsive_loop_stalls.sql, 2026-10-09 01:31Z, stalls 00:52-01:29Z):
2 of 20 API loop stalls of 2.5-3.4 s were the ext_pinnacle cycle registering
a competition's discovery seeds back to back (pinnapi_reactive.register ->
match_event -> fixture_view / names.absence -> _tokens: one full scan of
the feed cache per seed, and a re-tokenisation of every cached record per
miss). The responsiveness harness (tools/api_responsiveness_harness.py)
then showed the first batch after a boot folding every cached name on the
loop (0.5-0.66 s holds at its load).
"""
from __future__ import annotations

import asyncio
import random
import threading

# ═════════════════════════════════════════════════════════════════════
# 4 · REACTIVE SEEDS REGISTERED IN A BATCH (ext_pinnacle cycle, 2 of 20)
# ═════════════════════════════════════════════════════════════════════

def _feed_cache(n_events=2600):
    from sportsassets import pinnapi_feed as F
    c = F.FeedCache()
    c.offload_snapshots = False
    ep = c.new_connection([("prematch", 1)])
    recs = []
    for i in range(n_events):
        recs.append({"id": 10_000 + i, "type": "matchup",
                     "startTime": "2026-10-09T18:00:00Z", "isLive": False,
                     "units": "Regular", "league": {"id": 1, "name": "L"},
                     "participants": [
                         {"name": "Home %d" % i, "alignment": "home"},
                         {"name": "Away %d" % i, "alignment": "away"}],
                     "markets": [{"key": "s;0;m", "type": "moneyline",
                                  "period": 0, "status": "open",
                                  "prices": [{"designation": "home",
                                              "price": -120},
                                             {"designation": "away",
                                              "price": 110}]}]})
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": 1_000, "events": recs}, epoch=ep, received_ms=1_000)
    return c


def _seeds(n=300):
    out = []
    for i in range(n):
        j = i * 7
        if i % 5 == 0:                       # a miss: the absence check
            home, away = "Nobody %d" % i, "Noone %d" % i
        else:
            home, away = "Home %d" % j, "Away %d" % j
        out.append({"id": "ev%d" % i, "home_team": home, "away_team": away,
                    "commence_time": "2026-10-09T18:00:00Z",
                    "sport_key": "soccer_epl"})
    return out


def test_a_batch_index_answers_exactly_what_each_scan_answers(monkeypatch):
    from sportsassets import pinnapi_feed as F
    from sportsassets import pinnapi_primary as P
    from sportsassets import pinnapi_reactive as RX

    c = _feed_cache()
    seeds = _seeds()
    scans = [P.match_event(c, e, "soccer") for e in seeds]
    built = []
    real_view = F.fixture_view

    def counting(events):
        built.append(1)
        return real_view(events)
    monkeypatch.setattr(F, "fixture_view", counting)
    monkeypatch.setattr(RX, "ACTIVE", type("A", (), {"cache": c})())
    idx = RX.batch_index()
    assert isinstance(idx, dict)
    batch = [P.match_event(c, e, "soccer", index=idx) for e in seeds]
    assert batch == scans
    assert len(built) == 1, "one fixture view for the whole batch"
    assert sum(1 for h, w in scans if h) > 200 and \
        sum(1 for h, w in scans if w) >= 60


def _absence_records(rng, n=900):
    """Feed records of two sports whose names share tokens, abbreviate to
    each other ("CRB" / "Clube de Regatas Brasil") and fold to the same
    text, at starts near and far from the seeds'. Returns the records and
    the seed vocabulary (half of it never appears in the feed)."""
    syll = ["ba", "ri", "to", "ma", "ne", "lo", "su", "ka", "de", "vi",
            "ro", "pe"]
    vocab = sorted({"".join(rng.choice(syll) for _ in range(3))
                    for _ in range(1200)})[:600]
    feed_words = vocab[:300]
    fixed = ["Clube de Regatas Brasil", "CRB", "Sao Paulo", "SP",
             "Bosnia & Herzegovina", "Bosnia and Herzegovina", "A C Milan",
             "AC Milan", "Atletico Mineiro", "AM"]
    out = []
    for i in range(n):
        def nm():
            if rng.random() < 0.05:
                return rng.choice(fixed)
            return " ".join(rng.choice(feed_words).title()
                            for _ in range(rng.randint(1, 3)))
        hour = rng.choice([17, 18, 19, 23]) if i % 7 else rng.choice([2, 5])
        out.append({"id": 50_000 + i, "sport_id": 29 if i % 5 else 1,
                    "startTime": "2026-10-%02dT%02d:00:00Z"
                    % (9 + (i % 3 == 0), hour),
                    "participants": [{"alignment": "home", "name": nm()},
                                     {"alignment": "away", "name": nm()}]})
    seeds = [w.title() for w in vocab] + fixed
    return out, seeds


def test_the_absence_index_answers_exactly_what_the_scan_answers():
    """(RC6) `absence` with a prepared AbsenceIndex returns the scan's
    dict, verbatim, for seeds that share tokens, acronyms and folded text
    with the feed, and for seeds the feed never names -- including the
    near-start and elsewhere samples."""
    from sportsassets import pinnapi_names as N
    from sportsassets import pinnapi_primary as P

    rng = random.Random(29)
    records, words = _absence_records(rng)
    prepared = N.AbsenceIndex(records, 29, "soccer")
    start = P.epoch("2026-10-09T18:00:00Z")
    kinds = set()
    for k in range(400):
        home, away = rng.choice(words), rng.choice(words)
        for tol in (None, 5400.0):
            want = N.absence(records, sport_id=29, start=start, home=home,
                             away=away, family="soccer", evicted=k % 3 == 0,
                             tolerance_s=tol)
            got = N.absence(records, sport_id=29, start=start, home=home,
                            away=away, family="soccer", evicted=k % 3 == 0,
                            tolerance_s=tol, prepared=prepared)
            assert got == want, (home, away, tol)
            if tol:
                kinds.add((want["absent"], bool(want["sharing"]),
                           bool(want["named_near_start"]),
                           bool(want["named_elsewhere"])))
    # every shape of answer was exercised
    assert (True, False, False, False) in kinds
    assert (False, True, True, True) in kinds or \
        (False, True, True, False) in kinds
    assert any(not k[0] and not k[1] for k in kinds)   # evicted, no sharing
    assert any(k[3] and not k[2] for k in kinds) or \
        any(k[1] and not k[2] for k in kinds)


class _Counting:
    """unicodedata, with its NFKD normalisation counted."""

    def __init__(self):
        import unicodedata
        self.real, self.n = unicodedata, 0

    def normalize(self, form, text):
        self.n += 1
        return self.real.normalize(form, text)

    def combining(self, c):
        return self.real.combining(c)


def _fold_reference(value) -> str:
    """The fold as it was written before RC6 (pinnapi_primary.name)."""
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(t for t in re.findall(r"[^\W_]+", text.replace("&", " "))
                    if t != "and")


def test_a_name_is_folded_once_per_text_and_answers_as_before(monkeypatch):
    """(RC6) fixture_index folds both names of every cached fixture for every
    registration batch, on the loop: the fold is now computed once per text
    (a bounded memo), and answers exactly what the fold answered."""
    import uuid

    from sportsassets import pinnapi_names as N
    from sportsassets import pinnapi_primary as P

    for v in (None, 0, 1, True, False, 1.5, "", "Bosnia & Herzegovina",
              "Bosnia and Herzegovina", "São Paulo FC", "A.C. Milan",
              "Clube_de Regatas", "  Ünïcödé  Ténnis  ", "x" * 300):
        assert P.name(v) == _fold_reference(v), v
        assert N._fold(v) == _fold_reference(v), v
    count = _Counting()
    monkeypatch.setattr(P, "unicodedata", count)
    monkeypatch.setattr(N, "unicodedata", count)
    text = "Club %s de Fútbol" % uuid.uuid4().hex
    first = [P.name(text) for _ in range(5)]
    assert len(set(first)) == 1
    assert count.n == 1, "folded %d times" % count.n
    text2 = "Real %s" % uuid.uuid4().hex
    canon = [N.canonical(text2, "soccer", side="feed") for _ in range(5)]
    assert count.n == 2, "canonicalised with %d folds" % (count.n - 1)
    # every call still gets its own dict and its own rules list
    canon[0]["rules"].append("MUTATED")
    canon[0]["name"] = "changed"
    again = N.canonical(text2, "soccer", side="feed")
    assert again == canon[1] and "MUTATED" not in again["rules"]
    assert again is not canon[1] and again["rules"] is not canon[1]["rules"]


def test_canonical_answers_as_before_for_every_family_and_scope():
    from sportsassets import pinnapi_names as N

    names = ["Clube de Regatas Brasil", "Ohio St", "Ohio State Buckeyes",
             "Man Utd", "Manchester United FC", "A C Milan", "Real Madrid CF",
             "Bosnia & Herzegovina", None, 7]
    for fam in (None, "soccer", "football", "basketball"):
        for key in (None, "soccer_epl", "soccer_uefa_nations_league", 5):
            for side in ("provider", "feed"):
                for v in names:
                    got = N.canonical(v, fam, key, side=side)
                    nm, rules = N._canonical(str(v or ""), fam, key, side)
                    assert got == {"name": nm, "rules": list(rules)}


def _named_cache(prefix, n_events=2600):
    from sportsassets import pinnapi_feed as F
    c = F.FeedCache()
    c.offload_snapshots = False
    ep = c.new_connection([("prematch", 1)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": 1_000, "events": [
                 {"id": 90_000 + i, "type": "matchup",
                  "startTime": "2026-10-09T18:00:00Z", "isLive": False,
                  "participants": [
                      {"name": "%s Home %d" % (prefix, i),
                       "alignment": "home"},
                      {"name": "%s Away %d" % (prefix, i),
                       "alignment": "away"}],
                  "markets": [{"key": "s;0;m", "type": "moneyline",
                               "period": 0, "status": "open",
                               "prices": [{"designation": "home",
                                           "price": -120},
                                          {"designation": "away",
                                           "price": 110}]}]}
                 for i in range(n_events)]}, epoch=ep, received_ms=1_000)
    return c


def test_a_batch_folds_its_cold_names_on_the_lane_before_its_index(
        monkeypatch):
    """(RC6) The first batch after a boot found every name cold and folded
    5,200 of them on the loop inside fixture_index. warm_names folds them on
    the CPU lane first; the index the batch then builds on the loop folds
    nothing, and answers what it answered before."""
    import uuid

    from sportsassets import pinnapi_names as N
    from sportsassets import pinnapi_primary as P
    from sportsassets import pinnapi_reactive as RX

    c = _named_cache("Cold%s" % uuid.uuid4().hex[:8])
    monkeypatch.setattr(RX, "ACTIVE", type("A", (), {"cache": c})())
    want = P.fixture_index(c)          # the scan's answer (folds on loop)
    for fn in (P._name_of_text, N._fold_text, N._canonical_memo,
               N._tokens, N._acronym):
        fn.cache_clear()                # a fresh process: every name cold
    monkeypatch.setattr(P, "_WARM", set())
    ran_on = []
    real = P.warm_names

    def spy(pairs):
        ran_on.append(threading.current_thread().name)
        return real(pairs)
    monkeypatch.setattr(P, "warm_names", spy)
    assert asyncio.run(RX.warm_names()) == 2 * 2600
    assert ran_on and all(n.startswith("api-cpu") for n in ran_on), ran_on
    count = _Counting()
    monkeypatch.setattr(P, "unicodedata", count)
    monkeypatch.setattr(N, "unicodedata", count)
    got = RX.batch_index()
    assert count.n == 0, "the batch folded %d names on the loop" % count.n
    assert {k: v for k, v in got.items() if k != P.RAW} == \
        {k: v for k, v in want.items() if k != P.RAW}
    # warm now: the next batch has nothing cold, and no lane job is asked
    ran_on.clear()
    assert asyncio.run(RX.warm_names()) == 0 and not ran_on


def test_a_batch_with_few_cold_names_folds_them_where_it_is(monkeypatch):
    import uuid

    from sportsassets import pinnapi_reactive as RX
    c = _named_cache("Few%s" % uuid.uuid4().hex[:8],
                     n_events=RX.WARM_COLD_NAMES_MIN // 2 - 1)
    monkeypatch.setattr(RX, "ACTIVE", type("A", (), {"cache": c})())
    assert asyncio.run(RX.warm_names()) == 0


def test_the_cycle_warms_each_batch_before_its_index():
    """Source pin: each of the two batches awaits warm_names right before
    it builds its index (an await BEFORE the batch, never inside it)."""
    import inspect
    import re

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L)
    pairs = re.findall(r"await reactive\.warm_names\(\)\s*\n\s*"
                       r"(_idx = reactive\.batch_index\(\) if events|"
                       r"_def_idx = reactive\.batch_index\(\))", src)
    assert len(pairs) == 2, pairs


def test_the_cycle_registers_its_back_to_back_seeds_with_one_index():
    """Source pin beside the behaviour above: both of the ext_pinnacle
    cycle's synchronous seed batches (the discovery refresh and the deferred
    events of a fetch) build ONE batch index and pass it to every register;
    the per-event register inside the awaiting loop keeps its own scan."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L)
    assert src.count("reactive.batch_index()") == 2
    assert "index=_idx)" in src and "index=_def_idx)" in src


def test_no_index_survives_an_await_in_the_batches():
    """The index is only valid while the cache cannot change: no await may
    sit between building it and the last register of its batch."""
    import inspect
    import re

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L)
    for start, stop in (("_idx = reactive.batch_index()",
                         'r["discovery"] = "DISCOVERY_SEEDS_REFRESHED"'),
                        ("_def_idx = reactive.batch_index()",
                         "for _k in range(_i, len(events)):")):
        a = src.index(start)
        b = src.index(stop, a)
        assert re.search(r"\bawait\b", src[a:b]) is None, start
