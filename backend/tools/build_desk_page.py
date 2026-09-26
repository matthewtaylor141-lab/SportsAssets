"""Regenerate `api/desk_page.py` from the static bundle. One source, two hosts.

The desk is authored once under `frontend/public/command/` -- desk.html,
desk.css, desk.js -- and served two ways: as static files on the Command
site, and inlined into a single self-contained page on the API's own origin,
which is the origin this repository can deploy and which is same-origin with
`/api/command/*` without needing a proxy rule.

Run after editing any of the three:  python3 -m tools.build_desk_page
A test compares the two copies for drift, so a forgotten run is caught.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
BUNDLE = os.path.join(ROOT, "frontend", "public", "command")
TARGET = os.path.join(ROOT, "backend", "sportsassets", "api", "desk_page.py")

#: THE SAME GUARD core.js ENFORCES, restated because BTCore is not on the API
#: origin. A path outside /api/command/, or carrying `?`, `#`, `..` or a
#: backslash, is refused -- so the page cannot be re-pointed at another host
#: and cannot carry a token in a URL.
SHIM = r"""
window.BTCore = {
  esc: function (v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;"}[c];
    });
  },
  usd: function (v, d) {
    d = (d === undefined) ? 0 : d;
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD",
      minimumFractionDigits: d, maximumFractionDigits: d}).format(v);
  },
  endpoint: function (path) {
    if (typeof path !== "string"
        || !/^\/api\/command(?:\/|$)/.test(path)
        || path.indexOf("..") >= 0 || path.indexOf("\\") >= 0
        || /[?#]/.test(path)) {
      throw new Error("Only configured same-origin /api/command/ read " +
                      "endpoints are allowed.");
    }
    return path;
  }
};
"""

TAIL = ('"""\n\n#: The markers a drift test holds both copies to.\n'
        'CONTRACT_MARKERS = (\n'
        '    "Bettor EV Engine", "Controls", "Opportunities", "Orders", '
        '"Positions",\n'
        '    "Management", "Performance", "Activation", "NO_TRADE",\n'
        '    "/api/command/bettor/desk", "audit",\n)\n')


def build() -> str:
    html = open(os.path.join(BUNDLE, "desk.html")).read()
    css = open(os.path.join(BUNDLE, "desk.css")).read()
    js = open(os.path.join(BUNDLE, "desk.js")).read()
    html = html.replace('  <link rel="stylesheet" href="command.css">\n', "")
    html = html.replace('<link rel="stylesheet" href="desk.css">',
                        "<style>__CSS__</style>")
    html = re.sub(r'  <script src="[^"]+"></script>\n', "", html)
    html = html.replace("</body>", "  <script>__JS__</script>\n</body>")
    html = html.replace('<a href="index.html">Command</a>',
                        '<a href="/command/">Command</a>')
    return html.replace("__CSS__", css).replace("__JS__", SHIM + js)


def main() -> int:
    page = build()
    if "script src=" in page or "stylesheet" in page:
        print("the inlined page still references an external asset; the API "
              "origin serves none", file=sys.stderr)
        return 1
    old = open(TARGET).read() if os.path.exists(TARGET) else ""
    head = old.split('DESK_PAGE_HTML = r"""')[0] if old else ""
    if not head:
        print("target module missing its header; refusing to write",
              file=sys.stderr)
        return 1
    open(TARGET, "w").write(head + 'DESK_PAGE_HTML = r"""' + page + TAIL)
    print("wrote %s (%d bytes of page)" % (TARGET, len(page)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
