"""The COPIES cohort — the record the copy-trading thesis stands on.

Owner order 2026-08-20 morning ("I am going to have to start showing
that our system is profitable"): the whale-copy sleeves are the
profitable core (+$5.3k uncapped since Aug 1: RN1 +$2.5k, swisstony
+$2.1k, 0x2c33, HRH) and the account headline buries them under the
retired engine's residue. This surface is the copies-only scorecard —
uncapped, venue-backed via the order-level audit table (live_orders),
per-whale split and daily series — served publicly so the site can
headline the number the business is actually built on.

Pure aggregation is separated from the endpoint for unit tests.

FEES, SINCE 2026-09-04. Every dollar this surface published was GROSS:
`pnl` comes straight off live_orders and no venue fee has ever been
subtracted from it, while the copy lane is 100% taker on a venue whose
own market terms state a fee coefficient on every market. The record
now charges what it can read — the venue's own commission out of the
stored order receipt, and the venue's fee schedule where the venue
stated nothing, NEVER zero — and LABELS every figure that is still
gross as `pre_fee`, so nothing on this page can be read as net when it
is not. `pnl` keeps its meaning (the venue-basis gross, which the
Kalshi merge and every other consumer already read); `fee_usd` and
`pnl_net` are new keys beside it.

The charge is the ENTRY leg only. A sale's receipt is never written
back onto the entry row (live_executor has six `SET raw` sites and not
one of them is a sale), so the exit's commission is not in data we
hold: `pnl_net` is an UPPER bound on true net and says so.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

RECORD_TZ = ZoneInfo("America/New_York")

# The census that reads the venue's commission off the stored receipts.
# Bounded (a census over the cap is unreadable, not truncated), timed,
# and CONTAINED: an unreadable census costs the net figure and its
# label, never the record.
FEE_CENSUS_ROW_CAP = 4000
FEE_CENSUS_TIMEOUT_S = 8.0

# ONE BUDGET FOR THE WHOLE REQUEST'S CENSUS WORK, NOT ONE PER CENSUS.
#
# build() takes TWO censuses — the settled cohort's and the partials' —
# and each used to carry its own full 8s budget, with the second
# started unconditionally after the first had already spent its entire
# budget without an answer. A merely SLOW database (the case a
# detoasting read produces; a database that FAULTS answers in
# milliseconds) therefore made this public, unauthenticated, uncached
# endpoint wait 16 seconds and hold an ASGI worker for the duration,
# per site visitor, for a figure that is a LABEL on the page.
#
# So the deadline belongs to the request: the second census gets what
# is left of it and no more, and if the first census could not read at
# all, the second is not attempted — the dependency has already
# answered that question. Below this floor there is not enough budget
# left to be worth a round trip, and the census is skipped and SAYS SO
# on the payload rather than the page hanging for it. Every row is
# still charged the fee SCHEDULE off its own entry fill, never zero.
FEE_CENSUS_MIN_BUDGET_S = 0.25

# HOW MANY ROWS THE PUBLIC PAGE ASKS THE DATABASE TO OPEN.
#
# /api/copies-record is public, unauthenticated and uncached, and it is
# the endpoint the site calls hardest (Wall, Mission, TrackRecord,
# Accounts and lib/record.ts all read it). The census lifts only four
# JSON paths per row on the wire, but Postgres must DETOAST the whole
# `raw` column — the venue's complete API response for every order — to
# evaluate them, and the 8s wait_for bounds the CALLER's wait, not the
# database's work: a cancelled query has already done the I/O.
#
# So the census is bounded to the newest rows rather than to the whole
# record, which grows without limit. Rows outside the window are still
# CHARGED — the venue's fee schedule off their own entry fill, never
# zero — they simply do not get the venue's own stated commission read
# for them. Today that costs nothing measurable: no commission VALUE
# has ever been observed from this venue (pmus.py:2136-2139), so the
# census's whole yield is currently zero rows. `fee_census` on the
# payload says how many rows were asked for and how many were charged
# outside the window, so this can never silently become the reason a
# venue value stopped being read.
FEE_CENSUS_MAX_IDS = 400

# Serve-side labels. Anything not named net IS gross.
PRE_FEE = "pre_fee"
NET_OF_ENTRY_FEE = "net_of_entry_fee"

# A PARTIAL LINE'S NET IS NOT A SETTLED LINE'S NET, and both are served
# in the same `trades` array.
PARTIAL_NET_BASIS = (
    "the WHOLE entry leg's fee charged against a PARTIAL realization: "
    "`pnl` here is the realized leg so far and the entry bought every "
    "share, including the ones still held, so this is a LOWER bound on "
    "this line's net — not the same figure as a settled line's pnl_net")

# The copy sources. 'underdog', 'arb', 'manual' and the engine's own
# categories are NOT copies and never count here; an unknown username
# is excluded rather than guessed in (fail closed — a new whale joins
# this list when the owner promotes one).
COPY_WHALES = frozenset({
    "rn1", "swisstony", "kch123", "homerunhazard",
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
    # Dossier promotions (owner order 2026-08-21): $100 probation clips.
    "ferrarichampions2026", "0x076daa87",
})

# Display names for the per-whale split (addresses are unreadable).
DISPLAY = {
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465": "0x2c33",
    "rn1": "RN1",
    "swisstony": "SwissTony",
    "kch123": "kch123",
    "homerunhazard": "HomeRunHazard",
    "ferrarichampions2026": "ferrariChampions2026",
    "0x076daa87": "0x076daa87",
}


# CRYPTO copy sources (owner order 2026-08-21): detected by the fast
# lane and served to the engine's Kalshi crypto leg — deliberately NOT
# in COPY_WHALES, so the Polymarket sports record and executor never
# see them.
CRYPTO_WHALES = frozenset({"0xf705fa04", "jnstrtprdctnmrkts"})


# Sleeves that are neither copies nor "software": their P&L is its own
# story (dog experiment, arb class, the desk's manual relay tickets).
NON_COPY_SLEEVES = frozenset({"underdog", "arb", "manual"})


def _accrue_fee(bucket: dict, row: dict) -> None:
    """Fold one row's entry-leg fee into a bucket.

    A row whose fee is UNMEASURED is COUNTED, never charged zero: it is
    the count that makes the bucket's net an upper bound instead of a
    claim.
    """
    fee = row.get("fee_usd")
    if fee is None:
        bucket["fee_unmeasured_rows"] = bucket.get(
            "fee_unmeasured_rows", 0) + 1
        return
    try:
        f = float(fee)
    except (TypeError, ValueError):
        bucket["fee_unmeasured_rows"] = bucket.get(
            "fee_unmeasured_rows", 0) + 1
        return
    bucket["fee_usd"] = round(bucket.get("fee_usd", 0.0) + f, 4)
    bucket["fee_measured_rows"] = bucket.get("fee_measured_rows", 0) + 1
    # A row some of whose legs could not be priced contributes real
    # dollars and an incomplete charge. It is counted so the bucket's
    # fee is visibly a LOWER bound rather than silently one.
    if row.get("fee_is_partial"):
        bucket["fee_partial_rows"] = bucket.get("fee_partial_rows", 0) + 1


def finish_net(bucket: dict) -> dict:
    """Give a bucket its net figures and its basis label. Pure.

    `pnl` stays the venue-basis GROSS it has always been and is now
    labelled `pre_fee`. `pnl_net` is that minus every fee we could
    read. When NOTHING could be read, `pnl_net` is None — unreadable,
    never a zero fee dressed as a net figure. When SOME rows could not
    be read, `pnl_net` is served with `pnl_net_is_upper_bound` true,
    because the fees we could not read only ever make it smaller.

    TWO THINGS THAT ARE NOT THE SAME. A bucket whose rows could not be
    charged is UNREADABLE and serves `fee_usd` None — a zero fee
    printed beside an unreadable net is the same zero this unit exists
    to delete. A bucket with no rows at all has nothing to charge and
    says so, rather than printing a failed reading every quiet morning
    and training the operator to ignore the word.
    """
    measured = bucket.get("fee_measured_rows", 0)
    unmeasured = bucket.get("fee_unmeasured_rows", 0)
    partial = bucket.get("fee_partial_rows", 0)
    bucket["pnl_basis"] = PRE_FEE
    bucket["fee_measured_rows"] = measured
    bucket["fee_unmeasured_rows"] = unmeasured
    if partial:
        bucket["fee_partial_rows"] = partial
    if not measured:
        empty = not unmeasured and not float(bucket.get("pnl") or 0.0)
        bucket["fee_usd"] = 0.0 if empty else None
        bucket["pnl_net"] = 0.0 if empty else None
        bucket["roi_net"] = None
        bucket["net_basis"] = ("nothing settled — nothing to charge"
                               if empty else "unreadable")
        bucket["pnl_net_is_upper_bound"] = False
        return bucket
    bucket.setdefault("fee_usd", 0.0)
    net = round(float(bucket.get("pnl") or 0.0) - float(bucket["fee_usd"]), 2)
    bucket["fee_usd"] = round(float(bucket["fee_usd"]), 2)
    bucket["pnl_net"] = net
    staked = float(bucket.get("staked") or 0.0)
    bucket["roi_net"] = round(net / staked, 4) if staked else None
    bucket["net_basis"] = NET_OF_ENTRY_FEE
    bucket["pnl_net_is_upper_bound"] = True     # the exit leg is unread
    return bucket


def label_unmeasured(bucket: dict) -> dict:
    """Label a bucket that arrived from a source carrying NO fee
    accounting at all — the engine's Kalshi export, whose block has no
    per-fill shares or prices and therefore no fee figure of any kind.

    Its settled rows fold in as unmeasured BY COUNT and the bucket gets
    the same three keys every other served bucket has: `pnl_basis`
    pre_fee, `pnl_net` None, `net_basis` unreadable. Clause 3 of this
    unit is "wherever it is served", and a page that mixes labelled
    rows with bare ones reads as net throughout — which is precisely
    the hazard the labels exist to remove. `merge_totals` already does
    this for the buckets that pass through it; its callers' fallback
    branches build theirs by hand and this is where they get it.
    """
    b = dict(bucket)
    if b.get("net_basis"):
        return b                       # already labelled: idempotent
    b["fee_measured_rows"] = int(b.get("fee_measured_rows") or 0)
    b["fee_unmeasured_rows"] = (int(b.get("fee_unmeasured_rows") or 0)
                                + int(b.get("settled") or 0))
    return finish_net(b)


def software_scorecard(rows: list[dict]) -> dict:
    """The complement cohort (partner report, owner order 2026-08-20
    evening): every settled order that is NOT a whale copy and NOT a
    named non-copy sleeve — the retired engine's book plus unattributed
    residue. Uncapped daily series so 'what the software cost us, day by
    day' is a served number instead of a capped display artifact."""
    rows = [r for r in rows
            if (r.get("whale") or "") not in COPY_WHALES
            and (r.get("whale") or "") not in NON_COPY_SLEEVES]
    total = {"settled": 0, "wins": 0, "losses": 0,
             "pnl": 0.0, "staked": 0.0}
    by_day: dict[str, dict] = {}
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        stake = float(r.get("filled_usd") or 0)
        for b in (total,
                  by_day.setdefault(r.get("day") or "undated", {
                      "day": r.get("day") or "undated",
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0})):
            b["settled"] += 1
            b["wins"] += 1 if pnl > 0 else 0
            b["losses"] += 1 if pnl < 0 else 0
            b["pnl"] = round(b["pnl"] + pnl, 2)
            b["staked"] = round(b["staked"] + stake, 2)
            _accrue_fee(b, r)
    finish_net(total)
    for d in by_day.values():
        finish_net(d)
    return {"cohort": "software", "uncapped": True, "total": total,
            "daily": sorted((d for d in by_day.values()
                             if d["day"] != "undated"),
                            key=lambda d: d["day"], reverse=True)[:62],
            # THIS COHORT'S NET IS 100% ESTIMATE, ALWAYS. `basis_note`
            # rides on the copies scorecard only, so the software
            # complement's `pnl_net` was served with no statement of
            # what stands behind it — and these rows are NEVER in the
            # receipt census (`charge_the_fee` filters the id list to
            # COPY_WHALES), so the venue's own stated commission is
            # never read for one of them.
            "basis": {**basis_note(),
                      "fee_source_order": [
                          "the venue's fee schedule — ALWAYS: these "
                          "rows are outside the receipt census, so no "
                          "venue-stated commission is ever read for "
                          "them",
                          "unmeasured — never zero"]}}


