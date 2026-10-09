"""THE OWNER'S SEVEN ADVERSARIAL SCENARIOS, AS EXECUTABLE PROOF (RC6 red-team
lane; the matrix is docs/closeout/RED_TEAM_SCENARIOS.md).

Each scenario is mapped there to the existing tests and controls that
already prove it (the chaos matrix C01-C30 and the lanes' own suites); this
file holds the cases that were MISSING. A case that failed against the
candidate base 412c4962 was a defect, fixed in the same change:

  STALE         a GAP Kalshi book is written unroutable without a new
                observed_at, and only CURRENT books are re-asserted
  MIS-MAPPED    a PMUS market whose stated home / away are the reverse of
                Kalshi's is never mapped; the LONG side's subject is the
                PMUS team_a, never a position in the slug
  CREDENTIALS   (defect) another venue's PEM key file in PMUS_SECRET_KEY
                signed PMUS requests; (defect) the PMX RSA key in the
                Kalshi slot signed Kalshi requests; each venue's signer
                reads only its own slots
  REPLAY        (defect) a replayed Kalshi snapshot rolled a CURRENT book
                back; (defect) a snapshot past a lost message hid the gap
                for the sid's other markets; a duplicated delta is never
                applied twice
  RECONNECT     across a reconnect no pre-resync Kalshi book is served or
                persisted, and a delta before the new snapshot is ignored
  PARTIAL       the twin's IOC partial is graded on the filled quantity
                and FOK is all-or-none
  AUTHORITY     (defect) the readback's authority was four literals: it is
                READ (execmirror_control, kalshi_smalllive_control, the
                Adriana modules' own assertions) and UNREAD is a failure

Throwaway keys only; no network; no value of any key in any output.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import re
import types
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization as S
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from sportsassets import kalshi_ws as KWS
from sportsassets import venue_key as VK
from sportsassets.redteam import controls as C

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
NOW = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc).timestamp()

# ── throwaway keys ──────────────────────────────────────────────────────
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PEM = _RSA.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8,
                             S.NoEncryption()).decode()
RSA_B64 = base64.b64encode(RSA_PEM.encode()).decode()     # PMX_PRIVATE_KEY_B64
_KED = ed25519.Ed25519PrivateKey.generate()                # a Kalshi key
KALSHI_ED_PEM = _KED.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8,
                                   S.NoEncryption()).decode()
_PED = ed25519.Ed25519PrivateKey.generate()                # a PMUS key
_SEED = _PED.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw,
                           S.NoEncryption())
_PUB = _PED.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
PMUS_RETAIL = base64.b64encode(_SEED + _PUB).decode()
KALSHI_KID = "11111111-2222-3333-4444-555555555555"
PMUS_KID = "99999999-8888-7777-6666-555555555555"
SECRETS = (RSA_PEM, RSA_B64, KALSHI_ED_PEM, PMUS_RETAIL)


def no_leak(obj):
    blob = json.dumps(obj, default=str)
    for v in SECRETS:
        assert v not in blob
        body = "".join(v.splitlines()[1:2])[:24]       # a PEM's first line
        assert not body or body not in blob


# ═════════════════════════════════════════════════════════════════════
# 1 · STALE DATA
# ═════════════════════════════════════════════════════════════════════

class _Conn:
    """Records every statement; answers what the readers ask."""

    def __init__(self, rows=None, tables=()):
        self.sql: list = []
        self.rows = rows or {}
        self.tables = set(tables)

    async def execute(self, sql, *args):
        self.sql.append((" ".join(sql.split()), args))

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            return args[0] in self.tables if args else False
        return None

    async def fetchrow(self, sql, *args):
        for k, v in self.rows.items():
            if k in sql:
                return v
        return None


def _snap(sid, seq, t, yes, no=()):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "yes_dollars_fp": [list(x) for x in yes],
                    "no_dollars_fp": [list(x) for x in no]}}


def _delta(sid, seq, t, price, d, side="yes"):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": t, "market_id": "u-" + t,
                    "price_dollars": price, "delta_fp": d, "side": side}}


def test_stale_a_gap_book_is_written_unroutable_and_never_restamped():
    from sportsassets.workers import kalshi_ws_market_data as W
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(_snap(1, 1, "K-NYY", [("0.43", "100")]))
    b.on_message(_snap(1, 2, "K-TB", [("0.44", "100")]))
    b.on_message(_delta(1, 4, "K-TB", "0.45", "1"))          # gap: seq 3
    conn = _Conn()
    asyncio.run(W.write_book(conn, "K-TB", b, now=NOW + 5))
    sql, args = conn.sql[-1]
    # the GAP book is made unroutable at once; its observed_at is NOT moved
    assert sql.startswith("UPDATE kalshi_books_current SET readable = false")
    assert "observed_at" not in sql
    assert args[1].startswith("GAP:")
    # the re-assertion (the stream proves CURRENT books current) never
    # touches a book that is not CURRENT
    conn = _Conn()
    assert asyncio.run(W.reassert(conn, b, now=NOW + 10)) == 0
    assert conn.sql == []
    b.on_message(_snap(2, 1, "K-NYY", [("0.43", "100")]))
    asyncio.run(W.reassert(conn, b, now=NOW + 10))
    (sql, args), = conn.sql
    assert "AND readable" in sql and args[0] == ["K-NYY"]


# ═════════════════════════════════════════════════════════════════════
# 2 · MIS-MAPPED DATA
# ═════════════════════════════════════════════════════════════════════

def _kfx(home, away, start):
    from sportsassets import kalshi_market_data as KMD
    return KMD.KalshiFixture(
        event_ticker="KXMLBGAME-26OCT09%s%s" % (away, home),
        series_ticker="KXMLBGAME", sport="BASEBALL", league="MLB",
        start_epoch=start, home_id="H", away_id="A", home_code=home,
        away_code=away, tie_ticker=None, team_tickers=(),
        outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
        milestone_id="m")


def test_mismapped_a_reversed_stated_home_away_is_never_mapped():
    from sportsassets import kalshi_claims as KCL
    k = _kfx("NYY", "TB", NOW + 3600)
    good = {"slug": "g", "league": "mlb", "team_a": "NYY", "team_b": "TB",
            "home": "NYY", "away": "TB", "start_epoch": NOW + 3600}
    assert KCL.map_pmus(k, [good])["status"] == "ESTABLISHED"
    rev = dict(good, slug="r", home="TB", away="NYY")
    got = KCL.map_pmus(k, [rev])
    assert got["status"] == "NOT_ESTABLISHED"
    assert got["reasons"] == ["NO_CANDIDATE"]


def test_mismapped_the_long_subject_is_the_pmus_team_a_never_guessed():
    from sportsassets import kalshi_claims as KCL
    k = _kfx("NYY", "TB", NOW + 3600)
    book = {"offers": [("0.45", 10)], "bids": [("0.43", 10)]}
    m = {"slug": "s", "team_a": "TB", "team_b": "NYY"}
    got = KCL.pmus_instruments(k, m, evidence=None, book=book)
    # the market is ABOUT team_a (TB, the away team): YES = TB wins, NO =
    # TB does not -- never the slug's first team, never HOME by default
    assert {(i.side, i.subject, i.team_code) for i in got} == {
        ("YES", "AWAY", "TB"), ("NO", "AWAY", "TB")}
    home = KCL.pmus_instruments(k, dict(m, team_a="NYY", team_b="TB"),
                                evidence=None, book=book)
    assert {i.subject for i in home} == {"HOME"}
    # a team_a that is neither Kalshi team maps to nothing
    assert KCL.pmus_instruments(k, dict(m, team_a="BOS"), evidence=None,
                                book=book) == []


# ═════════════════════════════════════════════════════════════════════
# 3 · CREDENTIAL ISOLATION
# ═════════════════════════════════════════════════════════════════════

#: (venue modules, env-name prefixes they must never read)
_NAME_WALLS = (
    (("kalshi_ws.py", "kalshi_venue.py", "kalshi_key.py",
      "kalshi_market_data.py", "workers/kalshi_ws_market_data.py"),
     r"(PMUS|PMX|EDGE_PMUS)_[A-Z0-9_]*"),
    (("pmus.py", "venue_key.py", "workers/mirror_shadow.py",
      "api/track_record.py", "api/pmus_account.py"),
     r"KALSHI_[A-Z0-9_]*(KEY|PEM|ID)[A-Z0-9_]*"),
    (("pmx_institutional.py", "pmx.py"),
     r"(KALSHI_[A-Z0-9_]*(KEY|PEM)|PMUS_SECRET|PMUS_EXECMIRROR_SECRET)"
     r"[A-Z0-9_]*"),
)


def test_credentials_each_venue_signer_reads_only_its_own_slots():
    offenders = []
    for files, pat in _NAME_WALLS:
        rx = re.compile(r"[\"']%s[\"']" % pat)
        for f in files:
            src = (ROOT / f).read_text()
            offenders += ["%s: %s" % (f, m.group(0))
                          for m in rx.finditer(src)]
    assert offenders == [], offenders


@pytest.fixture()
def pmus_wire(monkeypatch):
    """The real SDK client, an httpx send that records every request and a
    spy on the SDK signer (neither may see an authenticated request)."""
    import httpx
    import polymarket_us.client as sdk_client
    from sportsassets import config as CONFIG
    from sportsassets import pmus
    sent, signed = [], []

    def send(self, request, *a, **k):
        sent.append((request.method, str(request.url),
                     "X-PM-Signature" in request.headers))
        return httpx.Response(200, json={"positions": {}, "eof": True},
                              request=request)
    real = sdk_client.create_auth_headers

    def spy(*a, **k):
        signed.append(a[2:])
        return real(*a, **k)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(sdk_client, "create_auth_headers", spy)

    def use(kid, sec):
        cfg = types.SimpleNamespace(pmus_key_id=kid, pmus_secret_key=sec)
        monkeypatch.setattr(CONFIG, "settings", lambda: cfg)
        monkeypatch.setattr(pmus, "settings", lambda: cfg)
        monkeypatch.setattr(pmus, "_client", None)
        monkeypatch.setattr(pmus, "_read_client", None)
        return pmus
    return types.SimpleNamespace(use=use, sent=sent, signed=signed)


def test_credentials_a_kalshi_pem_key_in_the_pmus_slot_never_signs(pmus_wire):
    """DEFECT (base 412c4962): every PMUS gate accepted "an Ed25519 key in
    any encoding", so Kalshi's own key file (an Ed25519 PKCS#8 PEM, Kalshi's
    default) pasted into PMUS_SECRET_KEY was normalised and SIGNED Polymarket
    US requests -- while the red-team control called the same slot
    CREDENTIAL_CLASS_MISMATCH ... MISMATCH_PATH_BLOCKED."""
    assert VK.pmus_slot_refusal(KALSHI_ED_PEM) == VK.R_PEM_KEY_FILE
    with pytest.raises(VK.SecretNotEd25519) as e:
        VK.signing_secret(KALSHI_ED_PEM)
    assert str(e.value) == VK.R_PEM_KEY_FILE
    pm = pmus_wire.use(PMUS_KID, KALSHI_ED_PEM)
    c = pm._get_client()
    assert pm.CREDENTIAL_GATE["installed"] is True
    assert pm.CREDENTIAL_GATE["refusal"] == VK.R_PEM_KEY_FILE
    for call in (lambda: c.portfolio.positions({"limit": 100}),
                 lambda: c.account.balances(),
                 lambda: c.orders.create({"marketSlug": "aec-x"})):
        with pytest.raises(VK.SecretNotEd25519) as e:
            call()
        assert str(e.value) == VK.R_PEM_KEY_FILE
    # the read client is judged on the value AS CONFIGURED: re-encoding the
    # key file must not strip the armour it is refused by
    r = pm._get_read_client()
    assert r is c
    with pytest.raises(VK.SecretNotEd25519):
        r.portfolio.positions({"limit": 100})
    from sportsassets.workers import mirror_shadow as MS
    assert MS.pmus_secret_unusable_reason(
        secret_fn=lambda: KALSHI_ED_PEM) == VK.R_PEM_KEY_FILE
    assert [s for s in pmus_wire.sent if s[2]] == []
    assert pmus_wire.signed == []
    no_leak(pm.CREDENTIAL_GATE)


def test_credentials_the_production_rsa_slot_and_a_real_key_are_unchanged(
        pmus_wire):
    # the PMX RSA PEM in the PMUS slot (production RC5): the same refusal
    for sec in (RSA_PEM, RSA_B64):
        assert VK.pmus_slot_refusal(sec) == VK.R_NOT_ED25519
    pm = pmus_wire.use("pmxclientidnotreal0123456789abcd", RSA_PEM)
    pm._get_client()
    assert pm.CREDENTIAL_GATE["refusal"] == VK.R_NOT_ED25519
    # a genuine retail key, in every encoding venue_key reads, still signs
    for sec in (PMUS_RETAIL, _SEED.hex(), base64.b64encode(_SEED).decode()):
        assert VK.pmus_slot_refusal(sec) is None
    pm = pmus_wire.use(PMUS_KID, PMUS_RETAIL)
    pm._get_client().portfolio.positions({"limit": 100})
    assert pm.CREDENTIAL_GATE["installed"] is False
    assert pmus_wire.signed and pmus_wire.sent[-1][2] is True


def _cross_env(**over):
    env = {"PMX_KEY_ID": "pmx-kid", "PMX_PRIVATE_KEY_B64": RSA_B64,
           "KALSHI_API_KEY_ID": KALSHI_KID,
           "KALSHI_PRIVATE_KEY_PEM": KALSHI_ED_PEM,
           "PMUS_KEY_ID": PMUS_KID, "PMUS_SECRET_KEY": PMUS_RETAIL}
    env.update(over)
    return env


def test_credentials_one_key_pair_in_two_venues_slots_is_found_by_key():
    from sportsassets import credential_isolation as CI
    ok = _cross_env()
    assert CI.reuse(ok) == []
    fps = CI.fingerprints(ok)
    assert set(fps) == {"PMX_PRIVATE_KEY_B64", "KALSHI_PRIVATE_KEY_PEM",
                        "PMUS_SECRET_KEY"}
    assert len(set(fps.values())) == 3
    no_leak(fps)
    # the PMX RSA key in the Kalshi slot: both RSA PEMs, no shape can tell
    # them apart -- the key pair does, whatever its encoding
    bad = _cross_env(KALSHI_PRIVATE_KEY_PEM=RSA_PEM)
    assert C.credential_classes(bad)["KALSHI"] == "KALSHI_RSA_API_KEY"
    assert CI.reuse(bad) == ["KALSHI_PRIVATE_KEY_PEM=PMX_PRIVATE_KEY_B64"]
    g = C.credentials({"api": C.credential_classes(bad)})
    assert g["status"] == C.RED
    assert ("CREDENTIAL_REUSED_ACROSS_VENUES:api:KALSHI_PRIVATE_KEY_PEM="
            "PMX_PRIVATE_KEY_B64") in g["blockers"]
    assert any("two venues" in a for a in g["evidence"]["owner_actions"])
    no_leak(g)
    # the production topology (the PMX RSA key also in the PMUS slot) is
    # named by key as well as by class
    prod = C.credential_classes(_cross_env(PMUS_KEY_ID="pmx-client",
                                           PMUS_SECRET_KEY=RSA_PEM))
    assert prod["CROSS_VENUE_KEY_REUSE"] == [
        "PMUS_SECRET_KEY=PMX_PRIVATE_KEY_B64"]
    # slots of ONE venue sharing a key are not a cross-venue reuse
    assert CI.reuse(_cross_env(PMUS_EXECMIRROR_SECRET_KEY=PMUS_RETAIL)) == []


def test_credentials_the_kalshi_signers_refuse_another_venues_key(
        monkeypatch):
    """DEFECT (base 412c4962): the PMX client's RSA key pasted into
    KALSHI_PRIVATE_KEY_PEM loaded (an RSA PEM is a documented Kalshi type)
    and signed Kalshi requests."""
    from sportsassets import credential_isolation as CI
    from sportsassets import kalshi_venue as KV
    env = _cross_env(KALSHI_PRIVATE_KEY_PEM=RSA_PEM, KALSHI_ENV="prod")
    st = KV.credential_state(env)
    assert st["state"] == KV.KALSHI_KEY_REUSED_ACROSS_VENUES
    assert st["complete"] is False
    assert st["also_configured_as"] == ["PMX_PRIVATE_KEY_B64"]
    sent = []

    class T:
        def send(self, *a, **k):
            sent.append(a)
            raise AssertionError("nothing may be sent")
    r = KV.KalshiClient(T(), env=env).balance()
    assert isinstance(r, KV.Refusal) and r.sent is False
    assert r.code == KV.KALSHI_KEY_REUSED_ACROSS_VENUES and sent == []
    assert KV.submission_gate(st, {"enabled": True}, {}, env_enabled=True,
                              now=NOW).code == \
        KV.KALSHI_KEY_REUSED_ACROSS_VENUES
    # its own key: unchanged
    own = KV.credential_state(_cross_env(KALSHI_ENV="prod"))
    assert own["state"] == "PRESENT" and own["also_configured_as"] == []
    no_leak(st)

    # the WebSocket runtime (the Kalshi signer production runs) beats
    # OWNER_ACTION_REQUIRED by name and never connects
    from sportsassets.workers import kalshi_ws_market_data as W

    class Stop(BaseException):
        pass
    beats, connects = [], []

    async def hb(service, status, detail=None, con=None):
        beats.append((status, detail))

    async def sleep(_s):
        raise Stop()
    monkeypatch.setattr(W, "heartbeat", hb)
    monkeypatch.setattr(W.asyncio, "sleep", sleep)
    monkeypatch.setattr(W.KWS, "websockets_connect",
                        lambda *a, **k: connects.append(a))
    with pytest.raises(Stop):
        asyncio.run(W.run(env=env))
    (status, detail), = beats
    assert status == "blocked" and detail["state"] == "OWNER_ACTION_REQUIRED"
    assert detail["why"] == CI.R_CROSS_VENUE_KEY_REUSE
    assert detail["key"]["also_configured_as"] == ["PMX_PRIVATE_KEY_B64"]
    assert connects == []
    no_leak(detail)


# ═════════════════════════════════════════════════════════════════════
# 4 · REPLAY (duplicated / replayed venue messages)
# ═════════════════════════════════════════════════════════════════════

def _book(b, t):
    cur = b.current(t)
    return cur["book"]["orderbook_fp"]["yes_dollars"] if cur["ok"] else None


def test_replay_a_replayed_snapshot_never_rolls_a_current_book_back():
    """DEFECT (base 412c4962): a snapshot was applied whatever its seq, so
    a replay of seq 1 after delta seq 2 reverted the book to its seq-1 state
    and served it as CURRENT."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    first = _snap(1, 1, "K-NYY", [("0.43", "100")])
    assert b.on_message(first) == "SNAPSHOT"
    assert b.on_message(_delta(1, 2, "K-NYY", "0.44", "50")) == "DELTA"
    assert _book(b, "K-NYY") == [["0.43", "100"], ["0.44", "50"]]
    assert b.on_message(first) == "GAP"                       # the replay
    cur = b.current("K-NYY")
    assert not cur["ok"] and cur["book"] is None
    assert cur["why"] == KWS.R_SNAPSHOT_OUT_OF_SEQUENCE
    assert b.stats["snapshots_out_of_sequence"] == 1
    assert "K-NYY" in b.resubscribe
    # nothing more on the broken sid is trusted, not even a snapshot
    assert b.on_message(_delta(1, 3, "K-NYY", "0.44", "-50")) == \
        "IGNORED_NOT_CURRENT"
    assert b.on_message(_snap(1, 4, "K-NYY", [("0.40", "1")])) == \
        "IGNORED_GAPPED_SID"
    assert not b.current("K-NYY")["ok"]
    # the resubscription (a new sid) restores CURRENT on ITS snapshot
    b.on_message(_snap(2, 1, "K-NYY", [("0.46", "20")]))
    assert _book(b, "K-NYY") == [["0.46", "20"]]


