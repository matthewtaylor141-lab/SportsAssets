"""FILL lane 0a (2026-09-08) -- THE CANDIDATE'S SIDE BAND IS A RAIL.

The candidate's side band -- how far the ask may sit from his price at
the open before the admission clause refuses `side_band` -- was a bare
environment read in the live worker (`rules._env_float("LIVE_SIDE_PRICE_BAND")`,
0.15 when unset), so a value above 0.15 in the environment WIDENED the
open's price band: the one rail on the money path a shell could raise.
Now `rules.LIVE_SIDE_PRICE_BAND_MAX = capped_env("LIVE_SIDE_PRICE_BAND",
0.15, floor=0.0)`: 0.15 is the ceiling, the environment may only lower it,
junk lands on 0.15, and `_tick_candidate` reads the constant. No behaviour
changes at the default; the 054 refusal row's `band` column keeps printing
the band the tick admitted on.

The pins: the constant and its call site (source), the rail's arithmetic
on the env (0.10 honoured, 0.30 -> 0.15, junk -> 0.15, negative -> 0.0),
the worker refusing `side_band` under a LOWERED band on an ask that the
default admits, an ask 0.20 over his price refused under an env of 0.30
(the raise ignored), and Martinez 534's shape (task 71; his 0.61, the
market 0.81 / 0.82) still refused `side_band` at the default with the
054 row's band 0.15. The constant is read at import, so the env pins set
the constant to what capped_env reads (the test_e12_catchup_side idiom).
"""
from __future__ import annotations

import inspect

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e19_smaller_reading import _martinez
from tests.test_mirror_live_worker import M, NOW, _armed  # noqa: F401 -- the fixture
from tests.test_mirror_live_worker import _census, _places, _pool, _rails_2026_09_06, _tick, _Venue
from tests.test_mirror_shadow import _fill

DEFAULT = 0.15


def test_fill_0a_the_band_is_a_capped_env_rail_with_the_default_as_its_ceiling():
    assert rules.LIVE_SIDE_PRICE_BAND_MAX == DEFAULT
    src = inspect.getsource(rules)
    assert 'LIVE_SIDE_PRICE_BAND_MAX = capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0)' in src
    assert src.count('capped_env("LIVE_SIDE_PRICE_BAND"') == 1 and "LIVE_SIDE_PRICE_BAND_MAX" in rules.__all__
    # the worker reads the constant and no longer the bare environment
    wsrc = inspect.getsource(ml._tick_candidate)
    assert "band = float(rules.LIVE_SIDE_PRICE_BAND_MAX)" in wsrc and 'd["band"] = band' in wsrc
    assert '_env_float("LIVE_SIDE_PRICE_BAND")' not in inspect.getsource(ml)
    assert 'os.getenv("LIVE_SIDE_PRICE_BAND"' not in inspect.getsource(ml)


def test_fill_0a_the_environment_may_only_lower_the_band():
    with pytest.MonkeyPatch.context() as mp:
        for raw, want in (("0.10", 0.10), ("0.15", 0.15), ("0.30", 0.15), ("2.0", 0.15), ("junk", 0.15),
                          ("inf", 0.15), ("nan", 0.15), ("", 0.15), ("-1", 0.0), ("0", 0.0), ("0.05", 0.05)):
            mp.setenv("LIVE_SIDE_PRICE_BAND", raw)
            assert rules.capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0) == want, raw
        mp.delenv("LIVE_SIDE_PRICE_BAND", raising=False)
        assert rules.capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0) == DEFAULT


def test_fill_0a_the_constant_itself_reads_the_environment_at_import_and_only_downward():
    """The review's MEDIUM (mutants W2 / W6): the pins above set the constant by hand,
    so a rules module whose floor equalled its default, or whose env name was not
    LIVE_SIDE_PRICE_BAND, passed every behavioural pin. This one imports the module in
    a fresh interpreter under the env and reads the constant it actually built."""
    import json
    import os
    import subprocess
    import sys

    code = ("import json, sys; from sportsassets.analytics import mirror_live_rules as r;"
            " print(json.dumps(r.LIVE_SIDE_PRICE_BAND_MAX))")
    for raw, want in (("0.10", 0.10), ("0.30", 0.15), ("wide", 0.15), ("-1", 0.0), (None, 0.15)):
        env = {k: v for k, v in os.environ.items() if k != "LIVE_SIDE_PRICE_BAND"}
        if raw is not None:
            env["LIVE_SIDE_PRICE_BAND"] = raw
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert json.loads(out.stdout.strip()) == want, (raw, out.stdout, out.stderr[-300:])


