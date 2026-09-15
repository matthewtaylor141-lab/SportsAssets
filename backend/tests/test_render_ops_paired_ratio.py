"""The target line (FILL lane 0b item 6, 2026-09-08; owner decision D3):
`paired-day` and `paired-ratio` pair PER EPISODE (a market pairs on any
settled episode, our stake and settled figure summed over the closed books
with settled_pnl, `episodes` / `settled_eps` beside them), drop the
zero-stake rows from the totals' sums and ratios (book 253 on 2026-09-08:
our_staked 0.00, settled +735.41 credited to the set; hourly_1737 1425),
and the hourly's one line leads with roi_ratio_net (his NET ROI, -0.1562
on 2026-09-08 against the gross -0.0622; 1.81x against 4.54x, hourly_1737
1487) with the gross beside it. The pattern arms `paired-day=<ISO>` and
`paired-ratio=<ISO>` are the bare presets' own SQL with
`b.opened_at >= that clock` on every books window -- the set restricted
to books opened after a deploy (section 3's read); the bare labels and the
hourly stay the whole set. The pins: the per-episode aggregates on both
presets, the `paired` gate, the staked FILTERs and the zero-stake columns
in both totals, the new line and its arguments, the pattern arms' regex
and their SQL derived from the bare text (2 windows on paired-day, 4 on
paired-ratio), the help line's patterns in case order, the hourly carrying
the bare SQL and no `$PS`; the paired-day CTE the exits-paired pins lift
(the `his` collapse) untouched.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import test_render_ops_hourly as hourly

YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
SETTLED = "b.state = 'closed' AND b.settled_pnl IS NOT NULL"
WINDOW = ("FROM mirror_books b WHERE b.whale = 'rn1' AND (b.opened_at >= now() - interval '24 hours'"
          " OR b.closed_at >= now() - interval '24 hours')")
SINCE = " AND b.opened_at >= '$PS'::timestamptz"
STAKED = "FILTER (WHERE COALESCE(our_staked, 0) > 0)"
DROPPED = "COALESCE(our_staked, 0) <= 0"
LINE = ("format('PAIRED %s mkts (%s staked, %s zero-stake dropped) his_roi_net %s our_roi %s roi_ratio_net %sx"
        " gross his_roi %s roi_ratio %sx same %s/%s opposite %s share_frac_med %s%% stake_frac_net_med %s%%"
        " cents_over %sc lat_med %ss', t.markets, t.staked_mkts, t.zero_stake_mkts, t.his_roi_net, t.our_roi,"
        " t.roi_ratio_net, t.his_roi, t.roi_ratio, t.same_sign, t.markets, COALESCE(t.opposite, '-'),"
        " t.share_frac_med_pct, t.stake_frac_net_med_pct, t.cents_over_med, t.lat_med_s)")


def _preset(text: str, name: str) -> tuple[str, int]:
    m = re.search(r'^ {16}' + re.escape(name) + r'\) SQL="(.*?)"; TO=(\d+)', text, re.M | re.S)
    assert m, name
    return m.group(1), int(m.group(2))


def _pattern_arm(text: str, name: str) -> tuple[str, str, int]:
    """The pattern arm's guard line, its SQL and its timeout."""
    head = text.index("                %s=*) if [[" % name)
    body = text[head:]
    guard = body[:body.index("\n")]
    sql = body[body.index('SQL="') + 5:]
    sql, rest = sql.split('"; TO=', 1)
    return guard, sql, int(rest.split()[0])


def test_paired_day_and_ratio_pair_on_any_settled_episode_and_print_the_episode_counts():
    text = YML.read_text()
    for name, n_bk in (("paired-day", 2), ("paired-ratio", 2)):
        sql, _ = _preset(text, name)
        assert sql.count(f"round(sum(b.peak_exposure_usd) FILTER (WHERE {SETTLED})::numeric, 2) AS staked,"
                         f" round(sum(b.settled_pnl) FILTER (WHERE {SETTLED})::numeric, 2) AS settled,"
                         f" bool_or({SETTLED}) AS paired, count(*) AS episodes, count(*) FILTER (WHERE {SETTLED})"
                         " AS settled_eps, min(b.opened_at) AS opened,") == n_bk, name
        assert sql.count("WHERE bk.paired)") == n_bk and "bk.closed" not in sql and "bool_and(b.state" not in sql
        assert sql.count("COALESCE(bk.frozen_reason, bk.last_reason) AS last_reason, bk.episodes, bk.settled_eps"
                         " FROM bk JOIN his") == n_bk
    pr, _ = _preset(text, "paired-ratio")
    # our peak shares on the settled episodes only (the fold of HIGH-1 kept: the leg's price per book)
    assert pr.count(f"CASE WHEN bool_and(b.avg_cost IS NOT NULL OR COALESCE(b.peak_exposure_usd, 0) = 0) FILTER (WHERE {SETTLED})"
                    " THEN max(b.peak_exposure_usd / NULLIF(CASE WHEN b.intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - b.avg_cost"
                    f" ELSE b.avg_cost END, 0)) FILTER (WHERE {SETTLED}) END AS peak_sh") == 2