def test_replay_a_snapshot_past_a_lost_message_gaps_every_market_of_the_sid():
    """DEFECT (base 412c4962): a snapshot advanced the sid's sequence past a
    lost delta of ANOTHER market, which then served a book missing that
    update as CURRENT and applied the next delta on top of it."""
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(_snap(7, 1, "K-NYY", [("0.43", "100")]))
    b.on_message(_snap(7, 2, "K-TB", [("0.44", "100")]))
    # seq 3 (a K-TB delta) is lost; a K-NYY snapshot arrives as seq 4
    assert b.on_message(_snap(7, 4, "K-NYY", [("0.42", "9")])) == "GAP"
    for t in ("K-NYY", "K-TB"):
        assert not b.current(t)["ok"], t
    assert b.on_message(_delta(7, 5, "K-TB", "0.45", "1")) == \
        "IGNORED_NOT_CURRENT"
    assert {"K-NYY", "K-TB"} <= b.resubscribe


def test_replay_a_duplicated_delta_is_never_applied_twice():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(_snap(1, 1, "K-NYY", [("0.43", "100")]))
    d = _delta(1, 2, "K-NYY", "0.44", "50")
    assert b.on_message(d) == "DELTA"
    assert b.on_message(d) == "GAP"                  # never +100 at 0.44
    assert not b.current("K-NYY")["ok"]
    assert b.books["K-NYY"]["yes"][Decimal("0.44")] == Decimal("50")