def _band_world(monkeypatch, ask, his_px=0.61, bid=None):
    """His 300 at `his_px` (a fresh fill), the venue's ask at `ask`; the
    per-market read agrees with his fills so only the band decides."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", 300.0, his_px, NOW - 10)])
    return p, _Venue(bid=(ask - 0.01) if bid is None else bid, ask=ask)


def test_fill_0a_env_0_10_is_honoured_an_ask_12c_over_his_price_refuses_side_band(monkeypatch):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LIVE_SIDE_PRICE_BAND", "0.10")
        band = rules.capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0)
        assert band == 0.10
        mp.setattr(rules, "LIVE_SIDE_PRICE_BAND_MAX", band)
        p, v = _band_world(monkeypatch, ask=0.73)             # 0.12 over his 0.61: inside 0.15, past 0.10
        st = _tick(p, v)
        assert _census(st, "side_band") >= 1 and not p.books and not _places(v)
        assert [r["refusal"] for r in p.cand_refusals] == ["side_band"]
        assert p.cand_refusals[0]["band"] == 0.10
    # the same ask at the default opens the book: the lowered band was the refusal
    p2, v2 = _band_world(monkeypatch, ask=0.73)
    st2 = _tick(p2, v2)
    assert _census(st2, "side_band") == 0 and len(p2.books) == 1


def test_fill_0a_env_0_30_lands_on_0_15_an_ask_20c_over_his_price_still_refuses(monkeypatch):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LIVE_SIDE_PRICE_BAND", "0.30")
        band = rules.capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0)
        assert band == DEFAULT, "the raise is ignored"
        mp.setattr(rules, "LIVE_SIDE_PRICE_BAND_MAX", band)
        p, v = _band_world(monkeypatch, ask=0.81)             # 0.20 over his 0.61
        st = _tick(p, v)
        assert _census(st, "side_band") >= 1 and not p.books and not _places(v)
        assert p.cand_refusals[0]["band"] == DEFAULT


def test_fill_0a_junk_in_the_environment_is_the_default(monkeypatch):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LIVE_SIDE_PRICE_BAND", "wide")
        band = rules.capped_env("LIVE_SIDE_PRICE_BAND", 0.15, floor=0.0)
        assert band == DEFAULT
        mp.setattr(rules, "LIVE_SIDE_PRICE_BAND_MAX", band)
        p, v = _band_world(monkeypatch, ask=0.77)             # 0.16 over: refused at 0.15
        st = _tick(p, v)
        assert _census(st, "side_band") >= 1 and not p.books
        p2, v2 = _band_world(monkeypatch, ask=0.75)           # 0.14 over: admitted at 0.15
        st2 = _tick(p2, v2)
        assert _census(st2, "side_band") == 0 and len(p2.books) == 1


def test_fill_0a_martinez_534s_shape_still_refuses_side_band_at_the_default(monkeypatch):
    """Task 71: his 0.61 against a market at 0.81 / 0.82 (the reopen after
    the 12:10Z flip close was refused `drift`, then `side_band`). Here the
    venue's per-market read agrees with his fills so the drift clause
    stands aside and the band alone refuses; the 054 row carries 0.15."""
    p, v, http = _martinez(monkeypatch, bid=0.81, ask=0.82, net=25_104.1)
    st = _tick(p, v, http=http)
    assert _census(st, "side_band") >= 1 and not p.books and not _places(v)
    row = p.cand_refusals[0]
    assert row["refusal"] == "side_band" and row["band"] == DEFAULT and row["ask"] == 0.82
    assert row["his_px"] == 0.61
    assert rules.LIVE_SIDE_PRICE_BAND_MAX == DEFAULT, "the pins above restored the constant"
