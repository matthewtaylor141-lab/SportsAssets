"""Mobile acceptance: 3D WebGL Floor (desks, avatars, touch look, desk tap)
and mobile Trader Mode, on iPhone portrait / landscape and iPad portrait /
landscape (Chromium device emulation: viewport, DPR, touch, iOS UA).

  python mobile_audit.py local <dist> <payload.json> <out>
  python mobile_audit.py prod  https://command.bettortoken.com <payload.json> <out>

local: a server reproducing the Netlify rewrites (/ -> command/index.html,
/floor -> command/floor.html, /trader -> command/trader.html, everything else
from dist/command). The Trader Mode read is answered with the AUTHENTICATED
production payload (signed p0 readback); every other /api read is 401.
prod: the deployed site itself; /api reads are intercepted the same way (no
owner session exists in this environment)."""
import copy, functools, glob, http.server, io, json, sys, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright

MODE, SRC, PAYLOAD, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
OUT.mkdir(parents=True, exist_ok=True)
RAW = json.loads(PAYLOAD.read_text())
checks, methods, trader_reads = [], [], []
EXE = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))[0]
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
IPAD = ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
import os
DEVICES = [
    ("iphone-portrait", 390, 844, 3, IPHONE), ("iphone-landscape", 844, 390, 3, IPHONE),
    ("ipad-portrait", 820, 1180, 2, IPAD), ("ipad-landscape", 1180, 820, 2, IPAD)]
if os.environ.get("ONLY"):
    DEVICES = [d for d in DEVICES if d[0] in os.environ["ONLY"].split(",")]


def check(name, cond, detail=None):
    checks.append({"name": name, "passed": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " :: " + str(detail)[:300]))


if MODE == "local":
    DIST = Path(SRC) / "command"
    REWRITE = {"/": "index.html", "/floor": "floor.html", "/trader": "trader.html"}

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def translate_path(self, path):
            p = path.split("?")[0].split("#")[0]
            if p in REWRITE:
                return str(DIST / REWRITE[p])
            if p.startswith("/command/"):
                p = p[len("/command"):]
            return str(DIST / p.lstrip("/"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    BASE = "http://127.0.0.1:%d" % srv.server_address[1]
else:
    BASE = SRC.rstrip("/")


def shifted():
    s = copy.deepcopy(RAW)
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


state = {"trader": "ok"}


def api(route):
    req = route.request
    methods.append(req.method)
    if req.url.split("?")[0].endswith("/api/command/paper/trader-mode"):
        trader_reads.append({"url": req.url, "method": req.method, "t": time.time(),
                             "same_origin": req.url.startswith(BASE + "/api/"),
                             "credentials_header": req.headers.get("accept")})
        if state["trader"] == "401":
            return route.fulfill(status=401, content_type="application/json", body='{"detail":"auth"}')
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(shifted()))
    # SIGNED IN: the other Command reads are not in the signed readback, so
    # they answer 503 (authenticated, feed not supplied by this harness) --
    # never 401, which would put the sign-in dialog over the page
    return route.fulfill(status=503, content_type="application/json",
                         body='{"detail":"not supplied by the acceptance harness"}')


def visible_unobscured(pg, sel):
    return pg.evaluate("""(sel)=>{const e=document.querySelector(sel);if(!e)return {ok:false,why:'missing'};
      const r=e.getBoundingClientRect();const cs=getComputedStyle(e);
      if(!r.width||!r.height||cs.visibility==='hidden'||cs.display==='none')return {ok:false,why:'hidden',r:[r.x,r.y,r.width,r.height]};
      const inside=r.left>=0&&r.top>=0&&r.right<=innerWidth+0.5&&r.bottom<=innerHeight+0.5;
      const top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
      return {ok:inside&&!!top&&(top===e||e.contains(top)),inside,top:top&&top.className,r:[r.x,r.y,r.width,r.height]}}""", sel)


ANCH_JS = """()=>{const s=window.__floor.scene;return [...document.querySelectorAll('.fl-agent')]
  .map(b=>b.dataset.slug).map(x=>{const a=s.anchorOf(x);return a?[a.x,a.y]:null}).filter(Boolean)}"""


def anchor_shift(a, b):
    n = min(len(a), len(b))
    return max((abs(a[i][0] - b[i][0]) + abs(a[i][1] - b[i][1])) for i in range(n)) if n else 0.0


def canvas_png(pg, sel):
    return pg.locator(sel).screenshot()


def diff_ratio(a, b):
    from PIL import Image, ImageChops
    ia, ib = Image.open(io.BytesIO(a)).convert("RGB"), Image.open(io.BytesIO(b)).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    d = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 24 else 0)
    return sum(d.histogram()[255:]) / float(ia.size[0] * ia.size[1])