def test_replay_a_reannounced_sid_is_a_new_subscription():
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(_snap(3, 1, "K-NYY", [("0.43", "100")]))
    b.on_message(_delta(3, 3, "K-NYY", "0.44", "1"))         # gap
    assert b.on_message(_snap(3, 4, "K-NYY", [("0.40", "1")])) == \
        "IGNORED_GAPPED_SID"
    # the venue announces sid 3 again (a reused number): its own snapshot
    # starts it afresh; an in-sequence sid is never reset by an announcement
    b.on_message({"type": "subscribed", "msg": {"channel": "orderbook_delta",
                                                "sid": 3}})
    assert b.on_message(_snap(3, 1, "K-NYY", [("0.41", "5")])) == "SNAPSHOT"
    assert _book(b, "K-NYY") == [["0.41", "5"]]
    b.on_message({"type": "subscribed", "msg": {"channel": "orderbook_delta",
                                                "sid": 3}})
    assert b.on_message(_delta(3, 2, "K-NYY", "0.41", "1")) == "DELTA"


class _FakeWS:
    def __init__(self, script):
        self.script, self.sent, self.closed = list(script), [], False

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        if not self.script:
            raise ConnectionError("closed")
        return json.dumps(self.script.pop(0))

    async def close(self):
        self.closed = True