def scorecard(rows: list[dict]) -> dict:
    """rows: settled live_orders rows with keys whale (lowercased),
    day (ET YYYY-MM-DD), pnl, filled_usd, and optionally sport.
    Returns the copies payload."""
    rows = [r for r in rows if (r.get("whale") or "") in COPY_WHALES]
    total = {"settled": 0, "wins": 0, "losses": 0,
             "pnl": 0.0, "staked": 0.0}
    by_whale: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_ws: dict[tuple, dict] = {}
    by_dw: dict[tuple, dict] = {}
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        stake = float(r.get("filled_usd") or 0)
        disp = DISPLAY.get(r["whale"], r["whale"])
        day = r.get("day") or "undated"
        sport = r.get("sport") or "unknown"
        for b in (total,
                  by_whale.setdefault(r["whale"], {
                      "whale": disp,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0}),
                  by_day.setdefault(day, {
                      "day": day, "settled": 0, "wins": 0, "losses": 0,
                      # per-day deployed (owner asked MERIDIAN "how much
                      # did we deploy yesterday in copies" 2026-08-22
                      # and it had no clean answer — now it does).
                      "pnl": 0.0, "staked": 0.0}),
                  by_ws.setdefault((disp, sport), {
                      "whale": disp, "sport": sport,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0}),
                  by_dw.setdefault((day, disp), {
                      "day": day, "whale": disp,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0})):
            b["settled"] += 1
            b["wins"] += 1 if pnl > 0 else 0
            b["losses"] += 1 if pnl < 0 else 0
            b["pnl"] = round(b["pnl"] + pnl, 2)
            if "staked" in b:
                b["staked"] = round(b["staked"] + stake, 2)
            _accrue_fee(b, r)
    total["roi"] = (round(total["pnl"] / total["staked"], 4)
                    if total["staked"] else None)
    total["win_rate"] = (round(total["wins"] / total["settled"], 4)
                         if total["settled"] else None)
    for w in by_whale.values():
        w["roi"] = (round(w["pnl"] / w["staked"], 4)
                    if w["staked"] else None)
    for w in by_ws.values():
        w["roi"] = (round(w["pnl"] / w["staked"], 4)
                    if w["staked"] else None)
    # EVERY SERVED BUCKET CARRIES ITS OWN BASIS. A page that shows one
    # net number beside four gross ones reads as net throughout.
    for bucket in ([total] + list(by_whale.values()) + list(by_day.values())
                   + list(by_ws.values()) + list(by_dw.values())):
        finish_net(bucket)
    return {
        "cohort": "copies",
        "uncapped": True,
        "total": total,
        "by_whale": sorted(by_whale.values(), key=lambda w: -w["pnl"]),
        "by_whale_sport": sorted(by_ws.values(),
                                 key=lambda w: (w["whale"], -w["pnl"])),
        # Full since-window (owner order 2026-08-22): the 31-day
        # truncation silently cut the record's own calendar once the
        # window outgrew a month.
        "daily": sorted((d for d in by_day.values()
                         if d["day"] != "undated"),
                        key=lambda d: d["day"], reverse=True),
        "daily_by_whale": sorted((d for d in by_dw.values()
                                  if d["day"] != "undated"),
                                 key=lambda d: (d["day"], d["whale"]),
                                 reverse=True)[:186],
        "basis": basis_note(),
    }


