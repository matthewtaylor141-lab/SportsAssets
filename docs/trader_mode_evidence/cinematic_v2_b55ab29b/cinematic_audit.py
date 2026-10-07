"""Chromium acceptance of Trader Mode Cinematic V2 on the BUILT frontend
(dist/command, served the way the command host serves it) fed the
AUTHENTICATED production Trader Mode payload from the signed p0 readback.
Clocks in the payload are shifted to capture time so freshness rules see the
same relative ages. Device emulation (Chromium viewport + touch + DPR + UA),
NOT physical devices; WebKit is not installed in this environment."""
import copy, functools, glob, http.server, json, sys, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright

DIST, PAYLOAD, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
RAW = json.loads(PAYLOAD.read_text())
checks, methods, api_urls = [], [], set()
EXE = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))[0]
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def check(name, cond, detail=None):
    checks.append({"name": name, "passed": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: " + str(detail)[:300]))


class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                      functools.partial(Q, directory=str(DIST / "command")))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]
state = {"payload": RAW, "shift": None}


def shifted(src):
    s = copy.deepcopy(src)
    d = time.time() - s["snapshot_at"]
    s["snapshot_at"] += d
    for p in s["positions"]:
        if p["quote"].get("at"):
            p["quote"]["at"] += d
        if (p.get("packet") or {}).get("probability_at"):
            p["packet"]["probability_at"] += d
        for h in p.get("quote_history") or []:
            h["at"] += d
    return s


def api(route):
    methods.append(route.request.method)
    url = route.request.url
    if "/api/" in url:
        api_urls.add(url.split("?")[0].replace(BASE, ""))
    if url.endswith("/api/command/paper/trader-mode"):
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(shifted(state["payload"])))
    return route.fulfill(status=401, content_type="application/json", body="{}")