def test_replay_end_to_end_the_subscriber_resubscribes_and_serves_only_fresh():
    seen = []
    first = _snap(3, 1, "K-NYY", [("0.43", "100")])
    ws = _FakeWS([{"type": "subscribed", "msg": {"channel": "orderbook_delta",
                                                 "sid": 3}},
                  first, _delta(3, 2, "K-NYY", "0.44", "5"),
                  first,                                   # replayed
                  _snap(4, 1, "K-NYY", [("0.47", "10")])])
    books = KWS.WsBooks(clock=lambda: NOW)
    real = books.on_message

    def watch(m, **k):
        out = real(m, **k)
        seen.append((out, _book(books, "K-NYY")))
        return out
    books.on_message = watch

    async def connect():
        return ws
    sub = KWS.Subscriber(connect, books, wanted=lambda: ["K-NYY"],
                         clock=lambda: NOW)
    with pytest.raises(ConnectionError):
        asyncio.run(sub.session())
    # the rolled-back seq-1 book was never served after seq 2
    served = [bk for _o, bk in seen if bk is not None]
    assert served == [[["0.43", "100"]], [["0.43", "100"], ["0.44", "5"]],
                      [["0.47", "10"]]]
    assert [m["cmd"] for m in ws.sent] == ["subscribe", "unsubscribe",
                                           "subscribe"]
    assert sub.resubscribes == 1