def basis_note() -> dict:
    """The one place this surface says what its dollars mean. Served
    beside every scorecard so no figure on the page can be read as net
    when it is gross."""
    from .proof2 import FEE_SCHEDULE_FORM, PMUS_FEE_COEFFICIENT
    return {
        "pnl": PRE_FEE,
        "roi": PRE_FEE,
        "pnl_net": NET_OF_ENTRY_FEE,
        "fee_source_order": ["the venue's own commission on the stored "
                             "order receipt",
                             "the venue's fee schedule",
                             "unmeasured — never zero"],
        "pmus_fee_coefficient": PMUS_FEE_COEFFICIENT,
        "fee_schedule_form": FEE_SCHEDULE_FORM,
        "pmus_fee_form_verified": False,
        "exit_leg_fee": ("unmeasured — a sale's receipt is not stored on "
                         "the entry row, so pnl_net is an UPPER bound on "
                         "true net"),
        # CLAUSE 3'S RESIDUAL, NAMED RATHER THAN IMPLIED. "Every served
        # bucket" is every bucket THIS surface serves. Two other
        # surfaces still publish copy P&L with no basis key at all, and
        # a page that labels one surface and not its neighbour makes
        # the unlabelled one read as the complete figure. Neither file
        # is in this unit's ownership, so this says so instead of
        # pretending otherwise.
        "still_unlabelled_elsewhere": [
            "api/track_record.py — net_pnl, roi and the daily pnl "
            "series are all pre-fee and carry no basis key",
            "analytics/proof.py — roi and pnl, same",
        ],
    }


