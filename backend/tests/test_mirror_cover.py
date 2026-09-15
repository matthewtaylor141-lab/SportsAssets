"""MIRRORCOVER, to-a-tee Phase 0 (owner order 2026-09-02 "mirror the
whales to a tee"): the read-only admin route the runner job reads, the
probe step's new MIRROR* lines, and the runner job itself -- driven here
against a fake venue SDK and empty inputs, so a payload the endpoint
never served still prints its tags and every class the job can print is
reached once."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "engine-diagnostic.yml"


def _wf() -> str:
    return WF.read_text()


def test_the_route_is_admin_gated_read_only_and_beside_the_shadow_route():
    import ast
    import inspect
    import types

    sys.modules.setdefault("pywebpush", types.SimpleNamespace(
        webpush=None, WebPushException=Exception))
    from sportsassets.api import app as app_mod

    tree = ast.parse(inspect.getsource(app_mod.admin_mirror_cover))
    node = tree.body[0]
    decos = node.decorator_list
    assert any(isinstance(d, ast.Call) and any(k.arg == "dependencies" for k in d.keywords)
               for d in decos)
    assert any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "get" for d in decos)
    src = inspect.getsource(app_mod.admin_mirror_cover)
    assert '"/api/admin/mirror-cover"' in src and "mirror_cover_report" in src
    for banned in ("submit_fok", "cancel_order", "close_position", "execute_manual", "maybe_execute"):
        assert banned not in src
    app_src = pathlib.Path(app_mod.__file__).read_text()
    assert app_src.index('"/api/admin/mirror-shadow"') < app_src.index('"/api/admin/mirror-cover"')
    # the report module the route calls never reaches for an order either
    from sportsassets.analytics import mirror_report as mr
    rep = inspect.getsource(mr)
    for banned in ("submit_fok", "cancel_order", "close_position", "execute_manual",
                   "maybe_execute", "mirror_exit", "position_side("):
        assert banned not in rep, banned


def _probe_statements() -> list[str]:
    wf = _wf()
    blk = wf[wf.index("# MIRROR-P0-LINES-BEGIN"):wf.index("# MIRROR-P0-LINES-END")]
    stmts, cur = [], ""
    for ln in blk.splitlines():
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        if t.endswith("\\"):
            cur += t[:-1] + " "
            continue
        stmts.append((cur + t).strip())
        cur = ""
    assert cur == "", "a continuation line without its next line"
    return stmts


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_every_new_probe_jq_line_prints_on_an_empty_endpoint(tmp_path):
    from sportsassets.analytics import mirror_report as mr
    stmts = _probe_statements()
    tags = {"MIRRORUNMAP", "MIRRORUNMAPMKT", "MIRRORSRC", "MIRRORFAM", "MIRRORSIGN", "MIRRORFILL",
            "MIRRORSHORT", "MIRRORSNAP", "MIRRORWOULD", "MIRRORDEAD"}
    seen = {re.search(r'"  (MIRROR[A-Z]+)', s).group(1) for s in stmts}
    assert seen == tags, seen ^ tags
    per_row = {"MIRRORUNMAPMKT"}
    # a real payload: the report over a fixture, so the row lines print too
    latest = [{"whale": "rn1", "condition_id": "0xabc", "us_market_slug": None, "reason": "unmapped",
               "detail": json.dumps({"his_slug": "atp-a-b-2026-09-02", "title": "A vs B", "sport": "tennis",
                                     "family": "moneyline", "explain": "no_key_intersection",
                                     "notional_6h": 12.5, "gross_sh": 30, "outcome_null": 0})},
              {"whale": "rn1", "condition_id": "0xdef", "us_market_slug": "aec-x", "his_net": -10.0, "mark": 0.4,
               "would_side": "BUY_LONG", "would_fill": True, "ledger_net": 0, "reason": "increase",
               "detail": json.dumps({"map": "premap", "family": "moneyline", "his_gross_usd": 9.0,
                                     "snap_state": "fresh_partial", "snap_age_s": 12,
                                     "target_short": -5, "would_side_short": "SELL_LONG",
                                     "would_fill_short": True, "touched_s": 30})}]
    payload = mr.summarize(latest, latest, {"rn1": {"ratio": 0.05}})
    payload["would_pnl"] = {"lots": 0, "error": "no-settle-fixture"}
    cases = {"obj": "{}", "empty": "", "junk": "<html>502</html>",
             "payload": json.dumps(payload, default=str)}
    for name, body in cases.items():
        f = tmp_path / f"mirror_{name}.json"
        f.write_text(body)
        for s in stmts:
            tag = re.search(r'"  (MIRROR[A-Z]+)', s).group(1)
            cmd = s.replace("/tmp/mirror.json", str(f))
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, (name, tag, r.stderr)
            out = r.stdout
            if name in ("empty", "junk"):
                # an empty or unparseable body is the endpoint being
                # unavailable, said once, never zeros dressed as a reading
                assert out.strip() == f"{tag} unavailable", (name, tag, out)
                continue
            if tag in per_row and name != "payload":
                continue                      # no rows: nothing to print, and no error
            assert out.startswith(f"  {tag} "), (name, tag, out)
            assert "unavailable" not in out, (name, tag, out)
            assert out.count("\n") >= 1
    # the absent endpoint: every line falls back to its named absence
    for s in stmts:
        tag = re.search(r'"  (MIRROR[A-Z]+)', s).group(1)
        cmd = s.replace("/tmp/mirror.json", str(tmp_path / "absent.json"))
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and r.stdout.strip() == f"{tag} unavailable", (tag, r.stdout)
    # the payload case printed the row line and the numbers that matter
    f = tmp_path / "mirror_payload.json"
    outs = "".join(subprocess.run(["bash", "-c", s.replace("/tmp/mirror.json", str(f))],
                                  capture_output=True, text=True, timeout=30).stdout for s in stmts)
    assert "MIRRORUNMAPMKT rn1 atp-a-b-2026-09-02 sport=tennis fam=moneyline why=no_key_intersection" in outs
    assert "MIRRORUNMAP markets=1 usd6h=12.5" in outs
    assert "MIRRORSRC mapped=1 admissible=1/$9.0 share=1" in outs and "premap=1/$9.0" in outs
    assert "MIRRORSIGN neg_markets=1" in outs and "MIRRORSHORT orders=1 resolved=1 fills=1 would_fill_short=1" in outs
    # jq renders a float share as 0.0 or 0 depending on its version: both are the number
    assert re.search(r"MIRRORSNAP markets=1 fresh_complete=0 share=0(\.0)? fresh_partial=1 ", outs), outs
    assert "MIRRORFILL nonlegacy: 1/1 rate=1" in outs
    assert re.search(r"MIRRORDEAD markets=0 usd=0(\.0)? \| touch: n=1 ", outs), outs
    assert "MIRRORWOULD long: lots=0" in outs and "no-settle-fixture" in outs


def test_the_probe_step_kept_every_existing_mirror_line():
    wf = _wf()
    for pinned in (
        '"$BASE/api/admin/mirror-shadow?hours=24"',
        "MIRRORMKT \\(.whale) \\(.us_market_slug // .condition_id[:16]) his_net=",
        '"  MIRRORREAD \\(.reading // "n/a")"',
        'echo "  MIRRORHB unreadable"',
        'echo "  MIRROR unavailable"',
    ):
        assert pinned in wf, pinned
    i, j = wf.index("# MIRROR-P0-LINES-BEGIN"), wf.index("# MIRROR-P0-LINES-END")
    assert wf.index('echo "  MIRRORHB unreadable"') < i < j < wf.index("# LANEWHY")


def test_the_runner_job_is_its_own_job_with_a_budget_and_reads_the_route():
    wf = _wf()
    job = wf[wf.index("\n  mirror-cover:\n"):]
    assert "timeout-minutes: 14" in job and "runs-on: ubuntu-latest" in job
    assert "/api/admin/mirror-cover?whale=rn1&hours=24" in job
    assert "data-api.polymarket.com/activity" in job and "usdcSize" in job
    for tag in ("MIRRORCOVER-TOTAL", "MIRRORCOVER-CELL", "MIRRORCOVER-WHY", "MIRRORCOVER-SRC"):
        assert tag in job
    for cls in ("mapped_ledger:", "listed_on_us_but_unmapped:", "listed_closed", "not_listed_on_us",
                "undiagnosed:budget", "undated", "null_condition"):
        assert cls in job, cls
    # every venue read is paced, and "not listed" needs all three misses
    assert "time.sleep(PACE)" in job and 'PACE = float(os.environ.get("MIRRORCOVER_PACE_S", "0.35"))' in job
    assert "for s in cands:" in job and "cands[:3]" not in job


def _runner_script() -> str:
    wf = _wf()
    # from the START of the marker's line, so every line keeps the same
    # indent and dedent strips the block's ten spaces
    i = wf.rfind("\n", 0, wf.index("# MIRRORCOVER-RUNNER")) + 1
    body = wf[i:]
    lines = []
    for ln in body.splitlines():
        if ln.strip() == "PY":
            break
        lines.append(ln)
    return textwrap.dedent("\n".join(lines)) + "\n"


_FAKE_SDK = '''
class _Err(Exception):
    pass


class NotFoundError(_Err):
    pass


class _Events:
    def list(self, params):
        if int(params.get("offset") or 0) > 0:
            return {"events": []}
        return {"events": [
            {"title": "Brandon Nakashima vs Alex Michelsen", "slug": "ev-nak-mic",
             "markets": [{"slug": "aec-atp-branak-alemic-2026-09-02", "closed": False,
                          "marketSides": [{"long": True, "price": {"value": "0.31"}},
                                          {"long": False, "price": {"value": "0.69"}}]}]},
            {"title": "Cardiff City FC vs QPR", "slug": "ev-car-qpr",
             "markets": [{"slug": "atc-elc-qpr-car-2026-09-02-car", "closed": False},
                         {"slug": "atc-elc-qpr-car-2026-09-02-qpr", "closed": True}]},
            {"title": "Novak Djokovic vs Carlos Alcaraz", "slug": "ev-djo-alc",
             "markets": [{"slug": "aec-atp-novdjo-caralc-2026-09-02", "closed": False}]},
        ]}


class _Markets:
    calls = []

    def retrieve_by_slug(self, slug):
        _Markets.calls.append(slug)
        if slug == "aec-wta-closed-one-2026-09-01":
            return {"market": {"slug": slug, "closed": True}}
        # the exact lane's derivative forms: a total listed in the SWAPPED
        # team order, a spread listed under his suffix verbatim
        if slug in ("tsc-mlb-bos-nyy-2026-07-22-8pt5", "asc-mlb-nyy-bos-2026-07-22-nyy-neg-1pt5"):
            return {"market": {"slug": slug, "closed": False}}
        raise NotFoundError("404")


class _Search:
    def query(self, params):
        q = (params.get("query") or "").lower()
        print(f"FAKE-SEARCH query={q!r} status={params.get('status')}")
        if "someone" in q:
            return {"events": [{"title": "Someone vs Other", "markets": [{"slug": "aec-x-y-2026-09-02"}]}]}
        # answered only by the second parameter set: the default variant
        # says nothing, the active-status variant lists it
        if "second" in q and params.get("status") == "active":
            return {"events": [{"title": "Second Chance vs Other", "markets": [{"slug": "aec-second-x-2026-09-02"}]}]}
        return {"events": []}


class PolymarketUS:
    def __init__(self):
        self.events, self.markets, self.search = _Events(), _Markets(), _Search()
'''


def _cond(cid, his_slug, title, cands, **kw):
    d = {"condition_id": cid, "his_slug": his_slug, "event_slug": None, "title": title,
         "event_title": None, "outcomes": [], "sport": "tennis", "family": "moneyline",
         "date": (re.search(r"\d{4}-\d{2}-\d{2}", his_slug or "") or [None])[0] if his_slug else None,
         "usd24h": 10.0, "n_fills": 3, "gross_sh": 100.0, "paired_sh": 10.0, "candidates": cands,
         "shadow": None, "map": None, "explain": "no_key_intersection"}
    d.update(kw)
    return d


def _run_runner(tmp_path, cover, acts):
    sdk = tmp_path / "sdk" / "polymarket_us"
    sdk.mkdir(parents=True, exist_ok=True)
    (sdk / "__init__.py").write_text(_FAKE_SDK)
    script = tmp_path / "runner.py"
    script.write_text(_runner_script())
    cj, aj = tmp_path / "cover.json", tmp_path / "acts.json"
    cj.write_text(cover)
    aj.write_text(acts)
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "sdk"), MIRRORCOVER_JSON=str(cj),
               MIRRORCOVER_ACTS=str(aj), MIRRORCOVER_PACE_S="0", MIRRORCOVER_BUDGET_S="60",
               MIRRORCOVER_INDEX_PAGES="3")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_runner_prints_every_class_against_a_fake_venue(tmp_path):
    import time
    now = time.time()
    conds = [
        _cond("0xmapped-premap", "atp-a-b-2026-09-02", "A vs B", ["aec-atp-a-b-2026-09-02"], usd24h=999.0,
              map={"source": "premap", "us_slug": "aec-atp-a-b-2026-09-02", "per_side": False, "map_class": "premap"},
              shadow={"mark": 0.40, "his_long": 100.0, "his_other": 20.0, "explain": None}),
        _cond("0xmapped-ledger", "atp-c-d-2026-09-02", "C vs D", ["aec-atp-c-d-2026-09-02"], usd24h=500.0,
              map={"source": "ledger", "us_slug": "aec-atp-c-d-2026-09-02", "per_side": False,
                   "map_class": "traded:ioc"}),
        _cond("0xindex-hit", "atp-nakashi-michels-2026-09-02", "US Open ATP: Brandon Nakashima vs Alex Michelsen",
              ["aec-atp-branak-alemic-2026-09-02", "aec-atp-alemic-branak-2026-09-02"], usd24h=400.0,
              explain="no_side_match"),
        _cond("0xindex-closed", "elc-qpr-car-2026-09-02-qpr", "Will QPR win on 2026-09-02?",
              ["atc-elc-qpr-car-2026-09-02-qpr"], usd24h=300.0, sport="soccer"),
        _cond("0xtitle-hit", "atp-djokovic-alcaraz-2026-09-02", "Novak Djokovic vs Carlos Alcaraz",
              ["aec-atp-wrong-guess-2026-09-02"], usd24h=250.0),
        _cond("0xdirect-closed", "wta-closed-one-2026-09-01", "Closed One vs Two",
              ["aec-wta-notthis-2026-09-01", "aec-wta-closed-one-2026-09-01"], usd24h=200.0),
        _cond("0xnot-listed", "itf-nobody-noone-2026-09-02", "Nobody vs Noone",
              ["aec-itfwo-nob-noo-2026-09-02", "aec-itfme-nob-noo-2026-09-02"], usd24h=150.0),
        _cond("0xsearch-amb", "itf-someone-other-2026-09-02", "Someone vs Other",
              ["aec-itfwo-som-oth-2026-09-02"], usd24h=100.0),
        _cond("0xundated", "us-open-2026-winner", "US Open winner", ["us-open-2026-winner"], usd24h=50.0,
              date=None, family="unknown"),
        _cond("", "x-y-2026-09-02", "X vs Y", ["aec-x-y-2026-09-02"], usd24h=5.0),
    ]
    cover = json.dumps({"whale": "rn1", "hours": 24, "conditions": conds, "markets": len(conds),
                        "null_condition_fills": 3, "map_calls": 4})
    acts = json.dumps([
        {"conditionId": "0xmapped-premap", "side": "BUY", "type": "TRADE", "usdcSize": 100.0, "timestamp": now - 60,
         "slug": "atp-a-b-2026-09-02", "eventSlug": "atp-a-b-2026-09-02", "title": "A vs B"},
        {"conditionId": "0xmapped-premap", "side": "BUY", "type": "TRADE", "usdcSize": 50.0, "timestamp": now - 120},
        {"conditionId": "0xmapped-premap", "side": "SELL", "type": "TRADE", "usdcSize": 999.0, "timestamp": now - 30},
        {"conditionId": "0xmapped-premap", "side": "BUY", "type": "TRADE", "usdcSize": 999.0,
         "timestamp": now - 30 * 3600},
        {"conditionId": "", "side": "BUY", "type": "TRADE", "usdcSize": 1.0, "timestamp": now - 10},
    ])
    out = _run_runner(tmp_path, cover, acts)
    lines = {ln.split()[1] if ln.startswith("MIRRORCOVER ") else None: ln for ln in out.splitlines()}
    by = {}
    for ln in out.splitlines():
        if ln.startswith("MIRRORCOVER ") and " rn1 " in ln:
            cls, cid = ln.split()[1], ln.split()[3]
            by[cid] = (cls, ln)
    assert by["0xmapped-p"][0] == "mapped_premap"
    # dollars: the data-api's BUY usdcSize inside 24 h (100 + 50), our ingest's figure beside it
    assert "usd24h=150.00 usd_db=999.0" in by["0xmapped-p"][1]
    # gross at the shadow's mark: 100 x 0.40 + 20 x 0.60 = 52.00; paired 2 x 10 / 100
    assert "gross=52.0 paired=0.2" in by["0xmapped-p"][1]
    assert by["0xmapped-l"][0] == "mapped_ledger:traded:ioc"
    assert by["0xindex-hi"][0] == "listed_on_us_but_unmapped:no_side_match" and "hit=slug" in by["0xindex-hi"][1]
    assert "us=aec-atp-branak-alemic-2026-09-02" in by["0xindex-hi"][1]
    assert by["0xindex-cl"][0] == "listed_closed" and "hit=slug" in by["0xindex-cl"][1]
    assert by["0xtitle-hi"][0].startswith("listed_on_us_but_unmapped:") and "hit=title:ev-djo-alc" in by["0xtitle-hi"][1]
    assert by["0xdirect-c"][0] == "listed_closed" and "hit=direct" in by["0xdirect-c"][1]
    assert by["0xnot-list"][0] == "not_listed_on_us" and "hit=search:0ev" in by["0xnot-list"][1]
    assert by["0xsearch-a"][0] == "undiagnosed:search_1ev"
    assert by["0xundated"][0] == "undated"
    assert by["-"][0] == "null_condition"
    # every candidate was tried before "not listed" was claimed
    tot = [ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER-TOTAL ")][0]
    assert "markets=10" in tot and "mapped=2/$" in tot and "admissible=1/$" in tot
    assert "mapped_ledger=1/$" in tot and "listed_unmapped=2/$" in tot and "not_listed=1/$" in tot
    assert "listed_closed=2/$" in tot and "undated=1/$" in tot and "undiagnosed=1/$" in tot
    assert "null_condition=3" in tot and "endpoint=ok" in tot and "budget_hit=False" in tot
    assert "direct_reads=" in tot and "search_reads=2" in tot and "paired_share=" in tot
    cells = [ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER-CELL ")]
    assert any(ln.startswith("MIRRORCOVER-CELL tennis|moneyline markets=") for ln in cells)
    assert any(ln.startswith("MIRRORCOVER-CELL soccer|moneyline markets=1") for ln in cells)
    whys = [ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER-WHY ")]
    assert any(ln.startswith("MIRRORCOVER-WHY no_side_match: markets=1") for ln in whys)
    assert "MIRRORCOVER-SRC endpoint=ok conditions=10 data_api_markets=1 data_api_usd=150.0 data_api_null_condition=1" in out
    assert "MIRRORCOVER-SRC index pages=1 markets=4 events=3" in out
    assert lines is not None


def test_the_runner_prints_its_totals_on_an_empty_or_absent_endpoint(tmp_path):
    out = _run_runner(tmp_path, "", "")
    assert "MIRRORCOVER-SRC endpoint=absent conditions=0" in out
    assert "MIRRORCOVER-TOTAL markets=0 usd=0.00" in out and "undiagnosed_share=n/a" in out
    out2 = _run_runner(tmp_path, "{}", "[]")
    assert "MIRRORCOVER-SRC endpoint=empty conditions=0" in out2 and "MIRRORCOVER-TOTAL markets=0" in out2
    # no endpoint but his own fills: the runner classes what the grammar it has allows
    import time
    acts = json.dumps([{"conditionId": "0xfromapi", "side": "BUY", "type": "TRADE", "usdcSize": 42.0,
                        "timestamp": time.time() - 5, "slug": "atp-nakashi-michels-2026-09-02",
                        "eventSlug": "atp-nakashi-michels-2026-09-02", "title": "Nakashima vs Michelsen"}])
    out3 = _run_runner(tmp_path, "", acts)
    assert "MIRRORCOVER-SRC endpoint=absent conditions=1" in out3
    ln = [x for x in out3.splitlines() if x.startswith("MIRRORCOVER ") and " rn1 " in x][0]
    assert "usd24h=42.00" in ln and "cands=3" in ln and "why=endpoint_absent" in ln


def test_the_shadow_still_never_touches_an_order():
    from tests.test_mirror_shadow import test_the_shadow_never_touches_an_order
    test_the_shadow_never_touches_an_order()


# ------------------------------------------ Phase 0 review of the instruments
# (owner order 2026-09-02 "mirror the whales to a tee")

def test_the_runner_tries_the_exact_lanes_derivative_forms(tmp_path):
    from sportsassets.analytics import mirror_report as mr
    total_c = mr.candidate_slugs("New York Yankees vs Boston Red Sox: Total 8.5",
                                 "mlb-nyy-bos-2026-07-22-o8pt5", "mlb-nyy-bos-2026-07-22", ["Over 8.5"])
    spread_c = mr.candidate_slugs("Spread: New York Yankees (-1.5)", "mlb-nyy-bos-2026-07-22-nyy-neg-1pt5",
                                  "mlb-nyy-bos-2026-07-22", ["New York Yankees"])
    assert "tsc-mlb-bos-nyy-2026-07-22-8pt5" in total_c and "asc-mlb-nyy-bos-2026-07-22-nyy-neg-1pt5" in spread_c
    conds = [
        _cond("0xtotal", "mlb-nyy-bos-2026-07-22-o8pt5", "New York Yankees vs Boston Red Sox: Total 8.5",
              total_c, usd24h=300.0, sport="baseball", family="total", explain="type_prefix_filter_emptied"),
        _cond("0xspread", "mlb-nyy-bos-2026-07-22-nyy-neg-1pt5", "Spread: New York Yankees (-1.5)",
              spread_c, usd24h=200.0, sport="baseball", family="spread", explain="no_side_match"),
        # the same total with the candidate set the runner had before the review
        _cond("0xtotal-old", "mlb-nyy-bos-2026-07-22-o8pt5", "New York Yankees vs Boston Red Sox: Total 8.5",
              [s for s in total_c if not s.startswith("tsc-")], usd24h=100.0, sport="baseball", family="total"),
    ]
    cover = json.dumps({"whale": "rn1", "hours": 24, "conditions": conds, "markets": len(conds)})
    out = _run_runner(tmp_path, cover, "[]")
    by = {ln.split()[3]: ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER ") and " rn1 " in ln}
    assert by["0xtotal"].split()[1] == "listed_on_us_but_unmapped:type_prefix_filter_emptied"
    assert "us=tsc-mlb-bos-nyy-2026-07-22-8pt5" in by["0xtotal"] and "hit=direct" in by["0xtotal"]
    assert by["0xspread"].split()[1] == "listed_on_us_but_unmapped:no_side_match"
    assert "us=asc-mlb-nyy-bos-2026-07-22-nyy-neg-1pt5" in by["0xspread"] and "hit=direct" in by["0xspread"]
    # without the derivative forms the same listed total read as not listed
    assert by["0xtotal-ol"].split()[1] == "not_listed_on_us"
    cells = [ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER-CELL ")]
    assert any(ln.startswith("MIRRORCOVER-CELL baseball|spread markets=1") and "listed_on_us_but_unmapped=1/$200.00" in ln
               for ln in cells)


def test_the_runner_search_asks_the_active_variant_after_a_zero(tmp_path):
    # every direct read is a 404 and no index or title hit: the search is
    # the last word, and its first variant answers 0 while the active
    # variant lists the market -- "not listed" must not be claimed off the
    # first zero
    conds = [
        _cond("0xsecond", "itf-second-chance-2026-09-02", "Second Chance vs Other",
              ["aec-second-x-2026-09-02"], usd24h=80.0, explain="no_side_match"),
        _cond("0xnobody", "itf-nobody-noone-2026-09-02", "Nobody vs Noone",
              ["aec-itfwo-nob-noo-2026-09-02"], usd24h=40.0),
    ]
    cover = json.dumps({"whale": "rn1", "hours": 24, "conditions": conds, "markets": len(conds)})
    out = _run_runner(tmp_path, cover, "[]")
    by = {ln.split()[3]: ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER ") and " rn1 " in ln}
    assert by["0xsecond"].split()[1] == "listed_on_us_but_unmapped:no_side_match"
    assert "hit=search" in by["0xsecond"] and "us=aec-second-x-2026-09-02" in by["0xsecond"]
    # both variants were asked, in order, before the answer stood
    asked = [ln for ln in out.splitlines() if ln.startswith("FAKE-SEARCH ") and "second" in ln]
    assert asked == ["FAKE-SEARCH query='second chance vs other' status=None",
                     "FAKE-SEARCH query='second chance vs other' status=active"]
    # a zero from both variants is still "not listed", and is one search read each
    assert by["0xnobody"].split()[1] == "not_listed_on_us" and "hit=search:0ev" in by["0xnobody"]
    asked_nobody = [ln for ln in out.splitlines() if ln.startswith("FAKE-SEARCH ") and "nobody" in ln]
    assert [ln.split("status=")[1] for ln in asked_nobody] == ["None", "active"]
    tot = [ln for ln in out.splitlines() if ln.startswith("MIRRORCOVER-TOTAL ")][0]
    assert "search_reads=2" in tot and "not_listed=1/$40.00" in tot
    # the runner's own text says so
    wf = _wf()
    fn = wf[wf.index("          def search(q):"):wf.index("          # ---- step C")]
    assert "n_last, evs_last = None, []" in fn and "if n_last:" in fn and "return n_last, evs_last" in fn
