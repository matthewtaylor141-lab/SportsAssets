"""Offline tests for the Colab launcher. NOTHING CONTACTS A VENUE.

The notebook is a launcher, so what has to be proven about it is narrow but
sharp:

  1. the instrument it carries is byte-identical to the frozen one
  2. the fingerprint gate actually STOPS on a mismatch -- a gate that only
     prints a warning is not a gate
  3. the launcher cannot reach inside the instrument: it passes command-line
     arguments and nothing else
  4. the capture configuration it passes is the authorised one, and
     custom_feature_enabled is off by simply never being passed
  5. the market-selection rule picks before the capture, from different
     events, and refuses rather than degrading when too few markets qualify
  6. the final step names exactly one file to send back

Run:  python3 -m pytest test_run836b_colab.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
NOTEBOOK = HERE / "RUN836B_COLAB.ipynb"
SCRIPT = HERE / "run836b_clob_capture.py"

FROZEN_SHA256 = "7931b54aef42f31b5f4118bf410340eddd71549125153ccbd9b33df29794760b"

pytestmark = pytest.mark.skipif(not NOTEBOOK.exists(), reason="notebook absent")


def _cells():
    return json.loads(NOTEBOOK.read_text())["cells"]


def _code_cells():
    return [("".join(c["source"])) for c in _cells() if c["cell_type"] == "code"]


def _cell_with(fragment: str) -> str:
    hits = [s for s in _code_cells() if fragment in s]
    assert hits, f"no code cell contains {fragment!r}"
    return hits[0]


# =====================================================================
# 1-2. THE INSTRUMENT IT CARRIES
# =====================================================================
def test_1_every_code_cell_is_valid_python():
    for i, src in enumerate(_code_cells()):
        compile(src, f"cell{i}", "exec")


def test_2_the_embedded_instrument_is_byte_identical_to_the_frozen_one():
    src = _cell_with("_B64")
    b64 = "".join(re.findall(r'^\s*"([A-Za-z0-9+/=]+)"\s*$', src, re.M))
    payload = base64.b64decode(b64)
    assert hashlib.sha256(payload).hexdigest() == FROZEN_SHA256
    assert payload == SCRIPT.read_bytes(), (
        "the notebook carries a different script than the repo's frozen one")


def test_3_the_fingerprint_gate_stops_on_a_mismatch(tmp_path):
    """A gate that prints and continues is not a gate.

    The cell is run twice for real: once as written, and once with the
    expected hash swapped for a wrong one. The second run must exit non-zero
    and must NOT leave a usable script behind -- if it merely warned, a
    tampered instrument would go on to record the experiment.
    """
    src = _cell_with("FROZEN_INSTRUMENT_HASH_MISMATCH")

    good = tmp_path / "good.py"
    good.write_text(src)
    r = subprocess.run([sys.executable, str(good)], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FROZEN_INSTRUMENT_HASH_VERIFIED" in r.stdout
    assert (tmp_path / "run836b_clob_capture.py").exists()

    tampered = tmp_path / "bad.py"
    tampered.write_text(src.replace(FROZEN_SHA256, "0" * 64))
    r2 = subprocess.run([sys.executable, str(tampered)], cwd=tmp_path,
                        capture_output=True, text=True, timeout=120)
    assert r2.returncode != 0, "a hash mismatch did not stop the notebook"
    assert "FROZEN_INSTRUMENT_HASH_MISMATCH" in (r2.stdout + r2.stderr)


# =====================================================================
# 4-6. THE LAUNCHER CANNOT REACH INSIDE THE INSTRUMENT
# =====================================================================
def test_4_the_instrument_is_launched_as_a_child_process_only():
    """No import, no exec, no patching -- arguments are the whole interface.

    This is what keeps 'launcher' honest. Importing the module would put the
    instrument inside the notebook's own process, where a later cell could
    rebind anything in it; it would also break outright, because the script
    calls asyncio.run() and a Colab kernel already owns an event loop.
    """
    src = _cell_with("run836b_clob_capture.py\",")
    assert "subprocess.run" in src

    joined = "\n".join(_code_cells())
    for banned in ("import run836b_clob_capture",
                   "from run836b_clob_capture",
                   "importlib",
                   "exec(",
                   "eval(",
                   "setattr(",
                   "monkeypatch"):
        assert banned not in joined, f"the launcher reaches inside: {banned!r}"


def test_5_the_capture_configuration_is_the_authorised_one():
    src = _cell_with("--session-seconds")
    for flag, value in (("--sessions", "3"),
                        ("--session-seconds", "75"),
                        ("--pause-seconds", "5"),
                        ("--rest-interval", "15"),
                        ("--heartbeat-interval", "10")):
        assert f'"{flag}", "{value}"' in src, f"{flag} is not {value}"


def test_6_custom_feature_enabled_is_off_by_never_being_passed():
    """The baseline the owner fixed. Absence is the mechanism, not a false flag."""
    joined = "\n".join(_code_cells())
    assert "--custom-feature-enabled" not in joined, (
        "the notebook passes the flag; the baseline capture must not enable it")


def test_7_the_tokens_are_passed_explicitly_one_flag_each():
    src = _cell_with("--token-id")
    assert 'cmd += ["--token-id", t]' in src
    assert "for t in TOKEN_IDS" in src


# =====================================================================
# 8-10. SELECTION HAPPENS BEFORE THE CAPTURE, AND REFUSES TO DEGRADE
# =====================================================================
def _run_selection(rows, tmp_path, target=4):
    """Run the real selection cell against a synthetic market list.

    httpx is stubbed so nothing leaves the machine; only the filter, the rank
    and the per-event rule are exercised -- which is the part that could bend
    the experiment.
    """
    src = _cell_with("SELECTION_RECORD")
    stub = f"""