def trades_list(rows: list[dict], limit: int = 400,
                with_fees: bool = False) -> list[dict]:
    """The public copy ledger (owner order 2026-08-22): the newest
    settled/cashed-out copy rows, one line each, display-named. Every
    row carries its venue and the copy latency (owner order
    2026-08-28: the latency lives NEXT TO the trade, not in a
    separate report). Pure — rows must arrive newest-first (build()
    orders by settled_at).

    `with_fees` adds the per-row fee, its source and the net line. It
    is an ADDITIVE KWARG WITH A DEFAULT, which is the one shape this
    codebase calls parallel-safe (§2 rule 1 of the mirror programme:
    arity or return-shape changes are not). The served ledger always
    passes it; the default keeps the row shape that
    `test_trades_list_copy_rows_only_display_named` pins exactly.
    """
    out = []
    for r in rows:
        if (r.get("whale") or "") not in COPY_WHALES:
            continue
        lat = r.get("latency_s")
        pnl = round(float(r.get("pnl") or 0), 2)
        fee = r.get("fee_usd")
        line = {"day": r.get("day"),
                "whale": DISPLAY.get(r["whale"], r["whale"]),
                "slug": r.get("slug") or r.get("us_market_slug"),
                "stake": round(float(r.get("filled_usd") or 0), 2),
                "pnl": pnl,
                "status": r.get("status") or "settled",
                "sport": r.get("sport"),
                "venue": r.get("venue"),
                "latency_s": (round(float(lat), 2)
                              if isinstance(lat, (int, float))
                              else None)}
        if with_fees:
            line.update({
                "pnl_basis": PRE_FEE,
                "fee_usd": (round(float(fee), 4)
                            if fee is not None else None),
                "fee_source": r.get("fee_source"),
                "pnl_net": (round(pnl - float(fee), 2)
                            if fee is not None else None),
                # A SETTLED LINE'S NET AND A PARTIAL LINE'S NET DO NOT
                # MEAN THE SAME THING, and build() puts them in the same
                # `trades` array. This line is a whole entry's fee
                # against that entry's whole realized P&L; the partial
                # lines say on their own face that they are not.
                "net_basis": (NET_OF_ENTRY_FEE if fee is not None
                              else "unreadable")})
        out.append(line)
        if len(out) >= limit:
            break
    return out


def partials_list(rows: list[dict]) -> list[dict]:
    """Ledger lines for rows still open on a remainder after a partial
    cash-out: the realized leg so far, the shares left. status
    'partial_cashout' so the front end tags them; never counted in the
    settled totals. Pure; tested.

    WHAT `pnl_net` MEANS ON ONE OF THESE LINES, said on the line. `pnl`
    is the realized leg SO FAR and `fee_usd` is the WHOLE entry's fee —
    the entry bought every share, including the ones still held, and
    the fee it paid is not divisible by anything this reader can see
    (the exit legs' own receipts are not stored). Charging all of it
    against part of the realization is the conservative direction and
    it is deliberate; what was missing is that these lines sit in the
    same `trades` array as settled lines whose `pnl_net` is a complete
    round-number-of-shares net, with nothing distinguishing them. Each
    partial line now carries its own `net_basis` saying it is a LOWER
    bound on that line's net.
    """
    out = []
    for r in rows:
        if (r.get("whale") or "") not in COPY_WHALES:
            continue
        pnl = float(r.get("pnl") or 0)
        if abs(pnl) < 0.005:
            continue
        lat = r.get("latency_s")
        fee = r.get("fee_usd")
        out.append({"day": None,
                    "whale": DISPLAY.get(r["whale"], r["whale"]),
                    "slug": r.get("us_market_slug"),
                    "stake": round(float(r.get("filled_usd") or 0), 2),
                    "pnl": round(pnl, 2),
                    "pnl_basis": PRE_FEE,
                    "fee_usd": (round(float(fee), 4)
                                if fee is not None else None),
                    "fee_source": r.get("fee_source"),
                    "pnl_net": (round(pnl - float(fee), 2)
                                if fee is not None else None),
                    "net_basis": (PARTIAL_NET_BASIS if fee is not None
                                  else "unreadable"),
                    "status": "partial_cashout",
                    "remaining_shares": float(r.get("remaining_shares") or 0),
                    "orig_shares": float(r.get("orig_shares") or 0),
                    "venue": r.get("venue"),
                    "latency_s": (round(float(lat), 2)
                                  if isinstance(lat, (int, float)) else None)})
    return out


