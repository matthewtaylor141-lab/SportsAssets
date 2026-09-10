# The two-sided RN1 mirror — design

**Status:** design, for owner decision. Nothing here is built or armed.
Trading remains paused (`mirror_live=false`, mode `exits`).

---

## 1. The venue fact everything rests on

Polymarket US keeps **one signed `netPosition` per market slug**. The four order
intents collapse to two wire sides — `pmus._norm_order` says it outright: *"a BUY_SHORT
reads ORDER_SIDE_SELL, short-truth 6/6."*

So **you cannot hold both sides of a market here.** Buying the complement of something you
hold is not a second position; it is a sale of the first.

That sounds like it kills two-sided copying. It does not. Work the money through:

| | RN1 (global venue) | Us (Polymarket US) |
|---|---|---|
| Leg A | buys YES at 0.48 | post-only **buy** at 0.48 |
| Leg B | buys NO at 0.51 | buying NO at 0.51 **is** selling YES at 0.49 → post-only **sell** at 0.49 |
| Position after | +1 YES, +1 NO | flat |
| Cash | −0.99, receives 1.00 at settlement | −0.48, +0.49 |
| **Profit** | **+0.01** | **+0.01** |

**Identical.** And ours is *better* in two ways: the profit is realised at the second fill
instead of at settlement, and the capital comes straight back. That is the merge engine the
case study describes — 57,560 merges, ~$179M, median lag near zero hours — except the venue
does it for us automatically instead of us having to call a merge.

**So: his pair edge is fully copyable. What is not copyable is holding it.** We copy the
*trade*, not the inventory.

## 2. What this means for E38, which I landed today

E38's `our_pair` sets `long_target == other_target == pair_target` — "one share of each leg."
Read literally on this venue that nets to zero, and the naming had me worried it was wrong.

It isn't. Execute it literally and you get: buy one long at `bid_long`, buy one other at
`bid_other` — and buying the other at `bid_other` *is* selling the long at `ask_long`. So the
two orders are **a post-only buy at the bid and a post-only sell at the ask.** That is a
two-sided market-making quote, and its economics — E38's `rest_pair = 1 − spread` — are exactly
right.

The orders are correct. The word "pair" is misleading: at this venue a pair is a **round trip
in time**, not two positions held at once. I will rename it rather than rebuild it.

## 3. Your standing-order rule — it closes a real hole

You asked for the standing order to be priced off **the actual purchase price of what we
hold, including fees, so the pair stays under $1**. That is right, and it is the one thing
E38 does not do.

E38 rests its sell at the touch (`ask_long`). If our buy filled and the market then moved
down, the touch can sit **below our basis** — and E38 would happily sell there, booking a
locked loss. Your rule forbids that.

### The arithmetic

Hold shares at true basis `c` (per share, fees included). Let `q` be the price of the
complement we would buy to close.

    profit per share  =  (1 − q) − c        →   profitable iff  c + q < 1.00

**Your "keep the pair under $1" and "sell above what we paid" are the same inequality.** You
arrived at the correct control from the pair side.

With a margin `m` and the per-contract fee `f(q)`:

    c + q + f(q)  ≤  1 − m

The venue's fee measured today is `f(p) ≈ 0.06 · p · (1 − p)` on **taking** fills, and the one
position built entirely from rests paid nothing. Solving for the ceiling on `q`:

    0.06·q² − 1.06·q + (1 − m − c) = 0

    q_max = [ 1.06 − √( 1.1236 − 0.24·(1 − m − c) ) ] / 0.12

*Worked example.* Basis `c = 0.45`, margin `m = 0.02`:
`q_max = (1.06 − √0.9964)/0.12 = 0.5150`. Check: `0.45 + 0.5150 + 0.06·0.5150·0.4850 = 0.9800`. ✓

In long terms the standing sell rests at `1 − q_max = 0.4850`, i.e. **basis + margin + the fee
we would owe.** If we are confident the rest stays a maker, `f = 0` and it is simply
`c + m`. The quadratic is the safe version — it prices the sell as if the venue might fill it
as a taker, which it has done before (order 153, `maker=false`).

