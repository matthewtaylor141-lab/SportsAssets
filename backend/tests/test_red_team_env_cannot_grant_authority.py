"""RED TEAM item 42 -- an environment toggle alone can never activate live
money. In a fresh interpreter whose environment sets every authority-
sounding switch to "on", the release still has: SMALL LIVE = SHADOW (a code
constant), no canonical live authorization issued even for an admissible
intent, Kalshi submission refused without the durable control row and a
fresh reconciliation, Adriana / Archer / Allie without order authority, and
the red-team interlock without auto-activation. SMALL_LIVE_MODE is assigned
from a literal everywhere, never from the environment."""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import warnings

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PKG = BACKEND / "sportsassets"

HOSTILE = {
    "SMALL_LIVE_MODE": "LIVE", "BETTOR_SMALL_LIVE": "1",
    "SMALL_LIVE_ENABLED": "1", "LIVE_AUTHORITY": "1", "LIVE_MONEY": "1",
    "KALSHI_LIVE_MONEY": "1", "KALSHI_LIVE": "on", "KALSHI_ENV": "prod",
    "ADRIANA_LIVE": "1", "ADRIANA_SUBMIT": "1", "ADRIANA_MODE": "LIVE",
    "ARCHER_ORDER_AUTHORITY": "1", "ALLIE_ORDER_AUTHORITY": "1",
    "ALLIE_CAPITAL_AUTHORITY": "1", "CAPITAL_AUTHORITY": "1",
    "RED_TEAM_AUTO_ACTIVATE": "1",
}

PROBE = r"""
import json, time
from sportsassets import live_authorization as LA, live_parity as LP
from sportsassets import kalshi_venue as KV
from sportsassets.agents import adriana_arb as AA, archer as AR
out = {}
out["small_live_mode"] = LA.SMALL_LIVE_MODE
out["parity_mode"] = LP.SMALL_LIVE_MODE
tok = LP.issue_live_authorization({"intent_id": "i", "content_sha": "c"},
                                  governance={"admissible": True},
                                  now=time.time())
out["authorization_issued"] = tok is not None
forged = LA.LiveAuthorization(intent_id="i", content_sha="c", issued_at=0.0)
out["forged_authorized"] = LA.authorized(forged)
env = dict(__import__("os").environ)
env[KV.ENABLED_ENV] = "1"
cred = {"state": "PRESENT", "key_fingerprint": "f"}
r = KV.submission_gate(cred, None, None,
                       env_enabled=KV.smalllive_env_enabled(env),
                       now=time.time())
out["kalshi_env_only_refusal"] = None if r is None else str(
    getattr(r, "code", r))
out["adriana_no_authority"] = AA.assert_no_authority()
out["adriana_mode"] = AA.MODE
out["archer_authority"] = AR.AUTHORITY
print(json.dumps(out))
"""


def test_a_hostile_environment_grants_nothing():
    env = dict(os.environ, **HOSTILE)
    env["PYTHONPATH"] = str(BACKEND)
    p = subprocess.run([sys.executable, "-c", PROBE], env=env,
                       capture_output=True, text=True, timeout=120,
                       cwd=str(BACKEND))
    assert p.returncode == 0, p.stderr[-2000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["small_live_mode"] == "SHADOW"
    assert out["parity_mode"] == "SHADOW"
    assert out["authorization_issued"] is False
    assert out["forged_authorized"] is False
    assert out["kalshi_env_only_refusal"] is not None
    assert out["adriana_no_authority"] is True
    assert out["adriana_mode"] == "SHADOW"
    assert out["archer_authority"] == "SHADOW_ONLY"


def _env_call(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv"):
            return True
        if isinstance(n, ast.Name) and n.id in ("getenv", "environ"):
            return True
    return False


def test_small_live_mode_is_never_read_from_the_environment():
    offenders = []
    for f in PKG.rglob("*.py"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [
                    n.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if "SMALL_LIVE_MODE" in names and n.value is not None and \
                        _env_call(n.value):
                    offenders.append("%s:%d" % (f.relative_to(BACKEND),
                                                n.lineno))
    assert offenders == []


def test_the_interlock_never_auto_activates():
    src = (PKG / "redteam" / "readiness.py").read_text()
    assert '"auto_activation": False' in src
    assert '"capital_authority_granted": False' in src
    # the one environment read is the build identity, never a switch
    import re
    reads = re.findall(r"environ\.get\(\"([A-Z_]+)\"|getenv\(\"([A-Z_]+)\"",
                       src)
    assert {a or b for a, b in reads} == {"RENDER_GIT_COMMIT"}