def today_stats(rows: list[dict], today: str,
                with_fees: bool = False) -> dict:
    """Today's copy scoreline (ET), uncapped. Pure; tested.

    `with_fees` adds the fee and net lines — additive kwarg with a
    default, so the exact four-key return
    `test_partials_are_their_own_lines_and_never_settled_rows` pins is
    unchanged for every caller that does not ask."""
    t = {"pnl": 0.0, "settled": 0, "wins": 0, "losses": 0}
    for r in rows:
        if (r.get("whale") or "") not in COPY_WHALES \
                or r.get("day") != today:
            continue
        pnl = float(r.get("pnl") or 0)
        t["pnl"] = round(t["pnl"] + pnl, 2)
        t["settled"] += 1
        t["wins"] += 1 if pnl > 0 else 0
        t["losses"] += 1 if pnl < 0 else 0
        if with_fees:
            _accrue_fee(t, r)
    return finish_net(t) if with_fees else t


async def charge_the_fee(pool, rows: list[dict],
                         census: dict | None = None,
                         budget_s: float | None = None) -> list[dict]:
    """Stamp fee_usd / fee_source on every row, venue's own value first.

    THE CENSUS IS BOUNDED AND ITS FAILURE IS CONTAINED. It reads the
    four JSON paths the receipts keep their executions in, keyed by id,
    under a timeout, ONLY FOR THE COPY ROWS — this record's own subject,
    which keeps the software complement (the retired engine's whole
    book) out of it — and only for the NEWEST `FEE_CENSUS_MAX_IDS` of
    those, because the record grows without limit and this endpoint is
    public and uncached. Over the row cap, past the timeout, or on any
    database fault it is skipped entirely and every row falls back to
    the venue's fee SCHEDULE computed off its own entry fill — never to
    zero, and never to a broken page. A row on a venue with no schedule
    and no stored value stays `unmeasured`, which the surfaces serve as
    unreadable.

    `census`, when a dict is passed, is filled in with what actually
    happened: how many copy rows there were, how many the census asked
    for, and whether the read came back. Without it a refusal is
    indistinguishable on the page from a census that ran and found
    nothing — which is exactly how a growing record silently stops
    reading the venue's own commission.

    `budget_s` is what is LEFT of the request's single census deadline
    (see FEE_CENSUS_MIN_BUDGET_S). It bounds this census's wait and,
    at or below the floor, skips it outright and says so — because two
    censuses each taking their own full budget is how a merely slow
    database made a public page wait sixteen seconds. Additive kwarg
    with a default: a caller that does not pass one gets exactly the
    previous behaviour.
    """
    from .proof2 import FEE_CENSUS_TIMEOUT_S as _T
    from .proof2 import charge_fees, receipts_by_id

    ids = [r.get("id") for r in rows
           if (r.get("whale") or "") in COPY_WHALES
           and r.get("id") is not None]
    asked = ids[:FEE_CENSUS_MAX_IDS]        # rows arrive newest-first
    budget = (FEE_CENSUS_TIMEOUT_S if budget_s is None
              else min(FEE_CENSUS_TIMEOUT_S, float(budget_s)))
    skipped = budget < FEE_CENSUS_MIN_BUDGET_S
    receipts = None
    if not skipped:
        try:
            receipts = await receipts_by_id(
                pool, asked,
                cap=FEE_CENSUS_ROW_CAP, timeout=min(budget, _T))
        except Exception:  # noqa: BLE001 — contained: the schedule charges
            receipts = None
    if isinstance(census, dict):
        census.update({
            "copy_rows": len(ids),
            "rows_asked": 0 if skipped else len(asked),
            "rows_beyond_the_window": max(0, len(ids) - len(asked)),
            "window": FEE_CENSUS_MAX_IDS,
            "read": receipts is not None,
            "skipped": skipped,
            "budget_s": round(max(0.0, budget), 2),
            "receipts_returned": len(receipts or {}),
            "note": ("rows beyond the window are charged the venue's fee "
                     "SCHEDULE off their own entry fill, never zero; only "
                     "the venue's own stated commission is unread for them"
                     if receipts is not None else
                     "this census was NOT TAKEN: the request's single "
                     "census deadline was already spent, or the earlier "
                     "census proved the database cannot answer one. Every "
                     "row here is charged the fee SCHEDULE, never zero, "
                     "and no venue-stated commission was read"
                     if skipped else
                     "the census was refused or timed out: every row on "
                     "this page is charged the fee SCHEDULE, never zero, "
                     "and no venue-stated commission was read"),
        })
    if receipts:
        rows = [{**r,
                 "receipt": (receipts.get(r.get("id")) or {}).get("receipt"),
                 "lane": (receipts.get(r.get("id")) or {}).get("lane"),
                 "orig_shares": (r.get("orig_shares")
                                 if r.get("orig_shares") is not None
                                 else (receipts.get(r.get("id")) or {}).get(
                                     "orig_shares"))}
                for r in rows]
    return charge_fees(rows)