# ═════════════════════════════════════════════════════════════════════
# 5 · RECONNECT (never serve pre-resync state)
# ═════════════════════════════════════════════════════════════════════

def test_reconnect_no_pre_resync_book_is_served_or_persisted():
    from sportsassets.workers import kalshi_ws_market_data as W
    b = KWS.WsBooks(clock=lambda: NOW)
    b.on_connected()
    b.on_message(_snap(1, 1, "K-NYY", [("0.43", "100")]))
    assert b.current("K-NYY")["ok"]
    b.on_disconnected()
    b.on_connected()                     # a new connection; sids restart
    # a delta numbered as if it continued the old sid is never applied to
    # the pre-disconnect book
    assert b.on_message(_delta(1, 2, "K-NYY", "0.44", "1")) == \
        "IGNORED_NOT_CURRENT"
    assert not b.current("K-NYY")["ok"]
    conn = _Conn()
    asyncio.run(W.write_book(conn, "K-NYY", b, now=NOW + 1))
    asyncio.run(W.reassert(conn, b, now=NOW + 1))
    (sql, args), = conn.sql
    assert "readable = false" in sql and "observed_at" not in sql
    assert args[1] == "GAP:%s" % KWS.R_DISCONNECT
    # the new connection's own snapshot is the only way back
    b.on_message(_snap(1, 1, "K-NYY", [("0.50", "7")]))
    assert _book(b, "K-NYY") == [["0.50", "7"]]


