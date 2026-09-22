"""EPISODES: whole trading lifecycles, with the inventory carried.

WHY THIS FILE EXISTS. Every economic number BETTOR has produced so far
was CONDITIONAL ARITHMETIC on a single book snapshot: "if both legs
fill, the pair earns +0.0086." A fill is not the end of a trade and a
cancellation is not the end of a trade. If inventory remains, the
episode is still running and its result is not yet known.

So this module defines an episode with a beginning and an end, runs it
on the time-ordered tape, and CARRIES EVERY REMAINING POSITION INTO
THE RESULT. No episode is dropped for being unfinished; unfinished is
a reported outcome.

────────────────────────────────────────────────────────────────────
THE EPISODE, DEFINED
────────────────────────────────────────────────────────────────────

BEGINS   at an observation where the market is OPEN, the book is
         two-sided and the spread is at least one tick. Two resting
         orders go up, both priced BY THE ENGINE
         (`incremental_ev("QUOTE_BID"/"QUOTE_OFFER")`):

           YES leg   buy YES  at the engine's bid quote,  size S
           NO  leg   buy NO   at 1 - the engine's offer,  size S

         The NO leg IS our YES offer. PMUS carries
         `shortQuote = 1 - bestBid`, so the NO book is the exact
         mirror of the YES book and a resting YES offer at `po` is a
         resting NO bid at `1 - po`. Modelling it as two long legs
         keeps the cash unambiguous: one YES plus one NO pays exactly
         1 at settlement, whatever happens.

         Collateral is committed at this instant, before any fill.

PARTIAL  each leg fills in pieces. Every fill is its own event with
FILLS    its own size, its own price and ITS OWN BANKER-ROUNDED FEE.
         A four-contract order that fills as 1+1+1+1 pays four
         separately-rounded fees, which is not the same number as one
         four-contract fill. That difference is the whole of section 3
         of the directive and it is applied here, not asserted.

CANCEL   at the quoting horizon the unfilled legs are cancelled. The
ACK      cancel is NOT effective at the instant it is requested: it
         takes effect from the NEXT observation, so any print in the
         interval containing the request can still fill us. The
         cancellation race is charged AGAINST us, every time.

RECOVERY unmatched inventory is not abandoned. The policy rests a
         maker exit on the opposite side for R_WAIT observations, and
         if that does not fill, crosses out as a taker. Matched pairs
         are left to settle: they pay exactly 1 and completing them
         early can only cost fees.

ENDS     at the first of:
           FLAT_PAIRED        both legs filled; a matched pair remains,
                              which settles at 1 with certainty
           FLAT_EXITED_MAKER  residual exited on a resting quote
           FLAT_EXITED_TAKER  residual crossed out
           CARRIED_SETTLED    the market expired while we held; valued
                              at the OBSERVED settlement
           CARRIED_OPEN       the capture ended while we held; valued
                              at the touch we could actually hit
           NEVER_FILLED       neither leg filled; the episode still
                              consumed collateral for its whole life

────────────────────────────────────────────────────────────────────
WHAT IS MODELLED AND WHAT IS ASSUMED -- the assumptions, named
────────────────────────────────────────────────────────────────────

A1  PRINTS. `sharesTraded` is cumulative; its increase between two
    observations is the volume printed in that interval. `lastTradePx`
    gives ONE price for that interval -- the last one. If several
    prints happened at different prices the interval's whole volume is
    attributed to that single price. This cuts both ways and cannot be
    corrected from a BBO feed.

A2  QUEUE. Quoting strictly inside the spread puts us alone at our
    price, so we are at the front. Quoting at the touch puts us behind
    the observed depth. Both variants are reported, and a third
    (`QUEUE_BEHIND`) assumes we are behind the depth even when inside,
    because a real book may have hidden or simultaneous improvement.

A3  OUR OWN QUOTE IS NOT IN THE TAPE. The recorded book never saw our
    order. A resting quote inside the spread would have changed what
    others did. This is the standard replay limitation and it is not
    fixable from recorded data -- only by resting real orders.

A4  NO REBATE ELIGIBILITY IS VERIFIED. Everything is computed TWICE:
    once with the published maker schedule applied, and once with
    every rebate set to zero. If the two disagree about the sign, the
    policy's edge is the rebate and the rebate is unverified.

Run:  python research/beta48/bettor_episodes.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_policy_ev as ev                                   # noqa: E402
import bettor_tape as tape                                      # noqa: E402

# ── the policy's parameters, frozen here so they are visible ──────────
QUOTE_HORIZON = 20      # observations the pair of quotes rests for
RECOVERY_WAIT = 10      # observations a maker exit rests before crossing
COOLDOWN = 1            # observations between episodes in one market

# Episode end states
FLAT_PAIRED = "FLAT_PAIRED"
FLAT_EXITED_MAKER = "FLAT_EXITED_MAKER"
FLAT_EXITED_TAKER = "FLAT_EXITED_TAKER"
CARRIED_SETTLED = "CARRIED_SETTLED"
CARRIED_OPEN = "CARRIED_OPEN_AT_HORIZON"
NEVER_FILLED = "NEVER_FILLED"

QUEUE_MODELS = ("QUEUE_FRONT_IF_INSIDE", "QUEUE_BEHIND_ALWAYS")


def _maker_fee(px, n, rebates_on):
    """Signed cash from the maker schedule on ONE fill of n contracts."""
    if not rebates_on:
        return 0.0
    return ev.fee(px, n, maker=True)          # positive: we receive


def _taker_fee(px, n):
    return ev.fee(px, n, maker=False)         # negative: we pay


class Episode:
    """One quoting lifecycle in one market. Nothing is dropped."""

    def __init__(self, slug, event, i0, row, size, tick, rebates_on,
                 queue_model):
        self.slug, self.event = slug, event
        self.i0, self.t0 = i0, row["t_iso"]
        self.size = float(size)
        self.tick = tick
        self.rebates_on = rebates_on
        self.queue_model = queue_model

        book = ev.Book(slug=slug, bid=row["bid"], ask=row["ask"], tick=tick)
        qb = ev.incremental_ev("QUOTE_BID", book, ev.Inventory(),
                               contracts=self.size, as_fill=0.0, p_fill=1.0,
                               rebate_eligible=rebates_on)
        qo = ev.incremental_ev("QUOTE_OFFER", book, ev.Inventory(),
                               contracts=self.size, as_fill=0.0, p_fill=1.0,
                               rebate_eligible=rebates_on)
        self.pb = qb.terms["quote_price"]          # our YES bid
        self.po = qo.terms["quote_price"]          # our YES offer
        self.entry_book = {"bid": row["bid"], "ask": row["ask"],
                           "spread": round(row["ask"] - row["bid"], 6),
                           "spread_ticks": int(round(
                               (row["ask"] - row["bid"]) / tick)),
                           "mid": round(0.5 * (row["bid"] + row["ask"]), 6),
                           "bid_at_touch": self.pb == row["bid"],
                           "offer_at_touch": self.po == row["ask"]}

        # position, in LONG legs -- one YES + one NO pays exactly 1
        self.yes = 0.0
        self.no = 0.0
        self.cash = 0.0            # realised cash, fees included
        self.fills = []
        self.rebates_paid = 0.0
        self.taker_fees_paid = 0.0

        self.open_yes = self.size   # unfilled size on the YES bid
        self.open_no = self.size    # unfilled size on the NO bid
        self.cancel_requested_at = None
        self.cancel_effective_from = None
        self.race_fills = 0

        self.status = None
        self.end_i = None
        self.end_t = None
        self.residual_value = 0.0
        self.residual_basis = None
        self.settlement = None
        self.notes = []

    # ── collateral ────────────────────────────────────────────────────
    def collateral_now(self):
        """Cash the venue is holding against us RIGHT NOW.

        Two readings, both reported, because which one PMUS uses for
        RESTING orders is UNDOCUMENTED and was never read back from the
        venue:

          filled_only  only the legs that actually filled
          with_resting filled legs PLUS the unfilled resting size
        """
        filled = self.yes_cost + self.no_cost
        resting = self.pb * self.open_yes + (1.0 - self.po) * self.open_no
        return filled, filled + resting

    @property
    def yes_cost(self):
        return sum(f["cash_out"] for f in self.fills if f["leg"] == "YES")

    @property
    def no_cost(self):
        return sum(f["cash_out"] for f in self.fills if f["leg"] == "NO")

    # ── fills ─────────────────────────────────────────────────────────
    def _fill(self, leg, px, n, i, row, maker, tag):
        """Record ONE fill. The fee is computed on THIS fill's size."""
        if n <= 0:
            return
        f = _maker_fee(px, n, self.rebates_on) if maker else _taker_fee(px, n)
        if maker:
            self.rebates_paid += f
        else:
            self.taker_fees_paid += f
        self.cash += -px * n + f
        if leg == "YES":
            self.yes += n
        else:
            self.no += n
        self.fills.append({
            "leg": leg, "px": px, "n": n, "maker": maker, "tag": tag,
            "i": i, "t": row["t_iso"], "fee_cash": round(f, 6),
            "fee_per_contract": round(f / n, 8),
            "cash_out": px * n,
            "raw_fee_before_rounding": round(
                (ev.THETA_MAKER if maker else ev.THETA_TAKER)
                * n * px * (1.0 - px), 8)})

    def _sell(self, leg, px, n, i, row, maker, tag):
        """Sell an owned leg (reduces the position, brings cash in)."""
        if n <= 0:
            return
        f = _maker_fee(px, n, self.rebates_on) if maker else _taker_fee(px, n)
        if maker:
            self.rebates_paid += f
        else:
            self.taker_fees_paid += f
        self.cash += px * n + f
        if leg == "YES":
            self.yes -= n
        else:
            self.no -= n
        self.fills.append({
            "leg": leg, "px": px, "n": -n, "maker": maker, "tag": tag,
            "i": i, "t": row["t_iso"], "fee_cash": round(f, 6),
            "fee_per_contract": round(f / n, 8), "cash_out": -px * n,
            "raw_fee_before_rounding": round(
                (ev.THETA_MAKER if maker else ev.THETA_TAKER)
                * n * px * (1.0 - px), 8)})

    # ── the fill model ────────────────────────────────────────────────
    def _available(self, side, prev, row, dvol, print_px):
        """Shares of `dvol` that could reach OUR order on `side`.

        side "YES": our bid at self.pb -- fills when someone sells at or
        below it, i.e. print_px <= pb.
        side "NO": our YES offer at self.po -- fills when someone buys
        at or above it, i.e. print_px >= po.
        """
        if dvol <= 0 or print_px is None:
            return 0.0
        if side == "YES":
            if print_px > self.pb + 1e-9:
                return 0.0
            inside = (prev["bid"] is not None
                      and self.pb > prev["bid"] + 1e-9)
            depth = prev["bid_depth"] or 0
        else:
            if print_px < self.po - 1e-9:
                return 0.0
            inside = (prev["ask"] is not None
                      and self.po < prev["ask"] - 1e-9)
            depth = prev["ask_depth"] or 0
        if self.queue_model == "QUEUE_FRONT_IF_INSIDE" and inside:
            ahead = 0.0
        else:
            ahead = float(depth)
        return max(0.0, dvol - ahead)

    # ── the run ───────────────────────────────────────────────────────
    def run(self, rows, settlement):
        n = len(rows)
        i = self.i0
        recovery_started = None
        exit_quote = None            # (leg, price) of a resting maker exit

        while True:
            i += 1
            if i >= n:
                self._finish_open(rows[-1], n - 1)
                return self
            prev, row = rows[i - 1], rows[i]

            # ── expiry / settlement ───────────────────────────────────
            if row["state"] == tape.EXPIRED:
                self._finish_expired(row, i, settlement)
                return self

            dvol = 0.0
            if (row["shares_traded"] is not None
                    and prev["shares_traded"] is not None):
                dvol = max(0.0, row["shares_traded"] - prev["shares_traded"])
            print_px = row["last_trade_px"]

            age = i - self.i0
            cancelled = (self.cancel_effective_from is not None
                         and i >= self.cancel_effective_from)

            # ── entry fills (the two resting legs) ────────────────────
            if not cancelled and (self.open_yes > 0 or self.open_no > 0):
                got = self._available("YES", prev, row, dvol, print_px)
                take = min(self.open_yes, got)
                if take > 0:
                    self._fill("YES", self.pb, take, i, row, True, "ENTRY")
                    self.open_yes -= take
                    if self.cancel_requested_at is not None:
                        self.race_fills += 1
                got = self._available("NO", prev, row, dvol, print_px)
                take = min(self.open_no, got)
                if take > 0:
                    self._fill("NO", 1.0 - self.po, take, i, row, True,
                               "ENTRY")
                    self.open_no -= take
                    if self.cancel_requested_at is not None:
                        self.race_fills += 1

            # ── both legs done: a matched pair, settles at 1 ──────────
            if (self.open_yes <= 1e-9 and self.open_no <= 1e-9
                    and recovery_started is None
                    and min(self.yes, self.no) >= self.size - 1e-9):
                self._finish_paired(row, i, settlement)
                return self

            # ── the quoting horizon: request the cancel ───────────────
            if (age >= QUOTE_HORIZON and self.cancel_requested_at is None
                    and (self.open_yes > 0 or self.open_no > 0)):
                self.cancel_requested_at = i
                # RACE: effective only from the NEXT observation, so a
                # print in this interval still fills us.
                self.cancel_effective_from = i + 1
                continue
            if age >= QUOTE_HORIZON and self.cancel_requested_at is None:
                self.cancel_requested_at = i
                self.cancel_effective_from = i

            if not cancelled:
                continue

            # ── RECOVERY ──────────────────────────────────────────────
            matched = min(self.yes, self.no)
            excess_yes = self.yes - matched
            excess_no = self.no - matched
            if excess_yes <= 1e-9 and excess_no <= 1e-9:
                if matched <= 0:
                    self._finish_never_filled(row, i)
                else:
                    self._finish_paired(row, i, settlement)
                return self

            if row["bid"] is None or row["ask"] is None:
                continue

            if recovery_started is None:
                recovery_started = i
                book = ev.Book(slug=self.slug, bid=row["bid"],
                               ask=row["ask"], tick=self.tick)
                if excess_yes > 0:
                    q = ev.incremental_ev(
                        "QUOTE_OFFER", book, ev.Inventory(), contracts=1.0,
                        as_fill=0.0, p_fill=1.0,
                        rebate_eligible=self.rebates_on)
                    exit_quote = ("YES", q.terms["quote_price"])
                else:
                    q = ev.incremental_ev(
                        "QUOTE_BID", book, ev.Inventory(), contracts=1.0,
                        as_fill=0.0, p_fill=1.0,
                        rebate_eligible=self.rebates_on)
                    # selling NO at (1 - engine bid) means someone buys
                    # YES at the engine's bid quote
                    exit_quote = ("NO", 1.0 - q.terms["quote_price"])
                continue

            # maker exit resting
            leg, xpx = exit_quote
            want = excess_yes if leg == "YES" else excess_no
            if leg == "YES":
                hit = (print_px is not None and print_px >= xpx - 1e-9)
                inside = row["ask"] is not None and xpx < row["ask"] - 1e-9
                depth = row["ask_depth"] or 0
            else:
                yes_px = 1.0 - xpx
                hit = (print_px is not None and print_px <= yes_px + 1e-9)
                inside = row["bid"] is not None and yes_px > row["bid"] + 1e-9
                depth = row["bid_depth"] or 0
            if hit and dvol > 0:
                ahead = 0.0 if (self.queue_model == "QUEUE_FRONT_IF_INSIDE"
                                and inside) else float(depth)
                take = min(want, max(0.0, dvol - ahead))
                if take > 0:
                    self._sell(leg, xpx, take, i, row, True, "EXIT_MAKER")
                    matched = min(self.yes, self.no)
                    if (self.yes - matched) <= 1e-9 and \
                            (self.no - matched) <= 1e-9:
                        if matched > 0:
                            self._finish_paired(row, i, settlement,
                                                FLAT_EXITED_MAKER)
                        else:
                            self._finish_flat(row, i, FLAT_EXITED_MAKER)
                        return self
                    continue

            if i - recovery_started >= RECOVERY_WAIT:
                # cross out as a taker
                matched = min(self.yes, self.no)
                ey, en = self.yes - matched, self.no - matched
                if ey > 0:
                    self._sell("YES", row["bid"], ey, i, row, False,
                               "EXIT_TAKER")
                if en > 0:
                    self._sell("NO", 1.0 - row["ask"], en, i, row, False,
                               "EXIT_TAKER")
                if matched > 0:
                    self._finish_paired(row, i, settlement,
                                        FLAT_EXITED_TAKER)
                else:
                    self._finish_flat(row, i, FLAT_EXITED_TAKER)
                return self

    # ── endings ───────────────────────────────────────────────────────
    def _stamp(self, row, i, status):
        self.status, self.end_i, self.end_t = status, i, row["t_iso"]

    def _finish_paired(self, row, i, settlement, status=FLAT_PAIRED):
        """A matched pair remains. It pays EXACTLY 1 per pair."""
        self._stamp(row, i, status)
        matched = min(self.yes, self.no)
        self.residual_value = matched * 1.0
        self.residual_basis = ("MATCHED_PAIR_PAYS_1 -- certain, but not "
                               "cash until settlement")
        self.settlement = settlement

    def _finish_flat(self, row, i, status):
        self._stamp(row, i, status)
        self.residual_value = 0.0
        self.residual_basis = "FLAT"

    def _finish_never_filled(self, row, i):
        self._stamp(row, i, NEVER_FILLED)
        self.residual_value = 0.0
        self.residual_basis = "FLAT -- neither leg filled"

    def _finish_expired(self, row, i, settlement):
        self._stamp(row, i, CARRIED_SETTLED)
        self.settlement = settlement
        if settlement == tape.SETTLEMENT_NOT_OBSERVED or settlement is None:
            self._stamp(row, i, CARRIED_OPEN)
            self.residual_value = 0.0
            self.residual_basis = ("EXPIRED BUT OUTCOME NOT PUBLISHED IN "
                                   "THE CAPTURE -- residual UNVALUED")
            self.notes.append("settlement unobserved; residual carried at 0 "
                              "and flagged, NOT marked")
            return
        s = float(settlement)
        self.residual_value = self.yes * s + self.no * (1.0 - s)
        self.residual_basis = ("OBSERVED SETTLEMENT %.4f -- yes*%0.4f + "
                               "no*%0.4f" % (s, s, 1.0 - s))

    def _finish_open(self, row, i):
        self._stamp(row, i, CARRIED_OPEN)
        matched = min(self.yes, self.no)
        ey, en = self.yes - matched, self.no - matched
        v = matched * 1.0
        basis = ["matched pair %g -> 1.0 each" % matched] if matched else []
        if row["bid"] is not None and ey > 0:
            v += row["bid"] * ey + _taker_fee(row["bid"], ey)
            basis.append("excess YES %g marked at the BID %.4f net of the "
                         "taker fee" % (ey, row["bid"]))
        elif ey > 0:
            basis.append("excess YES %g UNMARKED -- no bid" % ey)
        if row["ask"] is not None and en > 0:
            px = 1.0 - row["ask"]
            v += px * en + _taker_fee(px, en)
            basis.append("excess NO %g marked at 1-ASK %.4f net of the taker "
                         "fee" % (en, px))
        elif en > 0:
            basis.append("excess NO %g UNMARKED -- no ask" % en)
        self.residual_value = v
        self.residual_basis = ("MARKED AT THE TOUCH WE COULD HIT: "
                               + "; ".join(basis) if basis else "FLAT")

    # ── the result ────────────────────────────────────────────────────
    def result(self):
        matched = min(self.yes, self.no)
        filled_col, with_resting = self.collateral_now()
        settled = self.status == CARRIED_SETTLED
        return {
            "slug": self.slug, "event": self.event,
            "t0": self.t0, "t_end": self.end_t,
            "observations": (self.end_i - self.i0) if self.end_i else 0,
            "status": self.status,
            "queue_model": self.queue_model,
            "rebates": "PUBLISHED" if self.rebates_on else "EXCLUDED",
            "size": self.size,
            "quote_bid": self.pb, "quote_offer": self.po,
            "entry_book": self.entry_book,

            # the three quantities kept APART, as the directive requires
            "realised_cash": round(self.cash, 6),
            "residual_contracts": {"yes": round(self.yes, 4),
                                   "no": round(self.no, 4),
                                   "matched_pairs": round(matched, 4),
                                   "net_directional": round(
                                       self.yes - self.no, 4)},
            "residual_value": round(self.residual_value, 6),
            "residual_basis": self.residual_basis,
            "residual_is_cash": self.status in (FLAT_EXITED_MAKER,
                                                FLAT_EXITED_TAKER,
                                                NEVER_FILLED),
            "residual_is_settled": settled,
            "total_if_residual_realises": round(
                self.cash + self.residual_value, 6),

            "fills": len(self.fills),
            "entry_fills": sum(1 for f in self.fills if f["tag"] == "ENTRY"),
            "fill_sizes": [f["n"] for f in self.fills],
            "rebates_received": round(self.rebates_paid, 6),
            "taker_fees_paid": round(self.taker_fees_paid, 6),
            "cancel_requested_at_obs": (
                None if self.cancel_requested_at is None
                else self.cancel_requested_at - self.i0),
            "fills_after_cancel_request": self.race_fills,
            "collateral_filled_only": round(filled_col, 4),
            "collateral_incl_resting": round(with_resting, 4),
            "settlement": self.settlement,
            "notes": self.notes,
        }