async def build(since_day: str) -> dict:
    """Endpoint assembly: settled copy orders from the audit table."""
    from ..db import get_pool

    from ..copy_sports import sport_of

    pool = await get_pool()
    rows = await _settled_copy_rows(pool)
    windowed = []
    for r in rows:
        d = dict(r)
        if (d.get("day") or "") < since_day:
            continue
        d["sport"] = sport_of(d.get("us_market_slug") or "")
        windowed.append(d)
    census: dict = {}
    # ONE DEADLINE FOR THIS REQUEST'S CENSUS WORK. Both censuses draw on
    # it; the partials census below gets what is left and no more. A
    # slow database costs this page one budget, not one per census.
    census_deadline = time.monotonic() + FEE_CENSUS_TIMEOUT_S
    windowed = await charge_the_fee(pool, windowed, census)
    out = scorecard(windowed)
    # WAS THE VENUE'S OWN COMMISSION EVEN LOOKED FOR? A census refused
    # for exceeding its cap, timed out, faulted or bounded to the
    # window is otherwise indistinguishable on this page from one that
    # ran and found no venue value — which is exactly how a growing
    # record silently stops reading the venue's own number.
    out["fee_census"] = census
    # The complement cohort rides along so the partner report's
    # "software cost, day by day" is served uncapped from the same
    # audit table as the copies record.
    out["software"] = software_scorecard(windowed)
    # Live edge of the record (owner order 2026-08-22): what the copy
    # sleeves have ON the table right now, today's scoreline, and the
    # row-level ledger behind the aggregates.
    open_rows = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '')) AS whale,
               count(*)::int AS count,
               COALESCE(sum(COALESCE(NULLIF(filled_usd, 0),
                                     requested_usd)), 0)::float8 AS stake
        FROM live_orders
        WHERE status IN ('submitting', 'filled')
          AND lower(COALESCE(whale_username, '')) = ANY($1::text[])
        GROUP BY 1
        """, list(COPY_WHALES))
    out["open"] = {
        "count": sum(r["count"] for r in open_rows),
        "stake": round(sum(r["stake"] for r in open_rows), 2),
        # per-whale open exposure (owner order 2026-08-28: the hub's
        # accounts view shows WHO the table is riding on)
        "by_whale": sorted(
            ({"whale": DISPLAY.get(r["whale"], r["whale"]),
              "count": r["count"], "stake": round(r["stake"], 2)}
             for r in open_rows),
            key=lambda w: -w["stake"])}
    out["trades"] = trades_list(windowed, with_fees=True)
    # PARTIAL CASH-OUTS ARE MONEY TOO (owner report 2026-09-02: "the
    # front end does not show sell orders (sold) on the ledger so the
    # P&L is incredibly wrong"). A whale who trims is mirrored by a
    # partial sale; the row stays 'filled' on the remainder with the
    # realized leg accumulated on pnl, and the ledger only lists
    # settled/cashed-out rows -- so the realized money was invisible
    # until the market resolved. They are listed here as their own
    # lines and summed separately; the settled totals are untouched
    # (the row's final pnl carries the same dollars once it settles).
    try:
        partial_rows = await pool.fetch(
            """
            SELECT lo.id,
                   lower(COALESCE(lo.whale_username, '')) AS whale,
                   lo.pnl::float8 AS pnl, lo.filled_usd::float8 AS filled_usd,
                   lo.fill_price::float8 AS fill_price,
                   lo.filled_shares::float8 AS remaining_shares,
                   -- THE FEE READER NEEDS A SHARE COLUMN IT KNOWS BY
                   -- NAME. Aliasing filled_shares away to
                   -- remaining_shares left the row carrying neither
                   -- `shares` nor `filled_shares`, so row_fee's
                   -- row-level charge could never fire and every
                   -- partial line without a stored receipt came back
                   -- `unmeasured` — which, on this repository's last
                   -- recorded observation, is every one of them.
                   lo.filled_shares::float8 AS filled_shares,
                   COALESCE(lo.orig_shares, lo.filled_shares)::float8 AS orig_shares,
                   lo.us_market_slug, lo.venue, lo.reaction_s::float8 AS latency_s
            FROM live_orders lo
            WHERE lo.status = 'filled' AND COALESCE(lo.pnl, 0) <> 0
              AND lower(COALESCE(lo.whale_username, '')) = ANY($1::text[])
            ORDER BY lo.placed_at DESC
            """, list(COPY_WHALES))
    except Exception:  # noqa: BLE001 — orig_shares is migration 040's
        partial_rows = []
    # THE TRIM'S ENTRY FEE IS CHARGED TOO, ON THE ENTRY. `filled_shares`
    # on a partly exited row is the REMAINDER; migration 040's
    # `orig_shares` is the count the entry actually happened at, and it
    # is what the schedule is charged on when the row's stored receipt
    # cannot be read. Charging the remainder would have understated
    # every trimmed row's fee in exact proportion to how much of it we
    # had already sold.
    # AND IT DRAWS ON THE REQUEST'S REMAINING CENSUS BUDGET, NOT ON A
    # SECOND FULL ONE. When the settled census could not read at all,
    # this one is not attempted: the dependency has already answered
    # that question, and asking it again is how this public page came
    # to wait two full timeouts back to back. The rows are still
    # charged — the schedule, off their own entry fill, never zero.
    pcensus: dict = {}
    left = census_deadline - time.monotonic()
    if not census.get("read"):
        pcensus["skipped_after_first_census_failed"] = True
        left = 0.0
    partials = partials_list(
        await charge_the_fee(pool, [dict(r) for r in partial_rows], pcensus,
                             budget_s=left))
    measured = [p for p in partials if p["fee_usd"] is not None]
    out["fee_census"] = {**(out.get("fee_census") or {}),
                         "partials": pcensus}
    out["partials"] = {"count": len(partials),
                       "realized": round(sum(p["pnl"] for p in partials), 2),
                       "realized_basis": PRE_FEE,
                       "fee_usd": (
                           round(sum(p["fee_usd"] for p in measured), 2)
                           if measured else None),
                       "fee_unmeasured_rows": len(partials) - len(measured),
                       "realized_net": (
                           round(sum(p["pnl"] for p in partials)
                                 - sum(p["fee_usd"] for p in measured), 2)
                           if measured else None),
                       "realized_net_basis": (PARTIAL_NET_BASIS
                                              if measured else "unreadable"),
                       "rows": partials}
    out["trades"] = partials + out["trades"]
    out["today"] = today_stats(
        windowed, datetime.now(RECORD_TZ).strftime("%Y-%m-%d"),
        with_fees=True)
    # KALSHI COPY SLEEVE MERGE (owner order 2026-08-22: "include volume
    # and pnl from Kalshi — we copied closer to double the volume listed
    # and lost a little on Kalshi"). The platform ledger only sees the
    # Polymarket executor; the Kalshi copy legs live in the ENGINE's
    # ledger and arrive via its heartbeat export. Merge is additive and
    # fail-open: no export -> Polymarket-only record, flagged.
    pm_total = dict(out["total"])
    # FLOORED ON THE SAME DAY AS THE POLYMARKET SIDE (owner order
    # 2026-09-02; found by the probe's EPOCHCHECK: the record said
    # since=2026-09-01 and served a first day of 2026-08-05, because the
    # engine's Kalshi block carries its own lifetime window).
    kexp = floor_export(await _kalshi_copies_export(pool), since_day)
    # The per-venue split is a served bucket too: the Kalshi side comes
    # from an export with no fee accounting at all, so it is labelled
    # unmeasured rather than sitting bare beside a labelled Polymarket
    # total.
    ktotal = (kexp or {}).get("total")
    out["venues"] = {"polymarket": pm_total,
                     "kalshi": (label_unmeasured(ktotal)
                                if isinstance(ktotal, dict) else ktotal)}
    out["kalshi_included"] = bool(kexp)
    if kexp:
        kopen = kexp.get("open") or {}
        if kopen.get("count") or kopen.get("stake"):
            out["open"] = {
                **out["open"],       # by_whale stays PM-side detail
                "count": out["open"]["count"] + int(kopen.get("count") or 0),
                "stake": round(out["open"]["stake"]
                               + float(kopen.get("stake") or 0), 2)}
        out["total"] = merge_totals(out["total"], kexp.get("total") or {})
        out["daily"] = merge_daily(out["daily"], kexp.get("daily") or [])
        out["by_whale"] = merge_by_whale(out["by_whale"],
                                         kexp.get("by_whale") or {})
        ktoday = next((d for d in (kexp.get("daily") or [])
                       if d.get("day") == datetime.now(RECORD_TZ)
                       .strftime("%Y-%m-%d")), None)
        if ktoday:
            out["today"] = merge_totals(out["today"], ktoday,
                                        keys=("pnl", "settled", "wins",
                                              "losses"))
    out["since"] = since_day
    out["generated_at"] = datetime.now(RECORD_TZ).isoformat()
    return out


# THE SETTLED COHORT, IN TWO SHAPES. `orig_shares` is migration 040's
# and it is the count the ENTRY happened at — `filled_shares` is
# decremented by every partial exit, so charging the entry fee on it
# understates the fee in exact proportion to how much was already sold.
# A database without 040 still serves the record: the second statement
# is the first one minus that column, and the fee falls back to the
# remainder exactly as it did before, which is the pre-existing
# behaviour and not a new one.
_SETTLED_SQL = """
        SELECT lo.id,
               lower(COALESCE(lo.whale_username, '')) AS whale,
               to_char(lo.settled_at AT TIME ZONE 'America/New_York',
                       'YYYY-MM-DD') AS day,
               lo.pnl, lo.filled_usd, lo.us_market_slug, lo.status,
               lo.venue,
               -- filled_shares / fill_price are migration 007 columns,
               -- so this query still runs on a database that has not
               -- applied 041. `lane` is 041's and is read ONLY by the
               -- fee census below, which is allowed to fail: the public
               -- record must never go down for want of a fee figure.
               lo.filled_shares::float8 AS filled_shares,
               __ORIG__
               lo.fill_price::float8 AS fill_price,
               COALESCE(lo.reaction_s,
                        EXTRACT(EPOCH FROM (lo.placed_at - t.ts))
                        )::float8 AS latency_s
        FROM live_orders lo
        LEFT JOIN trades t ON t.id = lo.trade_id
        WHERE lo.status IN ('settled', 'cashed_out')
          AND lo.settled_at IS NOT NULL
        ORDER BY lo.settled_at DESC
        """


async def _settled_copy_rows(pool) -> list:
    """The settled cohort, with the entry's own share count when the
    database can give it. Never the reason the record is down."""
    try:
        return await pool.fetch(_SETTLED_SQL.replace(
            "__ORIG__",
            "COALESCE(lo.orig_shares, lo.filled_shares)::float8 "
            "AS orig_shares,"))
    except Exception:  # noqa: BLE001 — migration 040 absent: the fee
        # falls back to the remainder, exactly as it did before 040.
        return await pool.fetch(_SETTLED_SQL.replace("__ORIG__", ""))



def floor_export(kexp: dict | None, since_day: str) -> dict | None:
    """The engine's Kalshi block, cut to the display window. Daily rows
    before since_day are dropped and the total is rebuilt from what
    remains; the per-whale split is lifetime in the block and has no
    per-day form, so it is dropped whenever a day was cut rather than
    served as if it were windowed. Open positions are current state and
    pass through. Pure; tested."""
    if not isinstance(kexp, dict):
        return kexp
    daily = [d for d in (kexp.get("daily") or []) if isinstance(d, dict)]
    kept = [d for d in daily if str(d.get("day") or "") >= since_day]
    cut = len(kept) < len(daily)
    out = dict(kexp)
    out["daily"] = kept
    if cut:
        total = {"settled": 0, "wins": 0, "losses": 0, "pnl": 0.0, "staked": 0.0}
        for d in kept:
            total = merge_totals(total, d)
        # Rebuilt from two fee-less sides, so merge_totals' fee block
        # never ran and this total would be served bare. It is the
        # Kalshi venue's own line on the record: labelled, unmeasured.
        out["total"] = label_unmeasured(total)
        out["by_whale"] = {}
        out["floored_to"] = since_day
    return out


def merge_totals(a: dict, b: dict,
                 keys: tuple = ("settled", "wins", "losses",
                                "pnl", "staked")) -> dict:
    """Additive venue merge; ROI/win-rate recomputed. Pure; tested.

    THE NET FIGURE SURVIVES THE MERGE OR IT DIES HONESTLY. `pnl` grows
    by the other venue's dollars, so a `pnl_net` carried over unchanged
    from side A would silently describe a smaller book than the `pnl`
    printed next to it. The engine's Kalshi export carries no per-fill
    shares or prices and therefore no fee figure at all, so its settled
    rows are folded in as UNMEASURED and the merged net is recomputed
    from what remains readable — never inherited.
    """
    m = dict(a)
    for k in keys:
        m[k] = round(float(a.get(k) or 0) + float(b.get(k) or 0), 2)
        if k in ("settled", "wins", "losses"):
            m[k] = int(m[k])
    if "staked" in m:
        m["roi"] = (round(m["pnl"] / m["staked"], 4)
                    if m.get("staked") else None)
    if m.get("settled"):
        m["win_rate"] = round(m.get("wins", 0) / m["settled"], 4)
    if "fee_usd" in a or "fee_usd" in b:
        m["fee_usd"] = round(float(a.get("fee_usd") or 0)
                             + float(b.get("fee_usd") or 0), 4)
        m["fee_measured_rows"] = (int(a.get("fee_measured_rows") or 0)
                                  + int(b.get("fee_measured_rows") or 0))
        # A side that brought settled rows but no fee accounting at all
        # brings them in as unmeasured, by count.
        b_unmeasured = int(b.get("fee_unmeasured_rows") or 0)
        if "fee_usd" not in b and "fee_measured_rows" not in b:
            b_unmeasured += int(b.get("settled") or 0)
        a_unmeasured = int(a.get("fee_unmeasured_rows") or 0)
        if "fee_usd" not in a and "fee_measured_rows" not in a:
            a_unmeasured += int(a.get("settled") or 0)
        m["fee_unmeasured_rows"] = a_unmeasured + b_unmeasured
        finish_net(m)
    return m


def merge_daily(pm_daily: list[dict], k_daily: list[dict]) -> list[dict]:
    """Merge the two venues' ET-day series (newest first). Pure."""
    by_day = {d["day"]: dict(d) for d in pm_daily}
    for kd in k_daily:
        day = kd.get("day")
        if not day:
            continue
        if day in by_day:
            by_day[day] = merge_totals(by_day[day], kd)
            by_day[day]["day"] = day
        else:
            # A KALSHI-ONLY DAY IS STILL A SERVED BUCKET. Built by hand
            # here, it used to land in the same array as the labelled
            # Polymarket days with no pnl_basis, no pnl_net and no
            # net_basis — a gross row indistinguishable from a net one.
            by_day[day] = label_unmeasured(
                {"day": day,
                 **{k: kd.get(k, 0) for k in
                    ("settled", "wins", "losses", "pnl", "staked")}})
    return sorted(by_day.values(), key=lambda d: d["day"], reverse=True)