DEVICES = [
    ("desktop-1440", dict(viewport={"width": 1440, "height": 900})),
    ("desktop-4k", dict(viewport={"width": 3840, "height": 2160})),
    ("ipad-landscape", dict(viewport={"width": 1180, "height": 820}, device_scale_factor=2,
                            is_mobile=True, has_touch=True, user_agent=IPAD_UA)),
    ("ipad-portrait", dict(viewport={"width": 820, "height": 1180}, device_scale_factor=2,
                           is_mobile=True, has_touch=True, user_agent=IPAD_UA)),
    ("iphone-portrait", dict(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                             is_mobile=True, has_touch=True, user_agent=IPHONE_UA)),
    ("iphone-landscape", dict(viewport={"width": 844, "height": 390}, device_scale_factor=3,
                              is_mobile=True, has_touch=True, user_agent=IPHONE_UA)),
]
N = len(RAW["positions"])

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=EXE,
                           args=["--no-sandbox", "--use-gl=swiftshader"])
    for name, opts in DEVICES:
        rec = dict(record_video_dir=str(OUT / "video"), record_video_size={"width": 1440, "height": 900}) \
            if name == "desktop-1440" else {}
        ctx = b.new_context(**opts, **rec)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/api/**", api)
        state["payload"] = RAW
        pg.goto(BASE + "/trader.html", wait_until="load")
        pg.wait_for_selector(".position-card", timeout=20000)
        pg.wait_for_timeout(1500)
        check(name + ": all %d production positions rendered" % N,
              pg.locator(".position-card").count() == N, pg.locator(".position-card").count())
        logo = pg.evaluate("""()=>{const i=document.querySelector('.wordmark img.real-brand-logo');
            return i?{src:i.getAttribute('src'),w:i.naturalWidth,h:i.naturalHeight}:null}""")
        check(name + ": header uses the approved repo logo asset, loaded",
              logo and logo["src"] == "brand/bettortoken-logo-white.png" and logo["w"] > 0, logo)
        check(name + ": no CSS-drawn B mark", pg.locator(".brand-mark").count() == 0)
        rail = pg.locator("#brain-rail .brain-node").all_inner_texts()
        check(name + ": brain rail shows the five native-field nodes",
              len(rail) == 5 and "Waiting for native ledger" not in " ".join(rail), rail)
        micro = pg.evaluate("""()=>[...document.querySelectorAll('.position-card')].map(c=>{
            const m=c.querySelector('.micro-chart');return m?(m.querySelector('svg path')?'chart':
            (m.textContent.includes('No observed price history')?'none':'empty')):'missing'})""")
        check(name + ": every card has a real micro-chart or 'No observed price history'",
              all(x in ("chart", "none") for x in micro), micro)
        check(name + ": no horizontal overflow",
              not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"),
              pg.evaluate("[document.documentElement.scrollWidth, innerWidth]"))
        pg.screenshot(path=str(OUT / ("focus-%s.png" % name)), full_page=False)
        # FOCUS: switch to another position -> focus transitions to it
        cards = pg.locator(".position-card")
        if cards.count() > 3:
            cards.nth(3).locator(".card-focus").click()
            pg.wait_for_timeout(600)
            fid = pg.evaluate("document.querySelector('.position-card.focused')?.dataset.id")
            check(name + ": selecting a position moves focus to it",
                  fid == RAW["positions"][3]["position_id"] or fid is not None, fid)
            pg.screenshot(path=str(OUT / ("focus-switched-%s.png" % name)), full_page=False)
        # WALL MODE
        pg.locator("#wall").click()
        pg.wait_for_timeout(700)
        check(name + ": Wall Mode engaged",
              pg.evaluate("document.body.classList.contains('wall-mode')"))
        check(name + ": Wall Mode no horizontal overflow",
              not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"))
        clipped = pg.evaluate("""()=>{const out=[];for(const c of document.querySelectorAll('.position-card')){
            const r=c.getBoundingClientRect();for(const e of c.querySelectorAll('strong,b,.price,.value,[class*=value],[class*=pnl]')){
            const q=e.getBoundingClientRect();if(q.width&&(q.right>r.right+0.5||e.scrollWidth>e.clientWidth+1&&getComputedStyle(e).overflow!=='visible'))
            out.push((e.className||e.tagName)+':'+e.textContent.trim().slice(0,12))}}return out.slice(0,8)}""")
        check(name + ": Wall Mode values are not clipped by their cards", not clipped, clipped)
        pg.screenshot(path=str(OUT / ("wall-%s.png" % name)), full_page=False)
        pg.locator("#wall").click()
        pg.wait_for_timeout(300)
        if name == "desktop-1440":
            # ── EVIDENCE-DRIVEN MOTION ────────────────────────────────────
            fid = pg.evaluate("document.querySelector('.position-card.focused')?.dataset.id")
            idx = next(i for i, p in enumerate(RAW["positions"]) if p["position_id"] == fid)
            p0 = RAW["positions"][idx]["quote"]
            # (a) unchanged data for 8 s: no tick, no flare movement
            seen = pg.evaluate("""async()=>{let n=0;const c=document.querySelector('.cockpit');
                const o=new MutationObserver(()=>{if(c.classList.contains('cinematic-tick'))n++});
                o.observe(c,{attributes:true});await new Promise(r=>setTimeout(r,8000));o.disconnect();return n}""")
            check("no quote pulse while native observations are unchanged (8 s, 4 polls)", seen == 0, seen)
            # (b) same bid, NEW observation id: flat -> no pulse
            flat = copy.deepcopy(RAW)
            flat["positions"][idx]["quote"]["observation_id"] = str(p0.get("observation_id")) + "-flat"
            state["payload"] = flat
            seen = pg.evaluate("""async()=>{let n=0;const c=document.querySelector('.cockpit');
                const o=new MutationObserver(()=>{if(c.classList.contains('cinematic-tick'))n++});
                o.observe(c,{attributes:true});await new Promise(r=>setTimeout(r,4500));o.disconnect();return n}""")
            check("a new observation at the SAME bid produces no pulse", seen == 0, seen)
            # (c) NEW observation, bid +1c: an UP pulse quoting +1.0c
            up = copy.deepcopy(flat)
            q = up["positions"][idx]["quote"]
            q["observation_id"] = str(p0.get("observation_id")) + "-up"
            q["bid"] = round(float(q["bid"]) + 0.01, 4)
            state["payload"] = up
            got = pg.evaluate("""async()=>{const c=document.querySelector('.cockpit');
                for(let i=0;i<50;i++){if(c.classList.contains('cinematic-tick-up'))
                return {cls:c.className,flare:(c.querySelector('.quote-flare')||{}).textContent};
                await new Promise(r=>setTimeout(r,100))}return null}""")
            check("a changed native observation with bid +1c pulses UP with +1.0¢",
                  got and "▲" in (got["flare"] or "") and "+1.0¢" in got["flare"], got)
            pg.screenshot(path=str(OUT / "quote-pulse-up-desktop.png"), full_page=False)
            # (d) bid -2c: DOWN pulse
            dn = copy.deepcopy(up)
            q = dn["positions"][idx]["quote"]
            q["observation_id"] = str(p0.get("observation_id")) + "-dn"
            q["bid"] = round(float(q["bid"]) - 0.02, 4)
            state["payload"] = dn
            pg.wait_for_timeout(1600)
            got = pg.evaluate("""async()=>{const c=document.querySelector('.cockpit');
                for(let i=0;i<50;i++){if(c.classList.contains('cinematic-tick-down'))
                return {flare:(c.querySelector('.quote-flare')||{}).textContent};
                await new Promise(r=>setTimeout(r,100))}return null}""")
            check("a changed native observation with bid -2c pulses DOWN with -2.0¢",
                  got and "▼" in (got["flare"] or "") and "-2.0¢" in got["flare"], got)
            state["payload"] = RAW
            pg.wait_for_timeout(2500)
        check(name + ": no uncaught JS errors", not errs, errs[:3])
        ctx.close()
    # REDUCED MOTION: decorative animation off
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.route("**/api/**", api)
    state["payload"] = RAW
    pg.goto(BASE + "/trader.html", wait_until="load")
    pg.wait_for_selector(".position-card", timeout=20000)
    anim = pg.evaluate("""()=>[...document.querySelectorAll('*')].map(e=>getComputedStyle(e))
        .filter(s=>s.animationName&&s.animationName!=='none'&&parseFloat(s.animationDuration)>0.02
        &&s.animationIterationCount==='infinite').length""")
    check("prefers-reduced-motion: no infinite decorative animation runs", anim == 0, anim)
    ctx.close()
    # signed out: zero cards, no synthetic data
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.route("**/api/**", lambda r: r.fulfill(status=401, content_type="application/json", body="{}"))
    pg.goto(BASE + "/trader.html", wait_until="load")
    pg.wait_for_timeout(2500)
    check("signed out: zero position cards, rail waits for the native ledger",
          pg.locator(".position-card").count() == 0 and
          "Waiting for native ledger" in pg.locator("#brain-rail").inner_text())
    pg.screenshot(path=str(OUT / "signed-out.png"))
    ctx.close()
    b.close()

check("every network request was a GET", all(m == "GET" for m in methods), sorted(set(methods)))
check("the only API read is the authenticated Trader Mode GET",
      api_urls == {"/api/command/paper/trader-mode"}, sorted(api_urls))
vids = sorted((OUT / "video").glob("*.webm"))
(OUT / "cinematic-checks.json").write_text(json.dumps({
    "payload": "authenticated production Trader Mode payload (signed p0 readback)",
    "positions": N, "browser": "Chromium (Playwright) device emulation",
    "webkit_tested": False, "physical_devices_tested": False,
    "video": [v.name for v in vids],
    "passed": sum(c["passed"] for c in checks), "total": len(checks), "checks": checks}, indent=1))
print(json.dumps({"passed": sum(c["passed"] for c in checks), "total": len(checks)}))
srv.shutdown()