def test_the_totals_drop_the_zero_stake_rows_from_the_sums_and_ratios_and_count_them_beside():
    text = YML.read_text()
    for name in ("paired-day", "paired-ratio"):
        sql, _ = _preset(text, name)
        totals = sql[sql.rindex("SELECT count(*) AS markets, "):]
        assert totals.startswith(f"SELECT count(*) AS markets, count(*) FILTER (WHERE COALESCE(our_staked, 0) > 0) AS staked_mkts,"
                                 f" count(*) FILTER (WHERE {DROPPED}) AS zero_stake_mkts,"
                                 f" round(sum(our_settled) FILTER (WHERE {DROPPED})::numeric, 2) AS zero_stake_settled, ")
        # every dollar sum in the totals runs over the staked rows; none is bare
        # paired-day sums his_usd too; paired-ratio prints his_cost_net in its place
        for col in (("his_usd",) if name == "paired-day" else ("his_cost_net",)) + ("his_cost", "his_pnl", "our_staked", "our_settled"):
            assert f"sum({col}) {STAKED}" in totals, (name, col)
            # a bare sum is allowed only under the zero-stake FILTER (the dropped rows' settled dollars, printed beside)
            assert re.search(r"sum\(%s\)(?! FILTER \(WHERE COALESCE\(our_staked, 0\) (> 0|<= 0)\))" % col, totals) is None, (name, col)
            assert len(re.findall(r"sum\(%s\) FILTER \(WHERE COALESCE\(our_staked, 0\) <= 0\)" % col, totals)) == (1 if col == "our_settled" else 0)
        # sign agreement still counts every row: the OPPOSITE books are named, not hidden
        assert "count(*) FILTER (WHERE sign_agree = 'same') AS same_sign" in totals
        assert "count(*) FILTER (WHERE sign_agree = 'OPPOSITE') AS opposite_sign" in totals
    pr, _ = _preset(text, "paired-ratio")
    totals = pr[pr.rindex("(SELECT count(*) AS markets, "):]
    assert f"sum(his_cost_net) {STAKED}" in totals
    assert (f"CASE WHEN sum(his_cost_net) {STAKED} > 0 AND sum(our_staked) {STAKED} > 0 AND sum(his_pnl) {STAKED} <> 0"
            f" THEN round(((sum(our_settled) {STAKED} / sum(our_staked) {STAKED}) / (sum(his_pnl) {STAKED} /"
            f" sum(his_cost_net) {STAKED}))::numeric, 2) END AS roi_ratio_net") in totals


def test_the_hourly_line_leads_with_the_net_ratio_and_keeps_the_gross_beside_it():
    text = YML.read_text()
    pr, _ = _preset(text, "paired-ratio")
    assert LINE in pr and pr.count("format('PAIRED") == 1
    i = pr.index("his_roi_net %s")
    assert i < pr.index("roi_ratio_net %sx") < pr.index("gross his_roi %s roi_ratio %sx") < pr.index("same %s/%s")
    h, _ = _preset(text, "hourly")
    assert LINE in h and "format('PAIRED %s mkts his_roi %s" not in h


def test_the_since_arms_are_the_bare_presets_with_the_clock_on_every_books_window():
    text = YML.read_text()
    for name, n_windows, to in (("paired-day", 2, 60000), ("paired-ratio", 4, 120000)):
        bare, bare_to = _preset(text, name)
        guard, sql, arm_to = _pattern_arm(text, name)
        assert arm_to == bare_to == to
        assert guard == ('                %s=*) if [[ ! "$ARG" =~ ^%s=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2})?Z)$ ]];'
                         ' then echo "%s: arg must be %s=<YYYY-MM-DDTHH:MM[:SS]Z> (got \'$ARG\')"; exit 1; fi'
                         % (name, name, name, name)), guard
        assert text[text.index(guard) + len(guard):].startswith('\n                  PS="${BASH_REMATCH[1]}"\n                  SQL="')
        assert bare.count(WINDOW) == n_windows and SINCE not in bare
        assert sql == bare.replace(WINDOW, WINDOW + SINCE), "the arm is the bare SQL with the clock on the windows"
        assert sql.count(SINCE) == n_windows
        # the arm sits right after its bare label, before the next label
        assert text.index("                %s) SQL=" % name) < text.index(guard)
    # the whole set: the bare labels and the hourly carry no clock
    h, _ = _preset(text, "hourly")
    assert "$PS" not in h and text.count("'$PS'::timestamptz") == 6


def test_the_help_line_lists_the_patterns_in_case_order_and_the_paired_day_collapse_is_untouched():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    assert line.rstrip().endswith("patterns: book=<id>, paired-day=<YYYY-MM-DDTHH:MM[:SS]Z>,"
                                  " paired-ratio=<YYYY-MM-DDTHH:MM[:SS]Z>, tennis-witness=<YYYY-MM-DD>[:<surname>,<surname>...]\"; exit 1 ;;")
    case = text[text.index('case "$ARG" in'):text.index(line)]
    assert case.index("book=*)") < case.index("paired-day=*)") < case.index("paired-ratio=*)") < case.index("tennis-witness=*)")
    patterns = re.findall(r"^ {16}([a-z0-9-]+=)\*\) ", case, re.M)
    assert patterns == ["book=", "paired-day=", "paired-ratio=", "tennis-witness="]
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", case, re.M)
    assert names == labels and "|paired-day|paired-ratio|latency-census|" in line
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    # the `his` CTE exits-paired lifts from paired-day (its inner window and WHERE) is untouched
    from tests import test_render_ops_exits_paired as ep
    ep.test_the_exits_paired_preset_reads_his_rows_by_the_paired_days_own_collapse()
    for name in ("paired-day", "paired-ratio"):
        block = text[text.rindex("# THE PAIRED", 0, text.index("                %s) SQL=" % name)):text.index("                %s) SQL=" % name)]
        words = ("FILL lane 0b", "SETTLED EPISODE") if name == "paired-day" else ("owner decision D3", "NET RATIO", "1.81x")
        for word in words + ("zero-stake", "%s=<ISO>" % name):
            assert word in block, (name, word)
