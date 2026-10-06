"""P1: THE PMX INSTITUTIONAL CREDENTIAL, THE STREAM AS THE HELD-MARK PRIMARY,
KALSHI ISOLATION AND THE FRESHNESS TELEMETRY. Fakes only; no network.

ROOT CAUSE (production, release 221ce6b). `institutional_stream.start_default`
-> `market_data_identity.guard(PMX)` returned
MARKET_DATA_CREDENTIAL_IS_THE_FUNDED_KEY: on 2026-10-06 (between the
22:49Z 10-05 and 03:46Z 10-06 env-key readbacks) PMUS_KEY_ID / PMUS_SECRET_KEY
changed from the retail shape (36-char UUID id / 88-char Ed25519 secret) to
the PMX shape (32 chars = PMX_CLIENT_ID's length / ~1,678 chars = the PMX RSA
key's length + armour). By the guard's own comparisons the ONLY equality the
lengths allow is PMX_CLIENT_ID == PMUS_KEY_ID. The rule is right and is NOT
relaxed: a market-data credential equal to the funded key is refused.

  §1  the guard: a distinct, orderless PMX credential is ACCEPTED in the
      API's production shape (retail execution + funded + edge keys present);
      the incident shape is REFUSED and `diagnose` names the colliding env
      variables (never a value); equality is decided on normalised forms
      (UUID dashes / case / whitespace; raw PEM vs base64-of-PEM); the PMUS
      funded / execution protections (guard_key_pair) still refuse
  §2  the start: the refusal's detail travels in start_default and the
      freshness telemetry; a distinct credential starts
  §3  held symbols: the API stream's held budget (MAX_SYMBOLS +
      HELD_SYMBOL_BUDGET = institutional_stream.MAX_SYMBOLS); held markets
      beyond the focus bound are bootstrapped (bounded per pass, backlog
      carried) and subscribed
  §4  the held-mark same-book tap: a held REST read IS the probe's retail
      read -- the read is returned unchanged, a sample is pending, nothing
      is added for a non-held / non-exact / tap-off read, an exception is
      not retried, the public lane's pace is taken once
  §5  Kalshi isolation: the PMUS owner refuses a Kalshi ticker before any
      cache / queue / pace / request and never registers it as held; no
      market-data / freshness module imports Kalshi and no Kalshi module
      imports the PMUS pacing, gate, cache or freshness modules; KALSHI_* is
      never a market-data candidate
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import pathlib
import threading
import time

import pytest

from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_focus_universe as FU
from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets import market_data_identity as MDI
from sportsassets import paper_market_data as PMD

try:
    from tests.test_institutional_contract_map import AEC, SLUG
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"

RETAIL_KID = "11111111-2222-3333-4444-555555555555"
RETAIL_SEC = base64.b64encode(b"r" * 64).decode()          # Ed25519-shaped
FUNDED_KID = "99999999-8888-7777-6666-555555555555"
FUNDED_SEC = base64.b64encode(b"f" * 64).decode()
EDGE_KID = "77777777-8888-7777-6666-555555555555"
PMX_CID = "pmxclientidnotreal0123456789abcd"                 # 32 chars
PMX_KID = "pmx-kid-not-real-12345"
PEM = ("-----BEGIN PRIVATE KEY-----\n"
       + base64.b64encode(b"NOT-A-REAL-RSA-KEY-" * 20).decode() +
       "\n-----END PRIVATE KEY-----\n")
PMX_PK = base64.b64encode(PEM.encode()).decode()             # base64-of-PEM
PMX_PART = "firms/test-firm/users/not-a-real-participant"
VALUES = (RETAIL_SEC, FUNDED_SEC, PMX_PK, PMX_CID, PMX_KID, PMX_PART,
          RETAIL_KID, FUNDED_KID, PEM)


def api_env(**extra):
    """sportsassets-api as it is provisioned now, by NAME: PMX_* + the
    retail execution key + the funded key + the edge key + the stream flag."""
    env = {"INSTITUTIONAL_MD_STREAM": "on",
           "PMX_CLIENT_ID": PMX_CID, "PMX_KEY_ID": PMX_KID,
           "PMX_PRIVATE_KEY_B64": PMX_PK, "PMX_PARTICIPANT_ID": PMX_PART,
           "PMUS_EXECMIRROR_KEY_ID": RETAIL_KID,
           "PMUS_EXECMIRROR_SECRET_KEY": RETAIL_SEC,
           "PMUS_KEY_ID": FUNDED_KID, "PMUS_SECRET_KEY": FUNDED_SEC,
           "EDGE_PMUS_KEY_ID": EDGE_KID,
           "EDGE_PMUS_SECRET_KEY": base64.b64encode(b"e" * 64).decode()}
    env.update(extra)
    return env


def incident_env():
    """The production collision: the funded slot holds the PMX identity --
    the client id as PMUS_KEY_ID, the same RSA key as raw PEM."""
    return api_env(PMUS_KEY_ID=PMX_CID, PMUS_SECRET_KEY=PEM)


def no_leak(obj):
    blob = json.dumps(obj, default=str)
    for v in VALUES:
        assert v not in blob, "a credential value leaked"


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    IAS.reset()
    PMD.reset()
    FU.note_held_first([])
    PMD._TAP["tap"] = None
    yield
    IS.reset()
    IAS.reset()
    PMD.reset()
    FU.note_held_first([])
    PMD._TAP["tap"] = None


# ── §1 the guard ─────────────────────────────────────────────────────────

def test_a_distinct_orderless_pmx_is_accepted_in_the_api_shape():
    env = api_env()
    assert MDI.guard(MDI.PMX, env=env) is None
    p = MDI.pmx_candidate(env)
    assert p["distinct_from_funded"] is True
    assert p["distinct_from_retail_execution"] is True
    assert p["distinct_from_funded_basis"] == \
        "COMPARED_BY_VALUE_IN_THIS_PROCESS"
    inv = MDI.inventory(env)
    assert inv["institutional_market_data_credential"] == MDI.PMX
    assert inv["pmx_guard"]["refusal"] is None
    assert inv["pmx_guard"]["matches"] == []
    assert inv["pmx_guard"]["execution_slot_shapes"] == {
        "retail_execution": MDI.SHAPE_PMUS_RETAIL,
        "funded": MDI.SHAPE_PMUS_RETAIL}
    no_leak(inv)


def test_the_production_collision_is_accepted_and_named_by_env_name():
    """Production: the funded PMUS slot holds the PMX Auth0 client (same id,
    same RSA key). That slot is not a PMUS execution identity (PMUS signs only
    with an Ed25519 API key), and the PMX credential is orderless in this
    build: the PMX market-data stream is ACCEPTED, and the misprovisioned slot
    is NAMED (non_pmus_slot_collisions), never hidden."""
    env = incident_env()
    assert MDI.guard(MDI.PMX, env=env) is None
    d = MDI.diagnose(MDI.PMX, env=env)
    assert d["non_pmus_slot_collisions"] == ["funded"]
    assert d["non_pmus_slot_basis"] == MDI.PMX_NON_PMUS_SLOT_BASIS
    pairs = {(m["candidate_env"], m["execution_env"], m["basis"])
             for m in d["matches"]}
    assert ("PMX_CLIENT_ID", "PMUS_KEY_ID", "IDENTIFIER_EQUAL") in pairs
    # the same RSA key, raw PEM in one variable and base64-of-PEM in the
    # other: one secret
    assert ("PMX_PRIVATE_KEY_B64", "PMUS_SECRET_KEY", "SECRET_EQUAL") in pairs
    assert all(m["identity"] == "funded" for m in d["matches"])
    assert d["execution_slot_shapes"]["funded"] == MDI.SHAPE_RSA_PEM
    assert d["execution_slot_shapes"]["retail_execution"] == \
        MDI.SHAPE_PMUS_RETAIL
    assert MDI.refusal_why(MDI.PMX, env=env) is None
    no_leak(d)
    no_leak(MDI.inventory(env))


def test_a_pmx_collision_with_a_pmus_shaped_slot_still_refuses():
    """The narrow rule never weakens a PMUS execution protection: if the
    colliding slot holds a REAL PMUS API key shape, the collision refuses."""
    env = incident_env()
    pmus_id = api_env()["PMUS_KEY_ID"]
    pmus_sec = api_env()["PMUS_SECRET_KEY"]
    # the PMX client id equal to a PMUS-shaped funded key -> refused
    env2 = dict(env, PMUS_KEY_ID=pmus_id, PMUS_SECRET_KEY=pmus_sec,
                PMX_CLIENT_ID=pmus_id)
    assert MDI.guard(MDI.PMX, env=env2) == MDI.G_IS_FUNDED
    env3 = dict(env, PMUS_EXECMIRROR_KEY_ID=pmus_id,
                PMUS_EXECMIRROR_SECRET_KEY=pmus_sec, PMX_KEY_ID=pmus_id)
    assert MDI.guard(MDI.PMX, env=env3) == MDI.G_IS_RETAIL_EXECUTION
    # the PMUS market-data candidate gets no exemption at all
    env4 = dict(api_env(), PMUS_MD_KEY_ID=api_env()["PMUS_KEY_ID"],
                PMUS_MD_SECRET_KEY=api_env()["PMUS_SECRET_KEY"])
    assert MDI.guard(MDI.PMUS_MD, env=env4) in (MDI.G_IS_FUNDED,
                                               MDI.G_IS_RETAIL_EXECUTION)


def test_the_pmx_credential_is_orderless_in_this_build():
    """The narrow rule rests on these: no production PMX host in the only PMX
    order-capable adapter (pre-production), and funded submission off."""
    from sportsassets import bettor_funded_execution as FX
    assert FX.FUNDED_SUBMISSION_ENABLED is False
    src = (MDI.__file__.rsplit("/", 1)[0] + "/pmx.py")
    text = open(src).read()
    assert "THERE IS NO PRODUCTION HOST IN THIS MODULE" in text


def test_a_secret_collision_with_a_non_pmus_slot_is_named_not_refused():
    env = api_env(PMUS_SECRET_KEY=PEM)
    # a PEM in the funded secret slot is not a PMUS API key: named, accepted
    assert MDI.pmx_candidate(env)["distinct_from_funded_basis"] == \
        "SECRET_EQUAL"
    assert MDI.guard(MDI.PMX, env=env) is None
    assert MDI.diagnose(MDI.PMX, env=env)["non_pmus_slot_collisions"] == [
        "funded"]


@pytest.mark.parametrize("variant", [
    lambda v: v.upper(), lambda v: v.replace("-", ""),
    lambda v: " %s " % v, lambda v: v[:8] + " " + v[8:]])
def test_identifier_equality_survives_case_dashes_and_whitespace(variant):
    env = api_env(PMX_KEY_ID=variant(FUNDED_KID))
    assert MDI.guard(MDI.PMX, env=env) == MDI.G_IS_FUNDED
    env = api_env(PMX_CLIENT_ID=variant(RETAIL_KID))
    assert MDI.guard(MDI.PMX, env=env) == MDI.G_IS_RETAIL_EXECUTION


@pytest.mark.parametrize("encode", [
    lambda pem: pem,                                       # raw PEM
    lambda pem: pem.replace("\n", "\\n"),                  # escaped newlines
    lambda pem: base64.b64encode(pem.encode()).decode(),   # base64-of-PEM
    lambda pem: "".join(pem.split("\n")[1:-2]),            # the bare body
])
def test_secret_equality_survives_its_encoding(encode):
    """The SAME key in any encoding is detected as equal (diagnose names it);
    the slot holding a PEM is not a PMUS API key, so the PMX candidate is not
    refused for it -- it is named as a non-PMUS slot collision."""
    env = api_env(PMUS_EXECMIRROR_SECRET_KEY=encode(PEM))
    d = MDI.diagnose(MDI.PMX, env=env)
    assert ("PMX_PRIVATE_KEY_B64", "PMUS_EXECMIRROR_SECRET_KEY",
            "SECRET_EQUAL") in {(m["candidate_env"], m["execution_env"],
                                 m["basis"]) for m in d["matches"]}
    pem_slot = MDI.slot_shape(env["PMUS_EXECMIRROR_KEY_ID"],
                              env["PMUS_EXECMIRROR_SECRET_KEY"]) == \
        MDI.SHAPE_RSA_PEM
    if pem_slot:      # positively the PMX credential class: named, accepted
        assert d["non_pmus_slot_collisions"] == ["retail_execution"]
        assert MDI.guard(MDI.PMX, env=env) is None
    else:             # an unrecognised shape (a bare body): still refused
        assert d["non_pmus_slot_collisions"] == []
        assert MDI.guard(MDI.PMX, env=env) == MDI.G_IS_RETAIL_EXECUTION


def test_a_peer_fingerprint_of_the_pmx_client_is_refused():
    env = {k: v for k, v in api_env().items() if not k.startswith("PMUS_")}
    peers = {"funded": MDI.fingerprint(PMX_CID)}
    assert MDI.guard(MDI.PMX, env=env, peer_fingerprints=peers) == \
        MDI.G_IS_FUNDED
    d = MDI.diagnose(MDI.PMX, env=env, peer_fingerprints=peers)
    assert d["matches"][0]["basis"] == "FINGERPRINT_EQUAL_TO_PEER_REPORT"


def test_two_different_secrets_are_not_made_equal_by_normalisation():
    other = ("-----BEGIN PRIVATE KEY-----\n"
             + base64.b64encode(b"ANOTHER-KEY-ALTOGETHER" * 20).decode()
             + "\n-----END PRIVATE KEY-----\n")
    assert MDI.guard(MDI.PMX, env=api_env(PMUS_SECRET_KEY=other)) is None
    # strings that differ only in characters outside the base64 alphabet are
    # NOT decoded leniently into one value
    a, b = "abcd-efgh-ijkl-mnop-qrst", "abcdefghijklmnopqrst"
    assert not ({x for x in MDI._secret_forms(a) if isinstance(x, bytes)}
                & {x for x in MDI._secret_forms(b) if isinstance(x, bytes)})


def test_the_pmus_funded_and_execution_protections_are_unchanged():
    env = api_env()
    assert MDI.guard_key_pair("md-kid", "md-secret", env=env) is None
    assert MDI.guard_key_pair(RETAIL_KID, "x", env=env) == \
        MDI.G_IS_RETAIL_EXECUTION
    assert MDI.guard_key_pair(FUNDED_KID, "x", env=env) == MDI.G_IS_FUNDED
    assert MDI.guard_key_pair("x", FUNDED_SEC, env=env) == MDI.G_IS_FUNDED
    assert MDI.guard_key_pair("x", RETAIL_SEC, env=env) == \
        MDI.G_IS_RETAIL_EXECUTION
    assert MDI.guard_key_pair("", "x", env=env) == MDI.G_ABSENT
    # ... and now also by normalised form
    assert MDI.guard_key_pair(FUNDED_KID.upper(), "x", env=env) == \
        MDI.G_IS_FUNDED
    assert MDI.guard_key_pair("x", " %s\n" % RETAIL_SEC, env=env) == \
        MDI.G_IS_RETAIL_EXECUTION
    env = api_env(PMUS_SECRET_KEY=PEM)
    assert MDI.guard_key_pair("x", PMX_PK, env=env) == MDI.G_IS_FUNDED
    # a PMUS_MD key equal to an execution key is refused exactly as before
    env = api_env(PMUS_MD_KEY_ID=RETAIL_KID, PMUS_MD_SECRET_KEY="x")
    assert MDI.guard(MDI.PMUS_MD, env=env) == MDI.G_IS_RETAIL_EXECUTION


def test_slot_shape_is_an_enum_never_a_value():
    assert MDI.slot_shape("", "") == MDI.SHAPE_ABSENT
    assert MDI.slot_shape(FUNDED_KID, FUNDED_SEC) == MDI.SHAPE_PMUS_RETAIL
    assert MDI.slot_shape(PMX_CID, PEM) == MDI.SHAPE_RSA_PEM
    assert MDI.slot_shape(PMX_CID, PMX_PK) == MDI.SHAPE_RSA_PEM
    assert MDI.slot_shape("x", "y") == MDI.SHAPE_OTHER


def test_kalshi_keys_are_never_a_market_data_candidate_or_identity():
    env = api_env(KALSHI_API_KEY_ID=PMX_CID, KALSHI_PRIVATE_KEY_PEM=PEM)
    inv = MDI.inventory(env)
    assert {c["candidate"] for c in inv["candidates"]} == {MDI.PMX,
                                                          MDI.PMUS_MD}
    assert set(inv["execution_identities_in_this_process"]) == {
        "retail_execution", "funded", "edge_shadow"}
    assert MDI.guard(MDI.PMX, env=env) is None
    assert "kalshi" not in json.dumps(inv).lower()


# ── §2 the start and its telemetry ──────────────────────────────────────

def test_start_default_refuses_a_pmus_shaped_collision_with_the_names():
    pmus_id, pmus_sec = api_env()["PMUS_KEY_ID"], api_env()["PMUS_SECRET_KEY"]
    env = dict(incident_env(), PMUS_KEY_ID=pmus_id, PMUS_SECRET_KEY=pmus_sec,
               PMX_CLIENT_ID=pmus_id)
    got = IS.start_default(env=env, available=lambda: (True, None),
                           transport_factory=lambda b, t: pytest.fail(
                               "no transport for a refused credential"))
    assert got["started"] is False
    assert got["state"] == IS.S_CREDENTIAL
    assert got["why"] == MDI.G_IS_FUNDED
    assert "PMX_CLIENT_ID==PMUS_KEY_ID" in got["detail"]
    no_leak(got)
    inst = PMD.stream_updates()["institutional"]
    assert inst["running"] is False
    assert inst["state"] == IS.S_CREDENTIAL
    assert inst["why"] == MDI.G_IS_FUNDED
    assert inst["updates"] is None and inst["book_updates"] is None
    no_leak(inst)


class _Idle:
    def __init__(self, books, token_fn):
        self.books = books

    def start(self):
        pass

    def stop(self):
        pass

    def subscribe(self, symbols):
        pass


def test_a_distinct_credential_starts_in_the_api_shape():
    got = IS.start_default(env=api_env(), available=lambda: (True, None),
                           token_fn=lambda: "tok", transport_factory=_Idle)
    assert got["started"] is True and got["state"] == IS.S_IDLE
    assert "detail" not in got


def test_the_telemetry_counts_book_updates_and_held_subscriptions():
    IS.start_default(env=api_env(), available=lambda: (True, None),
                     token_fn=lambda: "tok", transport_factory=_Idle)
    IS.set_instrument(SLUG, AEC)
    IS.want([SLUG])
    IS.BOOKS.on_connected("grpc-test")
    for _ in range(3):
        IS.BOOKS.on_update({"symbol": SLUG, "bids": [(450, 1000)],
                            "offers": [(470, 500)],
                            "state": "INSTRUMENT_STATE_OPEN",
                            "transact_time": time.time()})
    IS.BOOKS.on_heartbeat()
    inst = PMD.stream_updates()["institutional"]
    assert inst["running"] is True and inst["connected"] is True
    assert inst["book_updates"] == 3 and inst["updates"] == 4
    assert inst["symbols"] == 1 and inst["by_refusal"] == {"OK": 1}
    assert set(inst["api_stream"]) >= {"held_symbol_budget", "held_wanted",
                                       "held_subscribed", "bootstrap_backlog"}


# ── §3 held symbols on the stream ───────────────────────────────────────

def test_the_held_budget_is_inside_the_streams_own_limit():
    assert IAS.MAX_SYMBOLS + IAS.HELD_SYMBOL_BUDGET == IS.MAX_SYMBOLS
    assert IS.MAX_SYMBOLS <= 1000        # documented symbols per stream
    assert FU.HELD_FIRST_MAX == IS.MAX_SYMBOLS
    FU.note_held_first(["m-%03d" % i for i in range(500)])
    assert len(FU.held_first()) == IS.MAX_SYMBOLS


def test_held_markets_beyond_the_focus_bound_are_bootstrapped_and_wanted(
        monkeypatch):
    focus = ["focus-%02d" % i for i in range(IAS.MAX_SYMBOLS)]
    held = ["held-%03d" % i for i in range(60)] + focus[:3]
    FU.note_held_first(held)
    wanted: list = []
    monkeypatch.setattr(IS, "want", lambda syms: wanted.extend(syms))
    monkeypatch.setattr(IS, "set_instrument", lambda s, r: None)
    monkeypatch.setattr(IAS, "_exact_here", lambda s: True)
    reads: list = []

    def bootstrap(client, s):
        reads.append(s)
        return {"record": {"symbol": s}}

    t0 = 1_800_000_000.0
    r = asyncio.run(IAS.refresh_once(bootstrap=bootstrap, symbols=focus,
                                     now=t0, max_bootstraps=40))
    assert r["held_extra"] == 60 and r["wanted"] == IAS.MAX_SYMBOLS + 60
    assert r["bootstrapped"] == 40 and r["backlog"] == 52
    assert reads[:IAS.MAX_SYMBOLS] == focus      # real-money tiers first
    r = asyncio.run(IAS.refresh_once(bootstrap=bootstrap, symbols=focus,
                                     now=t0 + 2, max_bootstraps=40))
    r = asyncio.run(IAS.refresh_once(bootstrap=bootstrap, symbols=focus,
                                     now=t0 + 4, max_bootstraps=40))
    assert r["backlog"] == 0
    assert set(reads) == set(focus) | set(held)
    assert set(wanted) >= set(held)
    d = IAS.describe()
    assert d["held_wanted"] == len(held) and d["held_subscribed"] == len(held)
    assert d["bootstrap_backlog"] == 0


def test_the_held_extra_is_bounded_by_the_budget(monkeypatch):
    FU.note_held_first(["held-%03d" % i for i in range(300)])
    assert len(IAS._held_extra([])) == IAS.HELD_SYMBOL_BUDGET


# ── §4 the held-mark same-book tap ──────────────────────────────────────

def retail_md(bids=(("0.45", "10"),), offers=(("0.47", "5"),)):
    return {"bids": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                     for p, q in bids],
            "offers": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                       for p, q in offers],
            "state": "MARKET_STATE_OPEN"}


def resident(bids=((450, 1000),), offers=((470, 500),)):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(SLUG, AEC)
    b.want([SLUG])
    b.on_connected("grpc-test")
    b.on_update({"symbol": SLUG, "bids": list(bids), "offers": list(offers),
                 "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": time.time()})
    return b


def tap_for(books, *, eligible=lambda s: True):
    return PMD.SameBookTap(eligible=eligible, current=books.current,
                           record_for=lambda s: AEC if s == SLUG else None)


def owner(tap, transport, **kw):
    o = PMD.Owner(transport=transport, recent=lambda s, max_age_s: None,
                  gate=lambda: {"blocking": False}, same_book_tap=tap, **kw)
    o.set_held([SLUG])
    return o


def test_a_held_rest_read_is_a_same_book_sample_and_is_returned_unchanged():
    tap = tap_for(resident())
    calls = []

    def transport(slug, deadline_epoch_s=None):
        calls.append(slug)
        return {"marketData": retail_md(), "error": None,
                "observed_at": time.time(), "marker": "the-read"}
    got = owner(tap, transport).read(SLUG)
    assert calls == [SLUG]                       # exactly one request
    assert got["marker"] == "the-read" and got["served_by"] == "REST"
    rows = tap.drain()
    assert len(rows) == 1
    row = rows[0]
    assert row["verdict"] == SB.V_AGREE and row["within_window"] is True
    assert row["identity_exact"] is True and row["orders_placed"] == 0
    assert row["focus_why"] == PMD.TAP_WHY
    assert tap.telemetry()["by_verdict"] == {SB.V_AGREE: 1}


def test_a_disagreeing_book_is_recorded_as_such():
    tap = tap_for(resident(bids=((970, 1000),), offers=((990, 500),)))
    owner(tap, lambda s, deadline_epoch_s=None: {
        "marketData": retail_md(), "error": None}).read(SLUG)
    assert tap.drain()[0]["verdict"] == SB.V_DISAGREE


def test_no_tap_for_a_non_held_or_non_exact_read_or_when_off():
    tap = tap_for(resident())
    o = owner(tap, lambda s, deadline_epoch_s=None: {
        "marketData": retail_md(), "error": None})
    o.read("aec-mlb-not-held-2026-10-06")
    assert tap.telemetry()["tapped"] == 0
    tap2 = tap_for(resident(), eligible=lambda s: False)
    owner(tap2, lambda s, deadline_epoch_s=None: {
        "marketData": retail_md(), "error": None}).read(SLUG)
    assert tap2.telemetry()["tapped"] == 0
    calls = []
    owner(False, lambda s, deadline_epoch_s=None: calls.append(s) or {
        "marketData": retail_md(), "error": None}).read(SLUG)
    assert calls == [SLUG]
    # the process default is inert while the stream does not run here
    assert IAS.running() is False
    assert PMD.default_same_book_tap().applies(SLUG) is False


def test_a_failing_read_is_not_retried_and_a_stream_miss_is_not_persisted():
    tap = tap_for(resident())
    calls = []

    def boom(slug, deadline_epoch_s=None):
        calls.append(slug)
        raise RuntimeError("venue down")
    got = owner(tap, boom).read(SLUG)
    assert calls == [SLUG] and got["error"] == "RuntimeError"
    assert tap.drain() == []
    # stream not current (nothing subscribed): counted, not persisted
    empty = IS.ResidentBooks()
    empty.set_state(IS.S_IDLE, "test")
    tap2 = tap_for(empty)
    owner(tap2, lambda s, deadline_epoch_s=None: {
        "marketData": retail_md(), "error": None}).read(SLUG)
    assert tap2.drain() == []
    assert tap2.telemetry()["tapped"] == 1
    assert list(tap2.telemetry()["by_verdict"]) == [
        "%s:%s" % (SB.V_NC, SB.NC_STREAM)]


def test_the_public_lane_is_paced_once_per_tapped_read():
    paces = []
    lane = PMD.AuthLaneState(PMD.AUTH_PUBLIC, sleep=lambda s: None)
    real = lane.pace
    lane.pace = lambda: paces.append(1) or real()
    tap = tap_for(resident())
    o = owner(tap, None, public_state=lane,
              public_transport=lambda s, lane=None: {
                  "marketData": retail_md(), "error": None})
    got = o.read(SLUG, auth=PMD.AUTH_PUBLIC)
    assert got["served_by"] == "PUBLIC_GATEWAY"
    assert paces == [1]
    assert tap.drain()[0]["verdict"] == SB.V_AGREE
    # the transport skips its own pace for exactly ONE prepaced request

    class Inner:
        def handle_request(self, request):
            return type("R", (), {"status_code": 200, "headers": {}})()

    class Req:
        method = "GET"
        url = type("U", (), {"path": "/v1/markets/%s/book" % SLUG})()
    paces.clear()
    t = PMD.PublicGatewayTransport(Inner(), lane)
    tok = PMD._prepaced.set(True)
    try:
        t.handle_request(Req())
        assert paces == []
        t.handle_request(Req())
        assert paces == [1]
    finally:
        PMD._prepaced.reset(tok)


class _Conn:
    def __init__(self):
        self.rows = []

    def transaction(self):
        conn = self

        class T:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return T()

    async def fetch(self, sql, *a):
        return [1]

    async def execute(self, sql, *a):
        assert sql.startswith("INSERT INTO institutional_same_book_probe")
        self.rows.append(a)


def test_pending_samples_are_persisted_once_by_the_refresh_helper():
    tap = tap_for(resident())
    owner(tap, lambda s, deadline_epoch_s=None: {
        "marketData": retail_md(), "error": None}).read(SLUG)
    conn = _Conn()
    got = asyncio.run(PMD.persist_tap_samples(conn, tap=tap))
    assert got == {"rows": 1, "written": 1} and len(conn.rows) == 1
    assert asyncio.run(PMD.persist_tap_samples(conn, tap=tap)) == {
        "rows": 0, "written": 0}
    # no tap in the process: nothing touches the connection
    assert asyncio.run(PMD.persist_tap_samples(None)) == {
        "rows": 0, "written": 0}


# ── §5 Kalshi isolation ─────────────────────────────────────────────────

KALSHI_TICKERS = ("KXMLBGAME-26OCT06NYYBOS-NYY", "KXNFLGAME-26OCT11KCBUF",
                  "INXD-26OCT06-B5800")


@pytest.mark.parametrize("ticker", KALSHI_TICKERS)
def test_the_pmus_owner_refuses_a_kalshi_ticker_before_anything(ticker):
    calls = []
    o = PMD.Owner(transport=lambda s, deadline_epoch_s=None: calls.append(s),
                  recent=lambda s, max_age_s: calls.append(("cache", s)),
                  gate=lambda: calls.append("gate") or {"blocking": False},
                  public_transport=lambda s, lane=None: calls.append(s),
                  same_book_tap=False)
    o.set_held([ticker, SLUG])
    assert not o.is_held(ticker) and o.is_held(SLUG)
    for auth in (None, PMD.AUTH_PUBLIC):
        got = o.read(ticker, auth=auth)
        assert got["error"] == PMD.R_FOREIGN_VENUE
    assert calls == []
    t = o.telemetry()
    assert t["totals"]["refused_foreign_venue"] == 2
    assert t["totals"]["reads"] == 0 and t["totals"]["rest_dispatches"] == 0
    assert o.public.telemetry()["totals"]["dispatched"] == 0


def test_polymarket_slugs_are_not_mistaken_for_kalshi_tickers():
    for s in (SLUG, "aec-mlb-nyy-bos-2026-10-06", "tec-nfl-kc-buf-2026-10-11"):
        assert PMD.is_foreign_ticker(s) is False
    for s in KALSHI_TICKERS:
        assert PMD.is_foreign_ticker(s) is True
    assert PMD.is_foreign_ticker("") is False


MARKET_DATA_MODULES = (
    "paper_market_data.py", "market_data_identity.py",
    "institutional_stream.py", "institutional_api_stream.py",
    "institutional_same_book.py", "institutional_focus_universe.py",
    "institutional_stream_evidence.py", "institutional_book.py",
    "institutional_contract_map.py", "pmx_institutional.py",
    "bettor_paper_freshness.py", "bettor_paper_guard.py",
    "bettor_stream_currency.py", "bettor_market_stream.py",
    "bettor_market_subscription.py", "live_book_currency.py",
    "venue_pace.py", "venue_request_gate.py", "xavier_freshness.py",
    "agents/paper_mark_refresh.py", "workers/institutional_md.py",
    "workers/ext_pinnacle_loop.py")
KALSHI_MODULES = ("kalshi_venue.py", "kalshi_account.py", "kalshi_mapping.py",
                  "kalshi_orders.py", "kalshi_linkage.py")
PMUS_SHARED = {"venue_pace", "venue_request_gate", "paper_market_data",
               "institutional_stream", "institutional_api_stream",
               "institutional_same_book", "bettor_paper_freshness",
               "bettor_paper_guard", "ext_pinnacle_loop", "pmus",
               "bettor_market_stream", "bettor_market_subscription",
               "market_data_identity", "live_book_currency"}


def _imported(path) -> set:
    tree = ast.parse((ROOT / path).read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[-1])
            out |= {a.name.split(".")[-1] for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[-1] for a in node.names}
    return out


def test_no_pmus_market_data_or_freshness_module_imports_kalshi():
    for m in MARKET_DATA_MODULES:
        assert (ROOT / m).exists(), m
        bad = {n for n in _imported(m) if "kalshi" in n.lower()}
        assert not bad, (m, bad)


def test_no_kalshi_module_shares_pmus_pacing_gate_cache_or_freshness():
    for m in KALSHI_MODULES:
        bad = _imported(m) & PMUS_SHARED
        assert not bad, (m, bad)
    src = (ROOT / "kalshi_venue.py").read_text()
    for name in ("venue_pace", "venue_request_gate", "recent_book",
                 "_RECENT_BOOKS", "paper_market_data", "SLA_S"):
        assert name not in src, name


def test_the_production_shaped_pmx_credential_starts_the_stream():
    """The exact production collision (funded PMUS slot holding the PMX Auth0
    client) no longer blocks the orderless PMX market-data stream."""
    got = IS.start_default(env=incident_env(), available=lambda: (True, None),
                           token_fn=lambda: "tok", transport_factory=_Idle)
    assert got["started"] is True and got["state"] == IS.S_IDLE
    no_leak(got)