import json, sys, types

class _Resp:
    status_code = 200
    def json(self): return {json.dumps(rows)!r} and json.loads({json.dumps(json.dumps(rows))})

class _Client:
    def __init__(self, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, url, params=None): return _Resp()

httpx = types.ModuleType("httpx")
httpx.Client = _Client
sys.modules["httpx"] = httpx
"""
    runner = tmp_path / "sel.py"
    runner.write_text(stub + "\n" + src + "\nprint('TOKENS=' + json.dumps(TOKEN_IDS))\n")
    return subprocess.run([sys.executable, str(runner)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)


def _market(i, *, vol, event, sport=True, closed=False, accepting=True):
    return {
        "question": f"Will Team {i} win the NFL game?" if sport else f"Will bond yields move {i}?",
        "slug": f"market-{i}",
        "closed": closed,
        "active": True,
        "acceptingOrders": accepting,
        "enableOrderBook": True,
        "volume24hr": vol,
        "clobTokenIds": json.dumps([f"tok{i}a", f"tok{i}b"]),
        "events": [{"id": event, "slug": f"event-{event}",
                    "tags": [{"slug": "nfl" if sport else "rates"}]}],
    }


def test_8_selection_takes_the_busiest_markets_from_different_events(tmp_path):
    rows = [
        _market(1, vol=100, event="E1"),
        _market(2, vol=900, event="E2"),
        _market(3, vol=800, event="E2"),      # same event as 2 -- must be skipped
        _market(4, vol=700, event="E3"),
        _market(5, vol=600, event="E4"),
        _market(6, vol=99999, event="E5", sport=False),   # not a sport
        _market(7, vol=99999, event="E6", closed=True),   # closed
        _market(8, vol=99999, event="E7", accepting=False),
    ]
    r = _run_selection(rows, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    tokens = json.loads(r.stdout.split("TOKENS=")[1].splitlines()[0])

    # busiest first, one per event, first token of each, non-sports and
    # non-tradeable excluded entirely
    assert tokens == ["tok2a", "tok4a", "tok5a", "tok1a"], tokens
    assert "tok3a" not in tokens, "two markets from the same event were taken"
    assert not any(t.startswith(("tok6", "tok7", "tok8")) for t in tokens)


def test_9_too_few_eligible_markets_is_a_refusal_not_a_smaller_capture(tmp_path):
    """The capture is never quietly weakened to fit what happens to be open."""
    rows = [_market(1, vol=100, event="E1"), _market(2, vol=90, event="E2")]
    r = _run_selection(rows, tmp_path)
    assert r.returncode != 0, "it proceeded with fewer than three markets"
    out = r.stdout + r.stderr
    assert "fewer than the 3 required" in out
    assert "not weakened" in out


def test_9b_the_accepting_orders_gate_is_enforced_when_the_surface_reports_it(tmp_path):
    """acceptingOrders is a REAL current field, so when present it must bind.

    Field name confirmed on the current SDK's own gamma Market model
    (models/gamma/market.py:71-73, validation_alias "acceptingOrders"). It is
    declared bool | None, so its presence is decided per response rather than
    assumed -- when rows carry it, a market that is not accepting orders is
    dropped rather than merely un-penalised.
    """
    rows = [
        _market(1, vol=9999, event="E1", accepting=False),   # busiest, but halted
        _market(2, vol=500, event="E2"),
        _market(3, vol=400, event="E3"),
        _market(4, vol=300, event="E4"),
    ]
    r = _run_selection(rows, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DISCOVERY_ACCEPTING_ORDERS_GATE = ENFORCED" in r.stdout
    tokens = json.loads(r.stdout.split("TOKENS=")[1].splitlines()[0])
    assert "tok1a" not in tokens, "a market that is not accepting orders was taken"
    assert tokens == ["tok2a", "tok3a", "tok4a"], tokens


def test_9c_the_gate_labels_itself_not_identified_when_the_surface_omits_it(tmp_path):
    """No field, no invented substitute -- and no silent pass either.

    If the response carries no accepting-orders field at all, the launcher says
    so in those words and does not apply the filter. Nothing is assumed in its
    place: the public feed and price-book preflight still have to succeed, and
    the capture itself still has to produce both evidence streams.
    """
    rows = []
    for i, (vol, ev) in enumerate([(500, "E1"), (400, "E2"), (300, "E3")], start=1):
        m = _market(i, vol=vol, event=ev)
        del m["acceptingOrders"]
        rows.append(m)
    r = _run_selection(rows, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DISCOVERY_ACCEPTING_ORDERS_GATE = NOT_IDENTIFIED" in r.stdout
    tokens = json.loads(r.stdout.split("TOKENS=")[1].splitlines()[0])
    assert tokens == ["tok1a", "tok2a", "tok3a"], tokens


def test_9d_the_gate_uses_only_field_names_the_current_sdk_declares():
    """Every VENUE field the launcher reads must be a real current name.

    Only reads from the venue payload count: m/ev/tag/payload .get("...") and
    the names passed to _num(m, ...). The launcher's own internal dict keys
    (vol24, liq, token, event) are its own vocabulary, not the venue's, and
    sweeping them in would be checking the wrong thing.

    Names below are declared on the current SDK's gamma models -- Market
    (models/gamma/market.py) for the market fields, and the keyset envelope
    keys for the response wrapper. Nothing here is invented.
    """
    src = _cell_with("SELECTION_RECORD")

    reads = set(re.findall(r'\b(?:m|ev|tag|payload)\.get\(\s*["\']([^"\']+)["\']', src))
    for call in re.findall(r"_num\(m,([^)]*)\)", src):
        reads |= set(re.findall(r'["\']([^"\']+)["\']', call))
    reads |= set(re.findall(r'ACCEPTING_FIELD\s*=\s*["\']([^"\']+)["\']', src))

    declared_by_current_sdk = {
        # gamma Market
        "acceptingOrders", "enableOrderBook", "active", "closed",
        "clobTokenIds", "clob_token_ids", "volume24hr", "volume24hrClob",
        "volumeNum", "volume", "liquidityNum", "liquidity",
        "gameStartTime", "sportsMarketType", "events", "tags",
        "slug", "question", "description", "category", "seriesSlug",
        # nested event / tag
        "id", "title", "label",
        # keyset / list response envelopes
        "data", "markets", "results",
    }
    unknown = reads - declared_by_current_sdk
    assert not unknown, f"discovery reads undeclared venue field name(s): {unknown}"
    # the gate's own field must actually be among them
    assert "acceptingOrders" in reads


def test_10_the_selection_rule_cannot_see_the_feed():
    """Ranking must use pre-capture market metadata only.

    If the rule ever consulted anything the websocket said, the choice of
    markets could be tuned to the result. The ranking keys are asserted here
    so that change would have to be deliberate.
    """
    src = _cell_with("SELECTION_RECORD")
    assert 'e["vol24"]' in src and 'e["liq"]' in src
    for feed_word in ("price_change", "websocket", "frames", "event_type",
                      "book\"", "hash"):
        assert feed_word not in src, (
            f"the selection rule references {feed_word!r} -- it must not look "
            "at anything the feed sends")


# =====================================================================
# 11-13. THE HAND-BACK
# =====================================================================
def test_11_the_final_step_names_exactly_one_file_to_return():
    src = _cell_with("SEND THIS ONE FILE BACK")
    assert "archive.name" in src
    assert "files.download" in src, "no browser download was offered"
    assert "sidebar" in src, "no fallback for a blocked browser download"


def test_12_the_notebook_reports_the_verdict_verbatim():
    src = _cell_with("CAPTURE_COMPLETE_FOR_RECONSTRUCTION")
    assert 'manifest["CAPTURE_COMPLETE_FOR_RECONSTRUCTION"]' in src, (
        "the verdict must be read from the instrument's manifest, not recomputed")
    assert "incomplete_reasons" in src


def test_13_the_notebook_does_not_interpret_the_protocol():
    """It collects evidence. Any verdict about semantics would be interpretation.

    The mandated field name CAPTURE_COMPLETE_FOR_RECONSTRUCTION is removed
    before scanning. It contains "reconstruct" but asserts nothing about the
    protocol -- it reports whether both evidence streams exist. Scanning a
    required identifier for prose words is the same name-vs-meaning mistake
    this project has hit before; the name is excluded by its exact spelling so
    the word is still caught anywhere else.
    """
    joined = "\n".join(_code_cells())
    joined = joined.replace("CAPTURE_COMPLETE_FOR_RECONSTRUCTION", "")
    joined = joined.lower()
    for banned in ("full replacement", "is a delta", "absolute size",
                   "reconstruct", "continuity", "sha1", "profit", "pnl",
                   "mirror_live", "pmus"):
        assert banned not in joined, f"the notebook interprets: {banned!r}"


def test_14_no_credential_surface_anywhere_in_the_notebook():
    joined = "\n".join(_code_cells()).lower()
    for banned in ("api_key", "apikey", "secret", "private_key", "authorization",
                   "bearer", "wallet", "passphrase", "signature",
                   "polymarket.us", "/v1/orders", "create_order", "place_order"):
        assert banned not in joined, f"credential/order surface present: {banned!r}"


def test_15_the_only_hosts_the_notebook_names_are_the_two_public_ones_plus_discovery():
    joined = "\n".join(_code_cells())
    hosts = set(re.findall(r"https?://([A-Za-z0-9.\-]+)|wss://([A-Za-z0-9.\-]+)", joined))
    flat = {h for pair in hosts for h in pair if h}
    assert flat == {
        "ws-subscriptions-clob.polymarket.com",   # the capture's feed
        "clob.polymarket.com",                    # the capture's REST witness
        "gamma-api.polymarket.com",               # discovery only, not evidence
    }, flat
