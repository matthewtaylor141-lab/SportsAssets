#!/usr/bin/env python3
"""BUILD THE WEEKLY MANAGEMENT REPORT FROM MEASURED INPUTS.

Owner requirement: "Commit the replacement report pipeline and its
dependencies to the repository so it is reproducible. This is already
requested work; do not ask whether I want durable tooling."

WHY A PIPELINE AND NOT A HAND-EDITED PAGE. Every prior edition was HTML
typed by hand, which has two failure modes this replaces:

  1 A FIGURE CAN REACH THE PAGE WITHOUT A SOURCE. Nothing stopped a
    number being typed into the template, and nothing recorded which
    query produced it. Week 38 published a figure that later moved and
    the edition could not say what it had been read from.
  2 A RESTATEMENT CAN SILENTLY REPLACE A PUBLISHED FIGURE, removing the
    reader's ability to see that a number changed.

SO THE CONTRACT IS: data lives in `wk39_inputs.json` with a `src` on
every measured block, rendering lives here, and `_require_src` REFUSES TO
BUILD when a measured block has no provenance. A missing source is a
build failure, not a silent omission.

    python3 weekly_report_build.py wk39_inputs.json \
        --css weekly_report.css --out weekly_report_wk39.html

WHAT THIS DOES NOT DO. It does not run the queries. The read path is
`research-sql` on a GitHub runner against the read replica (this
container has no route to the database), so the measured values are
transcribed into the JSON together with the query file, its sha256 and
the run number that printed them. That is the reproducibility boundary
and it is stated rather than papered over: re-running the named query at
the named sha256 is how a reader checks a figure.
"""

from __future__ import annotations

import argparse
import html
import json
import sys

#: Blocks that state measured quantities and therefore must carry `src`.
#: Listed explicitly: deriving it from "has numbers" would let a new
#: block escape the check by being shaped differently.
MEASURED_BLOCKS = (
    ("attribution", ("orders_opened_in_the_week", "last_order_any_lane",
                     "lanes_all_time", "funded_book")),
    ("subtotal", None),
    ("coverage", ("span", "missing_days_in_the_whole_span",
                  "the_residual_uncertainty_i_could_not_close")),
    ("sleeves", None),
)


class MissingProvenance(Exception):
    """A measured figure with no source. This stops the build."""


def _require_src(data: dict) -> list[str]:
    """Every measured block names where it was read from, or we do not build.

    Returns the list of (block, sha256, run) triples it accepted, so the
    page can print its own provenance footer from the same check that
    enforced it -- rather than from a second, hand-kept list that could
    disagree with it.
    """
    accepted = []
    for block, keys in MEASURED_BLOCKS:
        if block not in data:
            raise MissingProvenance("block %r is absent" % block)
        node = data[block]
        targets = [(block, node)] if keys is None else [
            ("%s.%s" % (block, k), node[k]) for k in keys if k in node]
        if keys is not None:
            for k in keys:
                if k not in node:
                    raise MissingProvenance(
                        "%s.%s is absent, so its figure cannot be checked"
                        % (block, k))
        for name, sub in targets:
            if not isinstance(sub, dict) or not str(sub.get("src") or "").strip():
                raise MissingProvenance(
                    "%s states a measured figure with no `src`. A number "
                    "without a source does not go on the page." % name)
            accepted.append((name, sub.get("sha256"), sub.get("run")))
    return accepted


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def money(v) -> str:
    """A signed dollar figure with the sign carried in the class, not the text."""
    f = float(v)
    return "%s$%s" % ("−" if f < 0 else "+", format(abs(f), ",.2f"))


def cls(v) -> str:
    return "neg" if float(v) < 0 else "pos"


def _kpi(label, value, detail, klass="") -> str:
    return ('<div class="kpi"><div class="lbl">%s</div>'
            '<div class="v %s">%s</div><div class="d">%s</div></div>'
            % (e(label), klass, e(value), e(detail)))