def test_reconnect_end_to_end_two_sessions():
    script1 = [_snap(1, 1, "K-NYY", [("0.43", "100")])]
    script2 = [{"type": "subscribed", "msg": {"channel": "orderbook_delta",
                                              "sid": 1}},
               _delta(1, 2, "K-NYY", "0.44", "1"),        # before snapshot
               _snap(1, 1, "K-NYY", [("0.52", "3")])]
    sockets = [_FakeWS(script1), _FakeWS(script2)]
    books = KWS.WsBooks(clock=lambda: NOW)
    states = []
    real = books.on_message

    def watch(m, **k):
        out = real(m, **k)
        states.append((out, _book(books, "K-NYY")))
        return out
    books.on_message = watch

    async def connect():
        return sockets.pop(0)
    sub = KWS.Subscriber(connect, books, wanted=lambda: ["K-NYY"],
                         clock=lambda: NOW)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            asyncio.run(sub.session())
    assert states == [("SNAPSHOT", [["0.43", "100"]]),
                      ("SUBSCRIBED", None),
                      ("IGNORED_NOT_CURRENT", None),
                      ("SNAPSHOT", [["0.52", "3"]])]
    assert not books.current("K-NYY")["ok"]       # the socket closed


# ═════════════════════════════════════════════════════════════════════
# 6 · PARTIAL FILLS
# ═════════════════════════════════════════════════════════════════════

