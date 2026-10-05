"""MOBILE COMMAND (command/mobile.html): the owner's iPhone / iPad app.

Static checks plus the page's pure view models run in node against payloads
shaped like the production endpoints (nothing here opens a socket):

  * READ ONLY: GET only, every read under /api/command/, no stream, no socket;
  * every read is no faster than the desk's minimum for that endpoint
    (command-ops.js MIN_MS) and coverage never faster than 120 s; the heavy
    /api/command/overview is not read at all;
  * a value the API does not serve renders DATA NOT AVAILABLE, never 0 or $0;
    the PAPER tiles read paper.* only -- a real-money legacy-mirror value never
    appears under a PAPER label; Available is available_usd, not cash_usd;
  * the agents are the floor's own seats: no invented agent (no Ariana), no
    invented state (no LISTENING), no invented progress;
  * the service worker serves the app shell only, scoped to mobile.html: it
    never intercepts /api/, never caches /build.json, deletes only its own
    caches;
  * the [hidden] attribute always hides (the install sheet once covered the
    whole app because a display rule beat it);
  * brand: the brand/ icon, no lone letter mark; installing the app does not
    edit the desktop index.html.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND = ROOT / "frontend" / "public" / "command"
HTML = (COMMAND / "mobile.html").read_text()
JS = (COMMAND / "mobile-command.js").read_text()
CSS = (COMMAND / "mobile-command.css").read_text()
SW = (COMMAND / "sw-mobile.js").read_text()
MANIFEST = json.loads((COMMAND / "manifest.webmanifest").read_text())
OPS_JS = (COMMAND / "command-ops.js").read_text()


def _code(js: str) -> str:
    """the script without comments or string literals (so prose cannot trip a check)"""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", "", js)
    return js


def test_the_page_loads_only_the_sign_in_and_its_own_script():
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', HTML)
    assert scripts == ["unlock.js", "mobile-command.js"]
    assert 'name="apple-mobile-web-app-capable" content="yes"' in HTML
    assert 'rel="manifest" href="manifest.webmanifest"' in HTML
    assert 'name="robots" content="noindex,nofollow"' in HTML
    assert "user-scalable=no" not in HTML                        # pinch zoom stays available
    assert 'id="mc-watch-real"' in HTML
    for ref in re.findall(r'(?:href|src)="([^"#:]+)"', HTML):
        if ref.startswith("./") and ref != "./mobile.html" and ref != "./":
            ref = ref[2:]
        if ref in ("./", "./mobile.html"):
            continue
        if ref.endswith(".html") and ref in ("floor.html", "ops.html"):
            assert (COMMAND / ref).exists(), ref
            continue
        assert not ref.startswith("/"), ref                      # relative: works on both hosts
        assert (COMMAND / ref).exists(), ref


def test_the_mobile_layer_is_read_only():
    code = _code(JS)
    verbs = set(re.findall(r"method:\s*'([A-Z]+)'", code))
    assert verbs == {"GET"}, verbs
    for banned in ("EventSource", "WebSocket", "sendBeacon", "XMLHttpRequest", "<form"):
        assert banned not in code, banned
    urls = re.findall(r"url:\s*'([^']+)'", code)
    assert urls and all(u.startswith("/api/command/") for u in urls), urls
    assert "/api/command/overview" not in code                  # ~25 unbounded statements, no timeout


def _min_ms() -> dict:
    block = OPS_JS.split("var MIN_MS = {")[1].split("};")[0]
    return {k: int(v) for k, v in re.findall(r"'(/api/command/[^']+)':\s*(\d+)", block)}


def test_every_read_is_no_faster_than_the_desk_minimum():
    mins = _min_ms()
    feeds = re.findall(r"url:\s*'([^']+)',\s*every:\s*(\d+)", JS)
    assert len(feeds) == 5
    for url, every in feeds:
        path = url.split("?")[0]
        assert int(every) >= mins.get(path, 15000), (url, every)
    assert dict((u.split("?")[0], int(e)) for u, e in feeds)["/api/command/coverage"] >= 120000
    assert "TIMEOUT_MS = 25000" in JS and "AbortController" in JS
    assert "if (e.inflight) { return e.inflight; }" in JS          # one in flight per URL
    assert "Math.pow(2, Math.min(e.fails, 5))" in JS               # backoff after a failure
    assert "if (S.signedOut) { return Promise.resolve(e.res); }" in JS   # no 401 loop while signed out


def test_nothing_is_invented():
    code = _code(JS)
    for word in ("Ariana", "ariana", "LISTENING", "Watching real events", "READINESS", "78%", "34%",
                 "'NOW'", "Recorded activity", "'Event '", "'Opportunity'"):
        assert word not in code, word
    assert "Ariana" not in HTML and "ariana" not in CSS
    assert "function finite" not in JS                              # Number(null) === 0 rendered nulls as $0
    assert "metric(" not in JS and "flat(" not in JS                # no regex guessing over flattened keys


def test_the_service_worker_is_the_shell_only():
    assert "startsWith('/api/')" not in SW and "/api/" not in _code(SW)
    assert "if (!SHELL_URLS.has(key)) { return; }" in SW
    assert "if (req.method !== 'GET') { return; }" in SW
    assert "k.indexOf(PREFIX) === 0 && k !== VERSION" in SW         # never another worker's caches
    assert "build.json" not in _code(SW)
    shell = json.loads("[" + SW.split("var SHELL = [")[1].split("];")[0].replace("'", '"') + "]")
    for p in shell:
        assert (COMMAND / p).exists(), p
    assert "register('sw-mobile.js', {scope: './mobile.html'" in JS
    assert "await navigator.serviceWorker" not in JS                # never holds the first read


def test_the_manifest_installs_the_mobile_page():
    assert MANIFEST["display"] == "standalone"
    assert MANIFEST["start_url"] == "./mobile.html" and MANIFEST["scope"] == "./mobile.html"
    sizes = {(i["sizes"], i["purpose"]) for i in MANIFEST["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")} <= sizes
    assert all(" " not in i["purpose"] for i in MANIFEST["icons"])  # purposes listed separately
    for i in MANIFEST["icons"]:
        assert (COMMAND / i["src"]).exists(), i["src"]


def test_hidden_always_hides_and_motion_is_optional():
    assert "[hidden]{display:none!important}" in CSS
    assert "prefers-reduced-motion:reduce" in CSS
    assert 'id="mc-install" class="mc-install" hidden' in HTML
    assert 'id="mc-sheet" class="mc-sheet" hidden' in HTML


def test_brand_icon_and_no_lone_letter_mark():
    assert not re.search(r">\s*B\s*<", HTML)
    assert 'class="mc-brandmark" src="brand/bettortoken-app-icon-180.png"' in HTML
    index = (COMMAND / "index.html").read_text()
    assert "BETTOR MOBILE PWA" not in index and "manifest.webmanifest" not in index


def test_the_sign_in_never_stacks_two_panels():
    unlock = (COMMAND / "unlock.js").read_text()
    body = unlock.split("function check() {")[1].split("\n  }\n")[0]
    assert "!document.getElementById('command-unlock')) panel(submit)" in body


def test_the_mobile_files_are_never_cached_stale():
    try:
        import tomllib
    except ImportError:                                            # pragma: no cover
        pytest.skip("tomllib needs Python 3.11+")
    heads = {h["for"]: h["values"] for h in tomllib.loads((ROOT / "netlify.toml").read_text())["headers"]}
    for p in ("/sw-mobile.js", "/command/sw-mobile.js", "/manifest.webmanifest", "/command/manifest.webmanifest"):
        assert heads[p]["Cache-Control"] == "no-cache", p


# ── the pure view models, run in node on production-shaped payloads ──
NOW = 1_760_000_000.0
EQUITY = {
    "paper": {"book": "PAPER", "status": "OK", "why": None, "realized_pnl_usd": 1234.56, "unrealized_pnl_usd": -0.4,
              "cash_usd": 950.5, "available_usd": 900.25, "reserved_usd": 50.25,
              "exposure": {"cost_basis_usd": 6761.0}, "open_positions": {"count": 20, "marked": 19, "unmarked": 1},
              "marks_as_of": {"newest_age_s": 12, "stale_mark_after_s": 300}, "lane": {"state": "RUNNING"}},
    "actual": {"label": "ACTUAL · LEGACY MIRROR", "scale": "1:1,000", "legacy_mirror": True,
               "venues": {"polymarket_us": {"status": "OK", "realized_pnl_usd": 777.77, "cash_usd": 888.88,
                                            "lane": {"state": "DISABLED"}}, "kalshi": {"status": "UNAVAILABLE"}}},
    "small_live_bettor": {"status": "SHADOW", "why": None},
}
FLOOR = {"read_at": NOW, "agents": [
    {"agent": "DEREK", "slug": "derek", "display_name": "Derek", "title": "Chief Investment Officer · alpha & entry decisions",
     "authority": {"level": "ENTRY_REQUEST_THROUGH_GATED_PATH"}, "workspace": "/derek", "state": "WORKING_ON",
     "state_detail": "deciding 3 valuations", "state_since": NOW - 60, "monitor": [{"label": "decisions 1h", "value": 12, "why": None}],
     "heartbeat": {"age_s": 4}},
    {"agent": "CHIEF_ALLOCATOR", "slug": "allocator", "display_name": "Chief Allocator", "title": "Capital allocation · shadow sleeve",
     "authority": {"level": "SHADOW_WEIGHTS_ONLY"}, "workspace": "/allocator", "state": "STALE", "state_detail": None,
     "state_since": None, "monitor": [{"label": "weights", "value": None, "why": "no intel run"}], "heartbeat": {"age_s": 9999}}],
    "edges": [{"from": "KAREN", "to": "DEREK", "kind": "CHALLENGE", "count": 2, "at": NOW - 30, "summary": "Karen challenge: stale book"}],
    "feed": [{"at": NOW - 10, "verdict": "ENTER", "refusal": None, "market": "aec-nfl-kc-buf-2026-10-04", "side": "YES",
              "fixture": "Chiefs at Bills", "p_blended": 0.61, "limit_price": 0.58, "book": "PAPER"}]}


def _node(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "const vm=require('vm'),fs=require('fs');const c={};c.window=c;c.globalThis=c;"
        "c.Date=class extends Date{constructor(...a){a.length?super(...a):super(%d)} static now(){return %d}};"
        "vm.runInNewContext(fs.readFileSync(%r,'utf8'),c);const M=c.BTMobile;"
        "process.stdout.write(JSON.stringify((function(){return %s})()))"
    ) % (int(NOW * 1000), int(NOW * 1000), str(COMMAND / "mobile-command.js"), expr)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_null_never_renders_as_zero():
    f = _node("[M.fmt.usd(null),M.fmt.usd(undefined),M.fmt.usd(''),M.fmt.usd(true),M.fmt.usd([]),M.fmt.int(null),"
              "M.fmt.pp(null),M.fmt.usd(-0.004),M.fmt.usd(1234.5),M.fmt.usd(-12.3)]")
    assert f[:7] == [None] * 7
    assert f[7] == "$0.00" and f[8] == "$1,234.50" and f[9] == "−$12.30"


def test_the_paper_tiles_read_paper_only():
    tiles = _node("M.model.kpis(%s, null, {derekWhy:'paper/derek not read', limit:40})" % json.dumps(EQUITY))
    by = {t["k"]: t for t in tiles}
    assert by["Realized P&L"]["value"] == "$1,234.56"
    assert by["Available"]["value"] == "$900.25"                    # available_usd, never cash_usd
    assert by["Deployed"]["value"] == "$6,761.00"
    blob = json.dumps(tiles)
    assert "777.77" not in blob and "888.88" not in blob and "950.5" not in blob   # the legacy mirror is never a PAPER tile
    assert all(t["book"].startswith("PAPER") for t in tiles)
    assert by["Decisions 24h"]["value"] is None and by["Decisions 24h"]["why"] == "paper/derek not read"
    gone = _node("M.model.kpis({paper:{status:'UNAVAILABLE',why:'ledger unreadable'},actual:%s}, null, {})" % json.dumps(EQUITY["actual"]))
    assert all(t["value"] is None for t in gone)
    assert "777.77" not in json.dumps(gone) and "ledger unreadable" in json.dumps(gone)
    unmarked = _node("M.model.kpis({paper:{status:'OK',unrealized_pnl_usd:0,open_positions:{count:3,marked:0}}}, null, {})")
    assert [t for t in unmarked if t["k"] == "Unrealized"][0]["value"] is None


def test_small_live_is_shown_as_served():
    assert _node("M.model.mode(%s)" % json.dumps(EQUITY))["value"] == "SHADOW"
    m = _node("M.model.mode({paper:{}})")
    assert m["value"] is None and m["why"]


def test_the_agents_are_the_floors_own_seats():
    a = _node("M.model.agents(%s)" % json.dumps(FLOOR))
    assert [x["name"] for x in a] == ["Derek", "Chief Allocator"]
    assert a[1]["state"] == "STALE" and a[1]["href"] == "/allocator"
    assert a[1]["portrait"] == "team-demo/assets/models/portraits/allocator.jpg"
    assert (COMMAND / a[1]["portrait"]).exists() and (COMMAND / a[0]["portrait"]).exists()
    assert a[1]["detail"] is None and a[1]["monitor"][0]["value"] is None
    assert _node("M.model.agents(null)") == []
    t = _node("M.model.tape(%s)" % json.dumps(FLOOR))
    assert [r["who"] for r in t] == ["DEREK · PAPER", "KAREN → DEREK"]
    assert _node("M.fmt.clock(%r)" % (NOW - 10)) == "08:53:10Z"


def test_the_funnel_never_sums_an_unmeasured_stage_as_zero():
    cov = {"tz": "America/New_York", "days": [{"day": "2026-10-04", "leagues": [
        {"league": "americanfootball_nfl", "league_name": "NFL", "provider_events": 14, "mapped_events": 12, "evaluated_events": 10,
         "entered_events": 2, "ordered_events": 2, "filled_events": 1},
        {"league": "americanfootball_ncaaf", "league_name": "NCAAF", "provider_events": 50, "mapped_events": None, "evaluated_events": None,
         "entered_events": None, "ordered_events": None, "filled_events": None,
         "unavailable": {"mapped_events": "collector did not request it"}}]}]}
    fu = _node("M.model.funnel(%s)" % json.dumps(cov))
    v = {s["k"]: s["value"] for s in fu["stages"]}
    assert v["provider_events"] == 64 and v["mapped_events"] == 12 and v["filled_events"] == 1
    assert fu["partial"] is True and "collector did not request it" in json.dumps(fu)
    assert [s for s in fu["stages"] if s.get("largestLoss")][0]["k"] == "mapped_events"
    assert _node("M.model.funnel(null,'coverage not read')")["stages"] == []


def test_the_release_shows_api_workers_and_alignment():
    rel = {"api": {"sha": "191b2992406af8350bc9f0276de5b2fa15caf80f", "short": "191b299"},
           "workers": {"sha": "0123456789abcdef", "short": "0123456"},
           "alignment": {"verdict": "MISALIGNED"}, "schema": {"max_version": 224}, "generated_at": NOW}
    rows = {r["k"]: r for r in _node("M.model.release(%s, {sha:'ab02b03a9c', deploy_id:'6ac2'})" % json.dumps(rel))}
    assert rows["API"]["value"] == "191b299" and rows["WORKERS"]["value"] == "0123456"
    assert rows["ALIGNMENT"]["value"] == "MISALIGNED" and rows["ALIGNMENT"]["tone"] == "bad"
    assert rows["FRONTEND"]["value"] == "ab02b03" and rows["NETLIFY DEPLOY"]["value"] == "6ac2"
    att = _node("M.model.attention({release:{ok:true,data:%s},equity:null,coverage:null,floor:null})" % json.dumps(rel))
    assert att["items"][0]["title"] == "API / WORKER SHA MISMATCH" and att["missing"] == ["equity", "coverage", "floor"]