def variance(png):
    from PIL import Image, ImageStat
    return ImageStat.Stat(Image.open(io.BytesIO(png)).convert("L")).stddev[0]


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=EXE, args=[
        "--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl",
        "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
    for name, w, h, dpr, ua in DEVICES:
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr,
                            is_mobile=True, has_touch=True, user_agent=ua)
        ctx.set_default_timeout(90000)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/api/**", api)
        state["trader"] = "ok"
        # ── ROOT COMMAND ─────────────────────────────────────────────
        pg.goto(BASE + "/", wait_until="load")
        pg.wait_for_timeout(2500)
        lv = visible_unobscured(pg, ".bt-trader-launch")
        check(name + ": root Command shows a visible, unobscured Trader Mode launcher", lv["ok"], lv)
        check(name + ": root Command launcher targets /trader",
              pg.evaluate("document.querySelector('.bt-trader-launch')?.getAttribute('href')") == "/trader")
        check(name + ": root Command no horizontal overflow",
              not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"))
        pg.screenshot(path=str(OUT / ("root-%s.png" % name)))
        # the Floor action the user actually has on this device
        btn = pg.evaluate("""()=>{const t=[...document.querySelectorAll('#hq-tabbar button[data-go=floor]')].find(b=>b.offsetParent);
            if(t)return '#hq-tabbar button[data-go=floor]';const n=[...document.querySelectorAll('#hq-nav button[data-go=floor]')].find(b=>b.offsetParent);
            return n?'#hq-nav button[data-go=floor]':null}""")
        check(name + ": a visible Floor action exists on root Command", btn is not None, btn)
        if btn:
            pg.locator(btn).first.tap()
            pg.wait_for_timeout(3000)
            url = pg.url
            if url.rstrip("/").endswith("/floor"):
                where = "DEDICATED_FLOOR"
            else:
                where = "HQ_INLINE_FLOOR"
            info = pg.evaluate("""()=>{const c=[...document.querySelectorAll('canvas')].map(x=>{const r=x.getBoundingClientRect();
                let gl=null;try{gl=!!(x.getContext('webgl2')||x.getContext('webgl'))}catch(e){}
                return {w:r.width,h:r.height,top:r.top,vis:r.width>150&&r.height>150&&r.bottom>0&&r.top<innerHeight,gl}});return c}""")
            check(name + ": Floor action shows a visible WebGL canvas (" + where + ")",
                  any(c["vis"] and c["gl"] for c in info), {"url": url, "canvases": info})
            pg.screenshot(path=str(OUT / ("floor-action-%s.png" % name)))
        # ── DEDICATED /floor ────────────────────────────────────────
        pg.goto(BASE + "/floor", wait_until="load")
        try:
            pg.wait_for_function("window.__floor && window.__floor.scene && window.__floor.scene.stats().avatars>0",
                                 timeout=30000)
        except Exception as e:
            pass
        pg.wait_for_timeout(2500)
        st = pg.evaluate("""()=>{const f=window.__floor||{};const s=f.scene;
            const c=document.querySelector('#fl-stage canvas');const r=c?c.getBoundingClientRect():null;
            const slugs=[...document.querySelectorAll('.fl-agent')].map(b=>b.dataset.slug);
            const anchors=s?slugs.map(x=>{const a=s.anchorOf?s.anchorOf(x):null;return a&&{slug:x,x:a.x,y:a.y,behind:a.behind}}).filter(Boolean):[];
            return {view:f.view,stats:s?s.stats():null,canvas:r?[r.x,r.y,r.width,r.height]:null,
              ih:innerHeight,iw:innerWidth,slugs,anchors,gl:c?!!(c.getContext('webgl2')||c.getContext('webgl')):false,
              fallbackLabel:document.body.classList.contains('view-2d')}}""")
        av = (st.get("stats") or {}).get("avatars") or 0
        check(name + ": /floor renders the 3D WebGL floor (not the 2D map)",
              st["view"] == "3d" and st["gl"] and not st["fallbackLabel"], st)
        cv = st.get("canvas")
        check(name + ": /floor canvas is on screen and large",
              cv and cv[2] > 300 and cv[3] > 250 and cv[1] < st["ih"], cv)
        check(name + ": /floor avatars rendered", av >= 1, st.get("stats"))
        on = [a for a in st["anchors"] if not a["behind"] and cv and cv[0] <= a["x"] + cv[0] <= cv[0] + cv[2]]
        check(name + ": /floor desks rendered (%d desk anchors)" % len(st["anchors"]),
              len(st["anchors"]) >= 7, st["anchors"][:3])
        png0 = canvas_png(pg, "#fl-stage canvas")
        check(name + ": /floor canvas has drawn content (not blank)", variance(png0) > 8, variance(png0))
        pg.screenshot(path=str(OUT / ("floor-%s.png" % name)))
        if os.environ.get("DBG"):
            print("DBG", pg.evaluate("""()=>{const r=document.querySelector('.fl-roster'),cs=getComputedStyle(r),q=r.getBoundingClientRect();return {cls:document.body.className,disp:cs.display,wrap:cs.flexWrap,rect:[q.top,q.height,q.width],ih:innerHeight,mq:matchMedia('(pointer:coarse) and (max-height:500px)').matches,vv:visualViewport&&[visualViewport.width,visualViewport.height,visualViewport.scale]}}"""))
        # touch drag look: compared with the same interval UNTOUCHED (the
        # avatars idle-animate, so some pixels change on their own)
        # wait for any intro camera flight to settle: anchors stable
        for _ in range(20):
            a1 = pg.evaluate(ANCH_JS); pg.wait_for_timeout(500); a2 = pg.evaluate(ANCH_JS)
            if anchor_shift(a1, a2) < 1.0:
                break
        anc0 = pg.evaluate(ANCH_JS)
        pg.wait_for_timeout(1260)
        anc_b = pg.evaluate(ANCH_JS)
        base_shift = anchor_shift(anc0, anc_b)
        png0 = canvas_png(pg, "#fl-stage canvas")
        cdp = ctx.new_cdp_session(pg)
        # start where the canvas is the element a finger would touch: visible
        # and not under the roster / launcher overlays
        pt = pg.evaluate("""()=>{const c=document.querySelector('#fl-stage canvas');const r=c.getBoundingClientRect();
          const top=Math.max(r.top,0),bot=Math.min(r.bottom,innerHeight);
          for(const fy of [0.55,0.45,0.65,0.35,0.75,0.25])for(const fx of [0.5,0.6,0.4]){
            const x=r.left+r.width*fx,y=top+(bot-top)*fy;if(document.elementFromPoint(x,y)===c)return [x,y]}return null}""")
        x0, y0 = pt if pt else (cv[0] + cv[2] * 0.5, cv[1] + cv[3] * 0.5)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
        for i in range(1, 13):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove",
                                                  "touchPoints": [{"x": x0 - i * 14, "y": y0 - i * 4}]})
            pg.wait_for_timeout(30)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        pg.wait_for_timeout(900)
        anc1 = pg.evaluate(ANCH_JS)
        shift = anchor_shift(anc_b, anc1)
        png1 = canvas_png(pg, "#fl-stage canvas")
        check(name + ": touch drag moves the 3D camera (desk anchors moved %.0f px vs %.1f px untouched)"
              % (shift, base_shift), shift > max(10.0, 5 * base_shift),
              {"drag_px": shift, "untouched_px": base_shift, "pixels_changed": diff_ratio(png0, png1)})
        pg.screenshot(path=str(OUT / ("floor-dragged-%s.png" % name)))
        # desk tap -> recorded desk state panel
        st2 = pg.evaluate("""()=>{const s=window.__floor.scene;const c=document.querySelector('#fl-stage canvas').getBoundingClientRect();
            return [...document.querySelectorAll('.fl-agent')].map(b=>b.dataset.slug).map(x=>{const a=s.anchorOf(x);
            return a&&!a.behind&&a.x>20&&a.x<c.width-20&&a.y>20&&a.y<c.height-20?{slug:x,x:c.left+a.x,y:c.top+a.y}:null}).filter(Boolean)}""")
        picked = None
        # anchorOf() is the desk's LABEL point (height 3.2, the top edge of
        # its hit volume): tap below it, on the desk / agent body
        tries = [(a, dy) for a in st2[:4] for dy in (40, 70, 100)]
        for a, dy in tries:
            pg.touchscreen.tap(a["x"], a["y"] + dy)
            pg.wait_for_timeout(1200)
            sel = pg.evaluate("window.__floor.selected")
            if sel:
                picked = {"tapped": a["slug"], "selected": sel,
                          "panel_visible": pg.evaluate("!document.querySelector('#fl-panel').hidden"),
                          "panel_text": pg.locator("#fl-panel").inner_text()[:160]}
                break
        check(name + ": tapping a desk selects it and opens its recorded desk panel",
              picked and picked["panel_visible"] and len(picked["panel_text"]) > 10, picked or st2[:3])
        pg.screenshot(path=str(OUT / ("floor-desk-%s.png" % name)))
        ov = pg.evaluate("""()=>{const l=document.querySelector('.bt-trader-launch');const R=e=>e.getBoundingClientRect();
            const vis=e=>{const c=getComputedStyle(e),r=R(e);return c.display!=='none'&&c.visibility!=='hidden'&&r.width>0&&r.height>0};
            const hit=[];for(const e of document.querySelectorAll('#fl-panel button,#fl-panel a,.fl-roster .fl-agent')){if(!vis(e)||!l||!vis(l))continue;
              const a=R(l),b=R(e);if(a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom)hit.push((e.textContent||'').trim().slice(0,30))}
            const hit2=[];for(const r of document.querySelectorAll('.fl-roster')){if(!vis(r))continue;const a=R(r);
              for(const e of document.querySelectorAll('#fl-panel button,#fl-panel a')){if(!vis(e))continue;const b=R(e);
              if(a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom)hit2.push('roster over '+(e.textContent||'').trim().slice(0,30))}}
            return hit.concat(hit2)}""")
        check(name + ": open desk panel's actions are not covered by the launcher or team dock", not ov, ov)
        if picked:
            pg.evaluate("document.querySelector('[data-close]')?.click()")
            pg.wait_for_timeout(600)
        lf = visible_unobscured(pg, ".bt-trader-launch")
        check(name + ": /floor shows a visible Trader Mode launcher", lf["ok"], lf)
        overlap = pg.evaluate("""()=>{const l=document.querySelector('.bt-trader-launch');if(!l)return ['no launcher'];
            const L=l.getBoundingClientRect();return [...document.querySelectorAll('#fl-view,#fl-reset,.fl-tools button,.fl-tools a,.fl-agent,#fl-stage canvas+*')]
            .filter(e=>e!==l&&!l.contains(e)&&e.offsetParent).filter(e=>{const r=e.getBoundingClientRect();
            return !(r.right<=L.left||r.left>=L.right||r.bottom<=L.top||r.top>=L.bottom)}).map(e=>e.id||e.textContent.trim().slice(0,20))}""")
        check(name + ": /floor launcher does not obscure floor controls", not overlap, overlap)
        check(name + ": /floor no horizontal overflow",
              not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"))
        # ── /trader via the launcher ────────────────────────────────
        n0 = len(trader_reads)
        pg.locator(".bt-trader-launch").first.tap()
        pg.wait_for_timeout(500)
        try:
            pg.wait_for_selector(".position-card", timeout=20000)
        except Exception:
            pass
        check(name + ": the launcher lands on /trader", pg.url.rstrip("/").endswith("/trader"), pg.url)
        cards = pg.locator(".position-card").count()
        check(name + ": /trader renders the authenticated readback (%d positions)" % cards,
              cards == len(RAW["positions"]), cards)
        check(name + ": /trader cockpit renders",
              len(pg.locator("#focus-market").inner_text()) > 20 if pg.locator("#focus-market").count() else False)
        pg.wait_for_timeout(5000)
        reads = trader_reads[n0:]
        check(name + ": /trader keeps polling (>= 3 GET reads in ~6 s)",
              len(reads) >= 3 and all(r["method"] == "GET" for r in reads), len(reads))
        check(name + ": /trader reads are same-origin /api/command/paper/trader-mode",
              reads and all(r["same_origin"] for r in reads), reads[:1])
        check(name + ": /trader no horizontal overflow",
              not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"))
        pg.screenshot(path=str(OUT / ("trader-%s.png" % name)))
        # a refused read: no demo/sample fallback
        state["trader"] = "401"
        pg.wait_for_timeout(4500)
        check(name + ": a refused read shows the session requirement, no demo fallback",
              "session required" in (pg.locator("#error").inner_text() if pg.locator("#error").count() else "")
              and not pg.evaluate("!!document.querySelector('#preview-notice:not([hidden])')"))
        state["trader"] = "ok"
        check(name + ": no uncaught JS errors on Command, Floor or Trader", not errs, errs[:3])
        ctx.close()
    b.close()

check("every network request was a GET", all(m == "GET" for m in methods), sorted(set(methods)))
(OUT / "mobile-checks.json").write_text(json.dumps({
    "mode": MODE, "base": BASE,
    "other_api_reads": "503 (signed-in shell; only the Trader Mode payload is from the signed readback)", "payload": "authenticated production Trader Mode payload (signed p0 readback)",
    "browser": "Chromium (Playwright) device emulation, SwiftShader WebGL",
    "webkit_tested": False, "physical_devices_tested": False,
    "passed": sum(c["passed"] for c in checks), "total": len(checks), "checks": checks}, indent=1))
print(json.dumps({"passed": sum(c["passed"] for c in checks), "total": len(checks)}))