### Where `c` must come from

**The venue, not our ledger.** The position object carries `cost`, `baseCost` and `fees`, and
`cost = baseCost + fees` holds exactly — so `c = cost / netPosition` is the true all-in basis.
Our own ledger has drifted from the venue repeatedly (books 1333, 534, 863). The basis that
gates a money decision must be the venue's own number, re-read, never our arithmetic.

Note `pmus._commission_fields:2492` reads three key names that have never held a value — fee
capture is currently broken. That gets fixed as part of this, because the rule depends on it.

## 4. The design

**Unit of copy: his LEG, never his net.** His net is the residue after his two sides cancel,
and that residue is the losing part of him — 58.6% of his cost is matched pairs, his matched
book is the profit, his directional residual is negative. Copying the net is the three-week
error.

**Size: `min(his leg A, his leg B) × ratio`, floored to whole shares.** Matched shares only.
His unmatched residual is recorded and never targeted.

**Quote: two post-only rests, always.**
- Buy side at the bid, capped at `his leg-A price` (never chase above him).
- Sell side at `max(ask_long, c + m + f)` — **your rule as a hard floor.** The sell never rests
  below basis + margin, whatever the touch says.

**Never cross.** E31 already made every order a post-only rest and retired every take path by
code default. That stays: taking both ways costs ~2.1¢ on a 0.32 contract and his whole pair
edge is ~1¢. One crossed leg destroys four pairs' profit.

**Unmatched cap.** If the buy fills and the sell does not, we hold directional inventory —
exactly what has been killing us. Hard cap on unmatched dollars per market and across the
book, and when it is hit we stop quoting new markets. We do **not** stop out: the case study is
explicit that crossing the spread to exit a residual costs more than carrying it, and our own
band study measured that cost at ~22% per trade.

## 5. What raises the fill rate

Measured today: resting buy **26.5%**, resting sell **30.7%**. If the two legs were independent
the joint rate would be ~7%, and at 7% this business does not exist. Four levers, in order of
expected effect:

1. **Quote both sides from his first fill — do not wait for his second leg.** He is the signal
   that a market is worth quoting; after that we are making the market, not following him.
   This is the single biggest lever and it is also the biggest change in character. **Decision
   for you.**
2. **Requote on every touch move.** Already built (E31). A rest left behind the touch is a rest
   that never fills.
3. **Accept partial fills** and re-quote the remainder rather than cancel-replace whole.
4. **Widen coverage.** Every market we cannot map is a market we cannot quote — and the
   `_us_slug_candidates` defect (no totals/spreads grammar; soccer spreads misrouted to the
   moneyline) is live money-path breakage that caps the universe today.

## 6. What I need you to decide

1. **Do we quote both sides from his first fill, or wait for his second leg?**
   Waiting is a truer mirror and fills far less. Quoting immediately is market making with him
   as the market selector, and is how the fill rate gets fixed.
2. **When our basis is too high to complete under $1, what happens?**
   (a) hold unmatched and keep quoting the sell at the floor — never a locked loss, but
   inventory builds; (b) follow him down and book the loss — tracks him, loses money.
   Your instruction reads as (a); I want it explicit.
3. **Margin `m`.** The case study's entry gate is ~1.5% net. The median US spread today is 1¢.
   `m = 0.01` is aggressive, `m = 0.02` safer and fills less.
4. **Unmatched caps** — per market and global. Case study starting point is ~$5K per market.

## 7. Order of work

1. Deploy the E38 shadow (already landed, sends nothing) to get the **joint fill rate**. That
   number decides whether any of this is worth building. Still waiting on your word.
2. Fix fee capture (`_commission_fields`) so basis is real.
3. Build the basis floor on the sell — your rule — in shadow first.
4. Fix `_us_slug_candidates` (live defect, caps the universe).
5. Only then arm anything, and only after the shadow says the joint fill rate clears
   break-even.
