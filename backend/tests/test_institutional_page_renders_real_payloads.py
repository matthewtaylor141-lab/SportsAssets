"""THE INSTITUTIONAL PAGE RENDERS THE REAL ROUTE PAYLOADS.

backend/tests/fixtures/institutional/{ok,empty,unavailable}/<read>.json are
responses captured from the REAL routes (FastAPI TestClient against a
migrated scratch database; see fixtures/institutional/capture.py):

  ok           seeded the way the route tests seed (intel shadow cycle,
               Karen challenges through their whole lifecycle, the
               small-live chain, closed paper and actual postmortems)
  empty        the same database before seeding: every intel envelope
               EMPTY (NO_SHADOW_RUN_YET), no Karen challenge, no finding
  unavailable  the routes against a database that does not exist: the
               intel envelopes say UNAVAILABLE, the other reads fail with
               HTTP 5xx and their reason

The page's own render code (frontend/public/command/institutional.js) runs
under node against each set (fixtures/institutional/render.js). Every
section renders the real values, or "UNAVAILABLE — <why>" with the server's
reason -- never a fabricated 0; PAPER and ACTUAL are never summed; every
SHADOW component is labelled SHADOW and never called LIVE.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import shutil
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FX = HERE / "fixtures" / "institutional"
JS = (HERE.parents[1] / "frontend" / "public" / "command"
      / "institutional.js").read_text()
SETS = ("ok", "empty", "unavailable")
SECTIONS = ("paper", "actual", "portfolio", "derek", "xavier", "audrey",
            "karen", "allocator", "risk", "calibration", "execution", "p5",
            "coverage", "quality", "collab")
SHADOW_SECTIONS = ("portfolio", "allocator", "risk", "calibration")
SHADOW_PILL = "SHADOW · NO AUTHORITY"


def fx(s, key):
    return json.loads((FX / s / ("%s.json" % key)).read_text())


def text(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


@pytest.fixture(scope="module")
def rendered():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    got = subprocess.run(["node", str(FX / "render.js")]
                         + [str(FX / s) for s in SETS],
                         capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_the_fixtures_are_the_pages_reads():
    paths = dict(re.findall(r"(\w+): \{path: '([^']+)'", JS))
    assert paths, "READS not found in the page script"
    for s in SETS:
        for key, path in paths.items():
            f = fx(s, key)
            assert f["route"].split("?")[0] == path, (s, key, f["route"])
    assert paths["p5"] == "/api/command/p5/evidence"
    assert paths["karenChallenges"] == "/api/command/karen/challenges"


def test_every_section_renders_in_every_set(rendered):
    for s in SETS:
        r = rendered[s]
        assert r["missing"] == [], (s, r["missing"])
        assert set(r["sections"]) == set(SECTIONS), s
        for sid, h in r["sections"].items():
            assert "could not be rendered" not in h, (s, sid, text(h)[:400])
        q = text(r["questions"])
        for question in ("What does BETTOR own?", "Why?",
                         "What is it worth now?", "What can go wrong?",
                         "Is BETTOR adding value?"):
            assert question in q, (s, question)
        assert "could not be rendered" not in q


def test_shadow_is_labelled_and_never_called_live(rendered):
    for s in SETS:
        for sid in SHADOW_SECTIONS:
            h = rendered[s]["sections"][sid]
            head = h.split("</h2>")[0]
            assert SHADOW_PILL in head, (s, sid)
            assert not re.search(r"\bLIVE\b", text(h)), (s, sid)
    # the top strip labels each shadow figure it uses
    q = rendered["ok"]["questions"]
    assert q.count(SHADOW_PILL) >= 3


# ── the populated set: the real field names reach the screen ─────────

def test_paper_and_actual_read_their_real_fields_and_are_never_summed(
        rendered):
    secs, q = rendered["ok"]["sections"], text(rendered["ok"]["questions"])
    risk = fx("ok", "risk")["body"]["data"]
    eq = risk["PAPER"]["data"]["equity"]["current_equity_usd"]
    paper = text(secs["paper"])
    assert "PAPER — SIMULATED" in paper and "$%s" % format(eq, ",.2f") in paper
    acct = fx("ok", "smallLive")["body"]["account"]
    actual = text(secs["actual"])
    assert acct["status"] == "AVAILABLE"
    assert "ACTUAL — VENUE" in actual
    assert "$%s" % format(acct["current_balance_usd"], ",.2f") in actual
    assert "$%s" % format(acct["buying_power_usd"], ",.2f") in actual
    pm = fx("ok", "postmortems")["body"]
    p_real = pm["paper"]["summary"]["realized_pnl_usd"]
    a_real = pm["actual"]["summary"]["realized_pnl_usd"]
    assert p_real and a_real
    assert "$%s" % format(p_real, ",.2f") in q
    assert "$%s" % format(a_real, ",.2f") in q
    summed = "$%s" % format(round(p_real + a_real, 2), ",.2f")
    for h in [q] + [text(v) for v in secs.values()]:
        assert summed not in h
    assert "never summed" in q
    # Xavier's real positions split by position_kind
    xm = fx("ok", "xavierMgmt")["body"]["positions"]
    n_paper = sum(1 for p in xm if p["position_kind"] == "PAPER"
                  and p["state"] == "OPEN")
    assert "%d open" % n_paper in q


def test_the_top_strip_answers_the_five_questions_from_real_fields(rendered):
    q = text(rendered["ok"]["questions"])
    reg = fx("ok", "regime")["body"]["data"]
    p5 = fx("ok", "p5")["body"]
    assert reg["recommendation"] in q
    assert p5["verdict"] in q
    assert p5["first_blocking_external"]["predicate"] in q
    prof = fx("ok", "quality")["body"]["profitability"]
    assert "PAPER %s" % prof["paper"] in q
    kp = fx("ok", "karen")["body"]["metrics"]["metrics"][
        "challenge_precision"]
    assert "(%d/%d)" % (kp["numerator"], kp["denominator"]) in q
    # no open position carries an entry thesis in this read: said so
    assert "UNAVAILABLE — NO_ENTRY_THESIS" in q
    # open paper positions are not marked by any read: said so, not $0
    assert "UNAVAILABLE — open paper positions are not marked" in q


def test_karen_shows_every_challenge_field_and_her_metrics(rendered):
    h = rendered["ok"]["sections"]["karen"]
    t = text(h)
    rows = fx("ok", "karenChallenges")["body"]["challenges"]["data"]
    assert {r["state"] for r in rows} >= {"OPEN", "RESPONDED", "UPHELD",
                                          "REJECTED"}
    for label in ("Target agent", "Target decision", "Evidence", "Category",
                  "Peer response", "Independent evaluation",
                  "False-block outcome", "Downstream impact"):
        assert t.count(label) >= len(rows), label
    for r in rows:
        for v in (r["challenge_id"], r["target_agent"], r["target_id"],
                  r["detector"], r["severity"], r["state"]):
            assert v in t, (r["challenge_id"], v)
        for e in r["evidence_refs"]:
            assert e["id"] in t
        if r["response"]:
            assert r["response"] in t and r["response_stance"] in t
        else:
            assert "awaiting %s's response" % r["target_agent"] in t
        if r["outcome"]:
            assert r["outcome_reason"] in t and r["resolved_by"] in t
        if r["false_block"] is True:
            assert "FALSE BLOCK" in t
        if r["improvement_finding_id"]:
            assert r["improvement_finding_id"] in t
            for k, v in (r["downstream_impact"] or {}).items():
                assert "%s: %s" % (k, v) in t
    met = fx("ok", "karen")["body"]["metrics"]["metrics"]
    for name, m in met.items():
        assert name.replace("_", " ") in t
        if m["measurable"] and name != "time_to_challenge":
            assert "%s / %s" % (m["numerator"], m["denominator"]) in t, name
    tt = met["time_to_challenge"]
    assert "%g s / %g" % (tt["numerator"], tt["denominator"]) in t


def test_the_p5_section_reads_the_runtime_evaluation(rendered):
    t = text(rendered["ok"]["sections"]["p5"])
    p5 = fx("ok", "p5")["body"]
    assert p5["verdict"] in t and p5["rule"] in t
    fbe = p5["first_blocking_external"]
    assert fbe["predicate"] in t and fbe["reason"] in t
    assert fbe["action"] in t
    assert p5["first_blocking"]["predicate"] in t
    for p in p5["predicates"]:
        assert p["predicate"] in t and p["status"] in t
    tot = p5["runtime_evidence"]["same_book"]["totals"]
    assert "Same-book comparable samples %s of %s needed" % (
        tot["comparable"], tot["supported_needs"]["min_comparable"]) in t
    assert p5["runtime_evidence"]["same_book"]["status"] in t
    assert p5["runtime_evidence"]["stream"]["status"] in t
    if tot["agree_rate"] is None:
        assert "Same-book agree rate UNAVAILABLE" in t


def test_the_shadow_sections_read_the_envelopes(rendered):
    secs = rendered["ok"]["sections"]
    al = fx("ok", "allocator")["body"]
    t = text(secs["allocator"])
    assert al["run_id"] in t
    assert "$%s" % format(al["data"]["sleeve_usd"], ",.2f") in t
    for a in al["data"]["allocation"]:
        assert a["binding_constraint"] in t
    t = text(secs["portfolio"])
    attr = fx("ok", "attribution")["body"]
    s = attr["data"]["summary"]
    assert "$%s" % format(s["PAPER"]["model_edge_usd"], ",.2f") in t
    # ACTUAL summary unmeasured: the reason, never $0
    assert "UNAVAILABLE — %s" % s["ACTUAL"]["unmeasured"][
        "model_edge_usd"] in t
    t = text(secs["risk"])
    assert "never summed" in t
    rp = fx("ok", "risk")["body"]["data"]
    assert "$%s" % format(rp["ACTUAL"]["data"]["gross_exposure_usd"],
                          ",.2f") in t
    assert rp["ACTUAL"]["data"]["equity"]["unmeasured"][
        "current_equity_usd"] in t
    t = text(secs["calibration"])
    ov = fx("ok", "calibration")["body"]["data"]["overall"]
    for src, o in ov.items():
        assert src in t and str(o["brier"]) in t


def test_coverage_quality_audrey_and_collaboration_use_real_fields(rendered):
    secs = rendered["ok"]["sections"]
    cov = fx("ok", "coverage")["body"]
    t = text(secs["coverage"])
    for stage in cov["stages"]:
        assert stage.replace("_", " ") in t
    for lg in cov["days"][0]["leagues"]:
        assert lg["league_name"] in t
    t = text(secs["quality"])
    for ms in fx("ok", "quality")["body"]["domains"].values():
        for m in ms:
            assert m["name"] in t
    t = text(secs["audrey"])
    pm = fx("ok", "postmortems")["body"]
    for b in ("paper", "actual"):
        assert "$%s" % format(pm[b]["summary"]["realized_pnl_usd"],
                              ",.2f") in t
    checks = fx("ok", "risk")["body"]["audrey_checks"]
    assert "%d of %d metrics agree" % (
        sum(1 for c in checks if c["agrees"]), len(checks)) in t
    t = text(secs["collab"])
    for f in fx("ok", "findings")["body"]["findings"]["data"]:
        assert f["title"] in t and f["stage"] in t


# ── EMPTY and UNAVAILABLE: reasons, never numbers ────────────────────

def _no_money(t):
    t = t.replace("$500,000 account", "")
    return not re.search(r"\$\d", t)


def test_empty_envelopes_say_why_and_show_no_figure(rendered):
    secs = rendered["empty"]["sections"]
    for key in ("allocator", "calibration", "attribution", "sizing",
                "regime"):
        assert fx("empty", key)["body"]["status"] == "EMPTY"
    for sid in SHADOW_SECTIONS:
        t = text(secs[sid])
        assert "NO_SHADOW_RUN_YET" in t, sid
        assert _no_money(t), (sid, t[:400])
    t = text(secs["karen"])
    assert "EMPTY — NO_CHALLENGE_MATCHES" in t
    met = fx("empty", "karen")["body"]["metrics"]["metrics"]
    for m in met.values():
        assert m["value"] is None
        assert "UNAVAILABLE — %s" % m["why"] in t
    # the postmortem read sends realized_pnl_usd 0 for an EMPTY book: the
    # page shows the reason, not $0.00
    pm = fx("empty", "postmortems")["body"]
    assert pm["paper"]["status"] == "EMPTY"
    t = text(secs["audrey"])
    assert "UNAVAILABLE — %s" % pm["paper"]["why"] in t
    assert "$0.00" not in t
    assert "EMPTY — NO_FINDING_HAS_ENTERED_THE_COLLABORATION_LOOP" in text(
        secs["collab"])


def test_unavailable_reads_say_why_and_show_no_figure(rendered):
    r = rendered["unavailable"]
    for key in ("intel", "allocator", "calibration", "attribution", "sizing",
                "risk", "regime"):
        body = fx("unavailable", key)["body"]
        assert fx("unavailable", key)["http"] == 200
        assert body["status"] == "UNAVAILABLE" and body["data"] is None
    for sid, h in r["sections"].items():
        t = text(h)
        assert "UNAVAILABLE — " in t, sid
        assert _no_money(t), (sid, t[:400])
        assert '<td class="num">0</td>' not in h, sid
    for sid in SHADOW_SECTIONS:
        assert "InvalidCatalogNameError" in text(r["sections"][sid])
    q = text(r["questions"])
    assert _no_money(q)
    assert "HTTP 503 · READ_FAILED" in q
    assert not re.search(r"\b0 open\b", q)


def test_the_page_never_invents_a_figure_for_a_missing_one():
    assert "UNAVAILABLE — " in JS
    assert SHADOW_PILL in JS
    # the page never computes PAPER + ACTUAL
    assert not re.search(r"paper\w*\s*\+\s*actual|actual\w*\s*\+\s*paper",
                         JS, re.I)