def _order(tif, qty, fills):
    return {"slug": "s", "tif": tif, "direction": "BUY", "side": "LONG",
            "limit": 0.50, "qty": qty, "eligible": NOW, "expires": NOW + 60,
            "fills": [{"qty": q} for q in fills],
            "books": [{"at": NOW + 1, "asks": [{"px": 0.48, "qty": 4}],
                       "bids": [{"px": 0.46, "qty": 4}]}]}


def test_partial_ioc_is_graded_on_the_filled_quantity_and_fok_is_all_or_none():
    from sportsassets.completion import fill_replay as FR
    ioc = FR.replay_marketable(_order("IOC", 10, [4]))
    assert ioc["state"] == "PARTIAL" and ioc["qty"] == 4.0
    fok = FR.replay_marketable(_order("FOK", 10, []))
    assert fok["state"] == "EXPIRED" and fok["qty"] == 0.0
    # agreement is on the quantity, not merely fill / no-fill: a PAPER
    # partial of 4 agrees; a PAPER "fill" of 10 on 4 shown is a mismatch
    assert FR.classify(_order("IOC", 10, [4]), ioc) is None
    assert FR.classify(_order("IOC", 10, [10]), ioc) == FR.M_QTY
    rep = FR.agreement([_order("IOC", 10, [4]),
                        dict(_order("FOK", 10, []), slug="t")])
    assert rep["agree"] == 2 and rep["optimistic_false_fills"] == 0


# ═════════════════════════════════════════════════════════════════════
# 7 · AUTHORITY
# ═════════════════════════════════════════════════════════════════════