def run_market(slug, rows, size, rebates_on, queue_model,
               max_episodes=None):
    """Non-overlapping episodes across one market's whole tape."""
    settlement, _ = tape.settlement_label(rows)
    tick = tape.market_tick(rows)
    event = tape.event_of(slug)
    out, i, n = [], 0, len(rows)
    while i < n:
        r = rows[i]
        if (r["state"] != tape.OPEN or r["bid"] is None or r["ask"] is None
                or (r["ask"] - r["bid"]) < tick - 1e-9):
            i += 1
            continue
        ep = Episode(slug, event, i, r, size, tick, rebates_on, queue_model)
        ep.run(rows, settlement)
        out.append(ep.result())
        i = max(ep.end_i or i, i) + COOLDOWN + 1
        if max_episodes and len(out) >= max_episodes:
            break
    return out


def run_all(size=100.0, rebates_on=True,
            queue_model="QUEUE_FRONT_IF_INSIDE", max_episodes=None):
    by_slug, _ = tape.load_tape()
    eps = []
    for slug, rows in sorted(by_slug.items()):
        eps.extend(run_market(slug, rows, size, rebates_on, queue_model,
                              max_episodes))
    return eps


if __name__ == "__main__":
    import collections
    import statistics as st

    print(__doc__.split("Run:")[0].strip()[:0] or "", end="")
    print("=" * 74)
    print("EPISODE ACCOUNTING -- every position carried, nothing dropped")
    print("=" * 74)
    for rebates_on in (True, False):
        for qm in QUEUE_MODELS:
            eps = run_all(size=100.0, rebates_on=rebates_on, queue_model=qm)
            tot = [e["total_if_residual_realises"] for e in eps]
            real = [e["realised_cash"] for e in eps]
            byst = collections.Counter(e["status"] for e in eps)
            carried = [e for e in eps
                       if e["residual_contracts"]["net_directional"] != 0]
            print()
            print("rebates=%-9s queue=%-22s episodes=%d" % (
                "PUBLISHED" if rebates_on else "EXCLUDED", qm, len(eps)))
            print("  statuses: %s" % dict(byst))
            print("  markets=%d  events=%d" % (
                len({e["slug"] for e in eps}), len({e["event"] for e in eps})))
            if tot:
                print("  TOTAL (realised + residual): sum %+0.2f  "
                      "median %+0.4f  mean %+0.4f  positive %d/%d" % (
                          sum(tot), st.median(tot), st.fmean(tot),
                          sum(1 for x in tot if x > 0), len(tot)))
                print("  realised cash only:          sum %+0.2f  "
                      "median %+0.4f" % (sum(real), st.median(real)))
                print("  episodes ending with UNMATCHED inventory: %d" %
                      len(carried))
