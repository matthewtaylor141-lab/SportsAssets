"""RUN 83.4 -- the offline end-to-end proof, with REAL PROCESSES and NO VENUE.

Three processes, started for real, talking over loopback:

    broker (python -m pmusbroker.server)
        |  POST /mint          minted for GET /v1/ws/markets, nothing else
    collector (python -m rn1collector.runner, from the BUILT ARTIFACT)
        |  wss:// handshake
    fake PMUS endpoint (rn1-collector/tests/fake_pmus.py)

THE FAKE ENDPOINT VERIFIES THE SIGNATURE ITSELF, from the public key, by
re-deriving `timestamp + method + path`. It does not import the broker. So a
handshake succeeding here means a third party holding only the public key
accepted what the broker produced -- if the two agreed merely because they
shared code, this would prove nothing.

THE CREDENTIAL IS GENERATED IN THE TEST. It is an Ed25519 keypair created at
setup, has never been presented to any venue, and is not a credential in any
sense that matters. No real secret is read, and none could be: the collector
artifact has no signing primitive in it.

NO REAL HOST IS CONTACTED, and that is enforced rather than asserted: a
sitecustomize on PYTHONPATH patches getaddrinfo/create_connection/socket.connect
in EVERY child process to raise on any Polymarket hostname, and writes a marker
file if it ever fires. The test fails if the marker appears.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
COLLECTOR_ROOT = REPO / "rn1-collector"
BROKER_ROOT = REPO / "pmus-broker"
ARTIFACT = COLLECTOR_ROOT / "build"
FAKE = COLLECTOR_ROOT / "tests" / "fake_pmus.py"
HOSTGUARD = COLLECTOR_ROOT / "tests" / "hostguard"

pytestmark = pytest.mark.skipif(
    not (COLLECTOR_ROOT.exists() and BROKER_ROOT.exists()),
    reason="run 83.4 artifacts not present")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(pred, timeout=20.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class Rig:
    """Builds the artifact, starts the processes, tears them down."""

    def __init__(self, tmp: pathlib.Path, *, fail_first: int = 0,
                 method: str = "GET") -> None:
        self.tmp = tmp
        self.procs: list[subprocess.Popen] = []
        self.marker = tmp / "HOSTGUARD_FIRED"
        self.record = tmp / "fake_record.json"

        # A generated test keypair. Never a real credential.
        from nacl.signing import SigningKey
        sk = SigningKey.generate()
        self.secret_b64 = base64.b64encode(bytes(sk)).decode()
        self.public_b64 = base64.b64encode(bytes(sk.verify_key)).decode()
        self.key_id = "00000000-0000-4000-8000-000000000001"
        self.caller_token = "offline-e2e-caller-token"

        self.broker_port = _free_port()
        self.fake_port = _free_port()

        subprocess.run([sys.executable, str(COLLECTOR_ROOT / "build_artifact.py")],
                       check=True, capture_output=True)

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(HOSTGUARD), str(BROKER_ROOT), str(ARTIFACT)])
        env["RN1_HOSTGUARD_MARKER"] = str(self.marker)
        env["PYTHONUNBUFFERED"] = "1"
        self.env = env

        ready = tmp / "fake_ready"
        self._spawn([sys.executable, str(FAKE),
                     "--port", str(self.fake_port),
                     "--public-key", self.public_b64,
                     "--record", str(self.record),
                     "--ready", str(ready),
                     "--frames", "3",
                     "--fail-first", str(fail_first),
                     "--method", method])
        _wait(ready.exists, what="fake PMUS endpoint")

        benv = dict(env)
        benv.update({"PMUS_BROKER_KEY_ID": self.key_id,
                     "PMUS_BROKER_SECRET_KEY": self.secret_b64,
                     "PMUS_BROKER_CALLER_TOKEN": self.caller_token,
                     "PMUS_BROKER_BIND": "127.0.0.1",
                     "PMUS_BROKER_PORT": str(self.broker_port)})
        self._spawn([sys.executable, "-m", "pmusbroker.server"], env=benv)
        _wait(self._broker_up, what="broker")

    def _spawn(self, cmd, env=None) -> subprocess.Popen:
        p = subprocess.Popen(cmd, env=env or self.env, cwd=str(self.tmp),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
        self.procs.append(p)
        return p

    def _broker_up(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.broker_port), 0.2):
                return True
        except OSError:
            return False

    def run_collector(self, *, path: str = "/v1/ws/markets",
                      out_name: str = "result.json") -> dict:
        """Run the collector artifact as a real process against the fake."""
        out = self.tmp / out_name
        env = dict(self.env)
        env.update({"RN1_OBS_BROKER_URL": f"http://127.0.0.1:{self.broker_port}",
                    "RN1_OBS_BROKER_CALLER_TOKEN": self.caller_token})
        p = subprocess.run(
            [sys.executable, "-m", "rn1collector.runner",
             "--ws-url", f"ws://127.0.0.1:{self.fake_port}{path}",
             "--frames", "3", "--out", str(out)],
            env=env, cwd=str(self.tmp), capture_output=True, text=True,
            timeout=90)
        self.last_stdout = p.stdout + p.stderr
        return json.loads(out.read_text()) if out.exists() else {
            "ok": False, "error": "no result file", "stdout": self.last_stdout}

    def mint_raw(self, body: dict | None, *, token: str | None = None) -> tuple[int, dict]:
        """Talk to the broker directly, the way a hostile caller would."""
        import httpx
        headers = {}
        if token is not None:
            headers["X-Broker-Caller"] = token
        r = httpx.post(f"http://127.0.0.1:{self.broker_port}/mint",
                       json=body, headers=headers, timeout=10)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    def attempts(self) -> list[dict]:
        return json.loads(self.record.read_text())["attempts"] \
            if self.record.exists() else []

    def close(self) -> None:
        for p in self.procs:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    assert not r.marker.exists(), (
        f"the host guard fired: {r.marker.read_text() if r.marker.exists() else ''}")
    r.close()


# =====================================================================
# 1. THE CORRECT HANDSHAKE SUCCEEDS
# =====================================================================
def test_1_the_market_websocket_handshake_succeeds_end_to_end(rig):
    result = rig.run_collector()
    assert result["ok"] is True, result
    assert result["frames"] == 3
    accepted = [a for a in rig.attempts() if a["ok"]]
    assert accepted, rig.attempts()
    assert accepted[0]["path"] == "/v1/ws/markets"
    assert accepted[0]["reason"] == "VERIFIED"
    # And the science survived the round trip.
    assert result["zero_ms_status"] in ("CAPTURED_STREAM",
                                        "NO_VALID_PRE_RECEIPT_STATE")
    assert result["depth_authority"] in (None, "TOP_OF_BOOK_ONLY")
    assert len(result["offsets"]) == 10


# =====================================================================
# 2, 3, 4. THE SAME MATERIAL FAILS EVERYWHERE ELSE
# =====================================================================
@pytest.mark.parametrize("bad_path", ["/v1/orders", "/v1/ws/private"])
def test_2_and_3_the_same_minted_material_fails_for_other_paths(rig, bad_path):
    """Proof by PRESENTATION, not by refusing to try.

    The collector mints for the market socket, then the test replays that exact
    header set at a different path. The fake recomputes `timestamp + GET + path`
    for the path actually requested, so the signature does not verify. This is
    the property that makes header scope real: a leaked market handshake is not
    a trading credential.
    """
    import asyncio

    import websockets

    status, payload = rig.mint_raw({"consumer": "adversarial"},
                                   token=rig.caller_token)
    assert status == 200
    headers = payload["headers"]

    async def attempt():
        url = f"ws://127.0.0.1:{rig.fake_port}{bad_path}"
        async with websockets.connect(url, additional_headers=headers):
            return "CONNECTED"

    with pytest.raises(Exception) as exc:
        asyncio.new_event_loop().run_until_complete(attempt())
    assert "401" in str(exc.value) or "403" in str(exc.value), str(exc.value)

    hit = [a for a in rig.attempts() if a["path"] == bad_path]
    assert hit, rig.attempts()
    assert hit[-1]["ok"] is False
    assert hit[-1]["reason"] == \
        "SIGNATURE_DOES_NOT_VERIFY_FOR_THIS_METHOD_AND_PATH"


def test_4_the_same_minted_material_fails_under_a_different_method(tmp_path):
    """The fake verifies against POST; material signed for GET does not verify."""
    r = Rig(tmp_path, method="POST")
    try:
        result = r.run_collector()
        assert result["ok"] is False
        refused = [a for a in r.attempts() if not a["ok"]]
        assert refused, r.attempts()
        assert all(a["reason"] ==
                   "SIGNATURE_DOES_NOT_VERIFY_FOR_THIS_METHOD_AND_PATH"
                   for a in refused), refused
        assert not r.marker.exists()
    finally:
        r.close()


# =====================================================================
# 5 & 6. A FAILED CONNECT MINTS AGAIN AND NEVER REPLAYS
# =====================================================================
def test_5_and_6_a_failed_connect_mints_again_and_never_replays(tmp_path):
    """The venue scheme has no nonce, so reuse is never attempted.

    The fake refuses the first connection AFTER verifying it, which is exactly
    the case where a naive client would retry the material it already has.
    """
    r = Rig(tmp_path, fail_first=1)
    try:
        result = r.run_collector()
        assert result["ok"] is True, result

        presented = [a["signature_fp"] for a in r.attempts()
                     if a["reason"] != "FORCED_CONNECT_FAILURE"]
        assert len(presented) >= 2, r.attempts()
        assert len(set(presented)) == len(presented), (
            f"a signature was presented twice -- material was replayed: {presented}")
        assert not r.marker.exists()
    finally:
        r.close()


# =====================================================================
# 7. THE COLLECTOR CANNOT ASK FOR A DIFFERENT METHOD OR PATH
# =====================================================================
@pytest.mark.parametrize("body", [
    {"method": "POST"},
    {"path": "/v1/orders"},
    {"url": "https://api.polymarket.us/v1/orders"},
    {"host": "api.polymarket.us"},
    {"consumer": "x", "path": "/v1/ws/private"},
])
def test_7_the_broker_refuses_every_attempt_to_steer_the_signature(rig, body):
    status, payload = rig.mint_raw(body, token=rig.caller_token)
    assert status == 400, (status, payload)
    assert payload["error"].startswith("SIGNING_PARAMETER_REFUSED")


def test_7b_the_broker_default_denies_an_unidentified_caller(rig):
    assert rig.mint_raw({"consumer": "x"}, token=None)[0] == 401
    assert rig.mint_raw({"consumer": "x"}, token="wrong")[0] == 401


def test_7c_a_smuggled_path_in_the_consumer_label_changes_nothing(rig):
    """The label is accepted; it simply has no route into the signed bytes."""
    status, a = rig.mint_raw({"consumer": "/v1/orders"}, token=rig.caller_token)
    assert status == 200
    status, b = rig.mint_raw({"consumer": "harmless"}, token=rig.caller_token)
    assert status == 200
    # Same key id, same header set shape; only the timestamp and signature move.
    assert a["headers"]["X-PM-Access-Key"] == b["headers"]["X-PM-Access-Key"]
    assert set(a["headers"]) == set(b["headers"])
    assert a["capability_id"] == b["capability_id"] == "PMUS_MARKET_WS_HANDSHAKE"


# =====================================================================
# 8, 9, 10. NO SECRET, NO ORDER, NO REAL HOST
# =====================================================================
def test_8_the_collector_never_receives_the_secret_key(rig):
    result = rig.run_collector()
    assert result["secret_seen"] is False
    blob = json.dumps(result) + rig.last_stdout
    assert rig.secret_b64 not in blob, "the secret reached the collector"
    assert rig.secret_b64[:16] not in blob

    # And the broker's mint response carries no secret either.
    _, payload = rig.mint_raw({"consumer": "x"}, token=rig.caller_token)
    assert rig.secret_b64 not in json.dumps(payload)
    assert set(payload["headers"]) == {
        "X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature"}


def test_9_no_order_frame_is_ever_emitted(rig):
    result = rig.run_collector()
    assert result["orders_emitted"] == 0
    sent = json.loads(rig.record.read_text())["frames_in"]
    assert sent, "the collector sent nothing at all"
    for raw in sent:
        msg = json.loads(raw)
        assert set(msg) <= {"subscribe", "unsubscribe"}, msg
        for verb in ("order", "cancel", "modify", "close-position", "price",
                     "size", "side"):
            assert verb not in raw.lower(), raw


def test_10_no_real_polymarket_hostname_is_ever_contacted(rig):
    rig.run_collector()
    assert not rig.marker.exists()
    # And prove the guard is armed rather than merely silent: it must refuse a
    # real hostname when asked directly, in this same interpreter.
    sys.path.insert(0, str(HOSTGUARD))
    try:
        import importlib

        import sitecustomize
        importlib.reload(sitecustomize)
        with pytest.raises(Exception) as exc:
            socket.getaddrinfo("api.polymarket.us", 443)
        assert "refusing to contact" in str(exc.value)
        with pytest.raises(Exception):
            socket.getaddrinfo("clob.polymarket.com", 443)
    finally:
        # Restore the real resolver for the rest of the session.
        socket.getaddrinfo = sitecustomize._real_getaddrinfo
        socket.create_connection = sitecustomize._real_create_connection
        socket.socket.connect = sitecustomize._real_connect
        sys.path.remove(str(HOSTGUARD))
        del sys.modules["sitecustomize"]


def test_the_artifact_the_collector_actually_ran_from_has_no_sdk():
    """The process under test ran from build/, so census build/, not the repo."""
    assert ARTIFACT.exists(), "build the artifact first"
    names = [p.name for p in ARTIFACT.rglob("*")]
    for banned in ("polymarket_us", "py_clob_client", "nacl", "live_executor"):
        assert not [n for n in names if banned in n], banned
    assert shutil.which(sys.executable)