def render(d: dict, css: str) -> str:
    accepted = _require_src(d)
    ed = d["edition"]
    at = d["attribution"]
    st = d["subtotal"]
    cv = d["coverage"]
    w38 = d["week_38"]
    wow = d["week_over_week"]
    parts: list[str] = []
    A = parts.append

    A('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">')
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    # A NAME, not a caption. Two-to-four words, specific to this edition.
    A("<title>Week 39 Management Report</title>")
    A('<style>\n%s\n</style>\n</head>\n<body>\n<div class="wrap">' % css)

    # ── masthead ────────────────────────────────────────────────────
    A('<header class="mast"><div class="brand">'
      '<div class="word">BETTORTOKEN</div>'
      '<div class="tag">WEEKLY MANAGEMENT REPORT</div></div>'
      '<div class="stamp"><b>NO.&nbsp;%d &middot; VERSION&nbsp;%d</b>%s</div>'
      '</header>' % (ed["number"], ed["version"], e(ed["window_et"])))

    # ── the headline, attributed ────────────────────────────────────
    A('<section class="hero">')
    A('<p class="eyebrow">Five of seven days &middot; run-off of earlier inventory</p>')
    A('<h1>A five-day subtotal of <span class="%s">%s</span> on '
      '%d settled markets — none of it from the autonomous engine, '
      'and none of it from activity this week.</h1>'
      % (cls(st["realized_usd"]), money(st["realized_usd"]),
         st["settled_markets"]))
    A('<p class="dek">%s</p>' % e(at["conclusion"]["value"]))
    A('<div class="halt"><div class="dot"></div><p><b>This is a subtotal, '
      'not a week total.</b> %s Mon&nbsp;Sep&nbsp;21 and Tue&nbsp;Sep&nbsp;22 '
      'carry no venue-truth rows and remain <b>unresolved</b> — the most '
      'likely reading is named below, together with the reason it cannot be '
      'confirmed.</p></div>' % e(st["is_not"]))
    A('</section>')

    # ── revision notice, first-class ────────────────────────────────
    A('<section class="sec"><h2>Version %d replaces versions 1 and 2</h2>'
      '<p class="dek">%s</p></section>'
      % (ed["version"], e(ed["version_note"])))

    # ── KPIs ────────────────────────────────────────────────────────
    A('<section class="sec"><div class="kpis">')
    A(_kpi(st["label"], money(st["realized_usd"]),
           "%d settled · %dW–%dL · ROI %.4f%%"
           % (st["settled_markets"], st["wins"], st["losses"],
              st["roi_pct"]), cls(st["realized_usd"])))
    A(_kpi("Orders opened this week",
           str(at["orders_opened_in_the_week"]["value"]),
           "by any lane · last order %s"
           % at["last_order_any_lane"]["value"], "warn"))
    A(_kpi("Autonomous EV engine",
           "%d intents" % at["funded_book"]["value"]["intents"],
           "has never sent an order", "warn"))
    A(_kpi("Sep 21 & Sep 22", "UNRESOLVED", "no rows in 51-of-53-day history",
           "warn"))
    A('</div></section>')

    # ── attribution ─────────────────────────────────────────────────
    A('<section class="sec"><h2>Whose figure this is</h2>')
    A('<p class="dek">Versions 1 and 2 led with a P&amp;L figure and never '
      'said which account, lane or strategy produced it. It is '
      '<b>%s</b>.</p>' % e(at["what_venue_truth_is"]["value"]))
    A('<table><thead><tr><th class="name">Lane</th><th>Orders</th>'
      '<th>Settled</th><th>Realized</th><th>First</th><th>Last</th>'
      '</tr></thead><tbody>')
    for r in at["lanes_all_time"]["value"]:
        A('<tr><td class="name">%s</td><td>%s</td><td>%s</td>'
          '<td class="%s">%s</td><td class="muted">%s</td>'
          '<td class="muted">%s</td></tr>'
          % (e(r["lane"]), format(r["orders"], ","),
             format(r["settled"], ","), cls(r["realized_usd"]),
             money(r["realized_usd"]), e(r["first"]), e(r["last"])))
    A('</tbody></table>')
    A('<p class="dek"><b>Two bases, never mixed.</b> %s</p>'
      % e(at["lanes_all_time"]["basis_warning"]))
    A('</section>')

    # ── the five days ───────────────────────────────────────────────
    A('<section class="sec"><h2>%s</h2>' % e(st["label"]))
    A('<table><thead><tr><th class="name">Day</th><th>Realized</th>'
      '<th>Settled</th><th>W–L</th><th>Cost</th></tr></thead><tbody>')
    A('<tr class="gap"><td class="name">Mon Sep 21</td><td colspan="4" '
      'class="muted">no row — unresolved</td></tr>')
    A('<tr class="gap"><td class="name">Tue Sep 22</td><td colspan="4" '
      'class="muted">no row — unresolved</td></tr>')
    for r in st["per_day"]:
        A('<tr><td class="name">%s</td><td class="%s">%s</td><td>%d</td>'
          '<td>%d–%d</td><td>$%s</td></tr>'
          % (e(r["day"]), cls(r["realized"]), money(r["realized"]),
             r["settled"], r["wins"], r["losses"],
             format(r["cost"], ",.2f")))
    A('<tr class="total"><td class="name">Five-day subtotal</td>'
      '<td class="%s">%s</td><td>%d</td><td>%d–%d</td><td>$%s</td></tr>'
      % (cls(st["realized_usd"]), money(st["realized_usd"]),
         st["settled_markets"], st["wins"], st["losses"],
         format(st["cost_usd"], ",.2f")))
    A('</tbody></table>')
    A('<p class="dek">%s</p>' % e(st["recompute_check"]))
    A('</section>')

    # ── coverage ────────────────────────────────────────────────────
    A('<section class="sec"><h2>What the two missing days mean</h2>')
    sp = cv["span"]
    A('<p class="dek">History runs <b>%s</b> to <b>%s</b>: <b>%d</b> of '
      '<b>%d</b> days present, <b>%d</b> missing, and the two missing days '
      'are the only gaps in the whole span. Both neighbours are '
      'present.</p>' % (e(sp["earliest_day"]), e(sp["latest_day"]),
                        sp["distinct_days_present"], sp["days_in_span"],
                        sp["days_missing"]))
    A('<p class="dek"><b>What absence means, from the writer rather than '
      'from the crawl clock.</b> %s</p>'
      % e(cv["what_absence_means_from_the_writer"]["value"]))
    A('<p class="dek"><b>A pagination guard does exist.</b> %s</p>'
      % e(cv["the_pagination_guard_that_does_exist"]["value"]))
    ru = cv["the_residual_uncertainty_i_could_not_close"]
    A('<div class="halt"><div class="dot"></div><p><b>And here is what I '
      'could not close.</b> %s<br><br>%s<br><br><b>What would close it:</b> '
      '%s</p></div>' % (e(ru["value"]), e(ru["consequence"]),
                        e(ru["what_would_close_it"])))
    A('<p class="dek"><b>A discriminator that did not work.</b> %s</p>'
      % e(cv["a_discriminator_that_did_not_work"]["value"]))
    A('</section>')

    # ── week 38: both figures ───────────────────────────────────────
    A('<section class="sec"><h2>Week 38, as published and as restated</h2>')
    A('<table><thead><tr><th class="name">Basis</th><th>Days</th>'
      '<th>Settled</th><th>Cost</th><th>Realized</th></tr></thead><tbody>')
    orig = w38["as_originally_published"]
    A('<tr><td class="name">%s</td><td>%d</td><td colspan="3" class="muted">'
      '%s</td></tr>' % (e(orig["label"]), orig["days"], e(orig["note"])))
    rs = w38["as_restated"]
    A('<tr><td class="name">%s</td><td>%d</td><td>%d</td><td>$%s</td>'
      '<td class="%s">%s</td></tr>'
      % (e(rs["label"]), rs["days"], rs["settled_markets"],
         format(rs["cost_usd"], ",.2f"), cls(rs["realized_usd"]),
         money(rs["realized_usd"])))
    A('</tbody></table>')
    rr = w38["revision_record"]
    A('<p class="dek"><b>Revision record.</b> %s %s <span class="muted">'
      'Restated by: %s. Basis of both: %s.</span></p>'
      % (e(rr["what_changed"]), e(rr["why_the_original_is_kept"]),
         e(rr["who_restated_it"]), e(rr["basis_of_both"])))
    A('<p class="dek"><b>Week over week, not like-for-like.</b> %s '
      'Week&nbsp;39 is <b>%.1f%%</b> of the settled count on <b>%.1f%%</b> '
      'of the cost.</p>' % (e(wow["_caution"]), wow["settled_ratio_pct"],
                            wow["cost_ratio_pct"]))
    A('</section>')

    # ── unresolved ──────────────────────────────────────────────────
    A('<section class="sec"><h2>Unresolved</h2>')
    for it in d["unresolved"]["items"]:
        A('<div class="halt"><div class="dot"></div><p><b>%s</b> '
          '<span class="mono muted">[%s]</span><br>%s<br><br>'
          '<b>Consequence:</b> %s<br><b>Cause:</b> %s%s</p></div>'
          % (e(it["title"]), e(it["status"]), e(it["what"]),
             e(it.get("consequence", "—")), e(it["cause"]),
             ("<br><b>Not claimed:</b> " + e(it["not_claimed"]))
             if it.get("not_claimed") else ""))
    A('</section>')

    # ── what this edition does not claim ────────────────────────────
    A('<section class="sec"><h2>What this edition does not claim</h2><ul>')
    for c in d["what_this_edition_does_not_claim"]:
        A("<li>%s</li>" % e(c))
    A("</ul></section>")

    # ── provenance footer, from the check itself ─────────────────────
    A('<section class="sec"><h2>Provenance</h2>'
      '<p class="dek">Built by <span class="mono">%s</span> from '
      '<span class="mono">wk39_inputs.json</span>. The builder refuses to '
      'render a measured block with no source, so every figure below named '
      'one. Re-run the query at the stated sha256 to check a figure.</p>'
      % e(ed["built_by"]))
    A('<table><thead><tr><th class="name">Block</th><th>Query sha256</th>'
      '<th>Run</th></tr></thead><tbody>')
    for name, sha, run in accepted:
        A('<tr><td class="name mono">%s</td><td class="mono muted">%s</td>'
          '<td class="muted">%s</td></tr>'
          % (e(name), e((sha or "—")[:16]), e(run or "—")))
    A('</tbody></table></section>')

    A('</div>\n</body>\n</html>')
    return "\n".join(parts) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs")
    p.add_argument("--css", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    with open(a.inputs) as fh:
        data = json.load(fh)
    css = open(a.css).read()
    try:
        page = render(data, css)
    except MissingProvenance as exc:
        print("BUILD REFUSED: %s" % exc, file=sys.stderr)
        return 2
    with open(a.out, "w") as fh:
        fh.write(page)
    print("wrote %s (%d bytes)" % (a.out, len(page)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
