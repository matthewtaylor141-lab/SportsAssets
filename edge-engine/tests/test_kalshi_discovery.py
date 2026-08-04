"""Kalshi discovery must prove what bet a market is before listing it.

The 2026-08-04 pricing audit found Kalshi discovery ran on canonical_outcome
alone — no line gate, no segment tag. Two live hazards:

* a total whose subtitle dropped the number ("Over") matches ANY sharp rung
  downstream — pair matching lets point-less sides through and the lowest
  alternate rung wins, so the "edge" is the gap between rungs;
* a spread-series outcome that parses as a plain team name gets priced
  against the MONEYLINE fair value — a different bet wearing the team's name.

Polymarket US closed both via bet_identity at discovery; Kalshi has no slug
grammar, so its gate reads the parsed outcome + series ticker instead.
"""

from edge.venues.kalshi import KalshiAdapter


class _Resp:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events, "cursor": ""}


class _Sess:
    def __init__(self, events):
        self._events = events

    def get(self, url, params=None, timeout=None):
        return _Resp(self._events)


def _discover(events, series):
    a = KalshiAdapter()
    a._sess = _Sess(events)
    out = []
    a.last_census = {}
    a._discover_series(out, "nfl", series)
    return a, out


def _event(title, markets):
    return {"event_ticker": "EVT", "title": title,
            "markets": [{"ticker": t, "yes_sub_title": sub, "title": mt}
                        for t, sub, mt in markets]}


def test_moneyline_outcomes_pass_through():
    _, out = _discover([_event("Eagles vs Cowboys", [
        ("T1", "Eagles", "Eagles vs Cowboys"),
        ("T2", "Cowboys", "Eagles vs Cowboys"),
    ])], "KXNFLGAME")
    assert len(out) == 1
    assert set(out[0].outcome_tokens.values()) == {"T1", "T2"}


def test_a_total_without_its_number_is_refused():
    a, out = _discover([_event("Eagles vs Cowboys: Total Points", [
        ("T1", "Over", "Eagles vs Cowboys: Total Points"),
        ("T2", "Under", "Eagles vs Cowboys: Total Points"),
    ])], "KXNFLGAME")
    assert out == []
    assert a.last_census.get("bare_total") == 2


def test_a_spread_series_outcome_missing_its_line_is_refused():
    a, out = _discover([_event("Eagles vs Cowboys Spread", [
        ("T1", "Eagles", "Eagles vs Cowboys Spread"),
        ("T2", "Cowboys -7.5", "Eagles vs Cowboys Spread"),
    ])], "KXNFLSPREAD")
    # The lined side alone is < 2 outcomes, so nothing lists — better no
    # market than one priced against the wrong fair value.
    assert out == []
    assert a.last_census.get("untagged_spread") == 1


def test_lined_spreads_and_totals_keep_their_lines():
    _, out = _discover([_event("Eagles vs Cowboys Spread", [
        ("T1", "Eagles -7.5", "Eagles vs Cowboys Spread"),
        ("T2", "Cowboys +7.5", "Eagles vs Cowboys Spread"),
    ])], "KXNFLSPREAD")
    assert len(out) == 1
    keys = set(out[0].outcome_tokens)
    assert any("-7.5" in k for k in keys)


def test_segment_titles_are_tagged_so_halves_never_meet_full_game():
    _, out = _discover([_event("Eagles vs Cowboys 1st Half", [
        ("T1", "Eagles", "Eagles vs Cowboys 1st Half"),
        ("T2", "Cowboys", "Eagles vs Cowboys 1st Half"),
    ])], "KXNFLGAME")
    assert len(out) == 1
    assert all(k.startswith("[h1] ") for k in out[0].outcome_tokens)


# ── PEM normalization: env-var pastes must not brick auth ────────────────

def _fresh_rsa_pem() -> str:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                             NoEncryption()).decode()


def test_pem_loads_in_every_paste_format(monkeypatch):
    pem = _fresh_rsa_pem()
    adapter = KalshiAdapter()
    for variant in (
        pem,                                   # proper multi-line
        pem.replace("\n", "\\n"),              # escaped newlines
        pem.replace("\n", " ").strip(),        # newlines -> spaces
        pem.replace("\n", ""),                 # newlines stripped
        f'"{pem}"',                            # quoted paste
    ):
        monkeypatch.setenv("EDGE_KALSHI_PRIVATE_KEY", variant)
        assert adapter._private_key() is not None