def merge_by_whale(pm_bw: list[dict], k_bw: dict) -> list[dict]:
    """Fold the engine's per-whale Kalshi record into the display list.
    Engine keys are lowercase usernames; only COPY_WHALES merge (the
    crypto sleeve is not copies and its whales never appear here)."""
    by_name = {w["whale"]: dict(w) for w in pm_bw}
    for uname, kb in k_bw.items():
        if uname not in COPY_WHALES:
            continue
        disp = DISPLAY.get(uname, uname)
        krow = {"settled": kb.get("settled", 0),
                "wins": kb.get("wins", 0),
                "losses": kb.get("losses", 0),
                "pnl": kb.get("pnl", kb.get("realized", 0)) or 0,
                "staked": kb.get("staked", 0) or 0}
        if disp in by_name:
            merged = merge_totals(by_name[disp], krow)
            merged["whale"] = disp
            by_name[disp] = merged
        else:
            # Same rule as merge_daily's: a Kalshi-only whale is served
            # beside labelled ones and carries its own basis.
            by_name[disp] = label_unmeasured(
                {"whale": disp, **krow,
                 "roi": (round(krow["pnl"] / krow["staked"], 4)
                         if krow["staked"] else None)})
    return sorted(by_name.values(), key=lambda w: -float(w["pnl"] or 0))


async def _kalshi_copies_export(pool) -> dict | None:
    """The engine heartbeat's kalshi_copies_record block, or None."""
    try:
        raw = await pool.fetchval(
            "SELECT detail FROM service_heartbeats "
            "WHERE service = 'edge_engine'")
        if not raw:
            return None
        import json as _json
        detail = raw if isinstance(raw, dict) else _json.loads(raw)
        exp = detail.get("kalshi_copies_record")
        if not isinstance(exp, dict) or "total" not in exp:
            return None
        return exp
    except Exception:  # noqa: BLE001 — record serves PM-only, flagged
        return None