def test_authority_small_live_is_read_from_its_gate():
    """DEFECT (base 412c4962): the readback's small_live was the literal
    "SHADOW" and the interlock compared completion's literal: a running
    actual lane still read SHADOW."""
    from sportsassets.redteam import readiness as R
    ok = {"gates": {"small_live_shadow": {"value": True, "reason": None}}}
    assert R.small_live_authority(ok)["state"] == "SHADOW"
    live = {"gates": {"small_live_shadow": {
        "value": False, "reason": "SMALL_LIVE_ACTUAL_LANE_ACTIVE"}}}
    assert R.small_live_authority(live)["state"] == "ACTUAL_LANE_ACTIVE"
    gone = {"gates": {"small_live_shadow": {
        "value": False, "reason": "EXECMIRROR_CONTROL_ABSENT"}}}
    assert R.small_live_authority(gone)["state"] == \
        "UNREAD:EXECMIRROR_CONTROL_ABSENT"
    assert R.small_live_authority({})["state"].startswith("UNREAD:")
    # completion's own `small_live` literal is no evidence either way
    assert R.small_live_authority(dict(live, small_live="SHADOW"))[
        "state"] == "ACTUAL_LANE_ACTIVE"


def test_authority_kalshi_live_money_is_read_from_its_control_row():
    from sportsassets import kalshi_venue as KV
    from sportsassets.redteam import readiness as R
    assert R.KALSHI_ENABLED_ENV == KV.ENABLED_ENV

    def read(row, env, tables=("kalshi_smalllive_control",)):
        return asyncio.run(R.kalshi_live_money(
            _Conn(rows={"kalshi_smalllive_control": row}, tables=tables),
            env=env))
    off = {"enabled": False, "stopped": False, "revision": 0}
    assert read(off, {})["state"] == "NOT_ACTIVATED"
    # a hostile environment switch alone changes nothing (evidence only)
    hostile = read(off, {KV.ENABLED_ENV: "1"})
    assert hostile["state"] == "NOT_ACTIVATED"
    assert hostile["env_switch_on_this_process"] is True
    stopped = {"enabled": True, "stopped": True, "revision": 3}
    assert read(stopped, {})["state"] == "NOT_ACTIVATED"
    on = {"enabled": True, "stopped": False, "revision": 4}
    assert read(on, {})["state"] == "CONTROL_ENABLED_NOT_STOPPED"
    assert read(None, {})["state"] == "UNREAD:KALSHI_CONTROL_ROW_MISSING"
    assert read(off, {}, tables=())["state"] == \
        "UNREAD:KALSHI_CONTROL_TABLE_ABSENT"


def test_authority_adriana_is_read_from_her_own_assertions(monkeypatch):
    from sportsassets.agents import adriana_arb as AA
    from sportsassets.redteam import readiness as R
    assert R.adriana_authority()["state"] == "SHADOW_ONLY"
    monkeypatch.setitem(AA.AUTHORITY, "submit", True)
    assert R.adriana_authority()["state"] == "AUTHORITY_GRANTED"


def test_authority_pm_acceptance_reads_the_lane_not_the_literal():
    """DEFECT (base 412c4962): pm_bind.acceptance's live_authority_shadow
    compared completion's literal small_live = "SHADOW"."""
    from sportsassets.pm_bind import acceptance as PA
    red = {"completion": {"small_live": "SHADOW"},
           "authority": {"small_live": "ACTUAL_LANE_ACTIVE"}}
    r = PA.evaluate(red=red, scoreboard={}, release=None, now=NOW)
    assert r["evidence_input"]["live_authority_shadow"] is False
    assert "live_authority_shadow" in r["critical_failures"]
    assert "execmirror_control" in r["provenance"]["live_authority_shadow"][
        "source"]
    red["authority"]["small_live"] = "SHADOW"
    r = PA.evaluate(red=red, scoreboard={}, release=None, now=NOW)
    assert r["evidence_input"]["live_authority_shadow"] is True


def test_authority_the_pm_acceptance_envelope_carries_what_was_read():
    from sportsassets.api import command_red_team as API
    ok = {"authority": {"small_live": "SHADOW",
                        "kalshi_live_money": "NOT_ACTIVATED",
                        "adriana": "SHADOW_ONLY",
                        "capital_authority_granted": False}}
    a = API._authority_of(ok)
    assert a["authority_expanded"] is False and a["small_live"] == "SHADOW"
    bad = {"authority": dict(ok["authority"],
                             kalshi_live_money="CONTROL_ENABLED_NOT_STOPPED")}
    b = API._authority_of(bad)
    assert b["authority_expanded"] is True
    assert b["kalshi_live_money"] == "CONTROL_ENABLED_NOT_STOPPED"
    assert API._authority_of({})["small_live"] == "UNREAD"
