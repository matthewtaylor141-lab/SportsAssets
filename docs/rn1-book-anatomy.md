# RN1's book, as the desk's forensic study reconstructs it (2026-09-10)

Source: "RN1 -- Anatomy of a $1.19 Billion Polymarket Sports Book" (BettorToken
Holdings, September 2026; 31 pages, 4,615,349 on-chain fills, 9 Jul 2025 to
10 Sep 2026). Page numbers below are the study's. This note keeps the facts the
mirror's lanes must design against; it decides nothing. The standing mandate
(the proportional directional mirror, docs/mirror-coverage.md) is the owner's.

## What the account is (pp. 3-5, 21-23)

- A two-sided spread-capture book, not a forecaster. 71% of every dollar bought
  BOTH outcomes of the same market; a matched YES+NO pair cost $0.969 on
  average and pays $1.00. Matched pairs earned +$12.90M (+3.20% on $403.37M);
  the unmatched DIRECTIONAL RESIDUAL lost -$793K (-0.48% on $163.92M).
  "The account is 107% arbitrage and -7% forecasting" (p. 21).
- Bid-only: 4,615,101 buys, 248 sells (0.005%). It never sells to exit; it
  merges completed pairs into cash (57,560 merges, $178.75M, median lag ~0 h)
  or waits for settlement (p. 25).
- Tiny clips at enormous frequency: median fill $8.69, p90 $290, 10,784 fills
  per active day; median $538 per market, p90 $10K; 338 markets per day
  (p. 5). Volume-weighted entry 0.481 on both sides: symmetric quoting near
  the mid, filled by takers (p. 25).
- Win rate 52.9% of markets, median win $148, median loss -$125: designed to be
  indifferent to who wins (p. 3).

## The residual by sport -- what a one-sided copy inherits (p. 13)

| Sport | $ deployed | Matched % | Matched ROI | Directional ROI |
|---|---|---|---|---|
| Soccer | $260.06M | 64% | +4.08% | +1.17% |
| Tennis ATP | $87.12M | 76% | +1.42% | +1.85% |
| Tennis WTA | $59.39M | 81% | +0.93% | +2.57% |
| Esports | $39.47M | 73% | +2.97% | -1.49% |
| NBA | $34.20M | 89% | +5.19% | -31.46% |
| MLB | $40.62M | 74% | +2.90% | -4.64% |
| NCAAF | $7.84M | 79% | +6.19% | -5.02% |
| NCAAB | $7.79M | 77% | +2.91% | +4.99% |
| NHL | $6.74M | 74% | +6.29% | -4.38% |
| NFL | $8.74M | 87% | +6.23% | -22.42% |
| Tennis ITF | $13.74M | 52% | -0.14% | -6.12% |

Market types (p. 19): moneyline +1.76% on $387.57M (68% of cost); totals
+2.92%; draw +3.61%; spread +3.95%; BTTS +7.24%; 1H totals -0.06%; 1H leader
-3.12% (the Bayern halftime market of 5 Sep 2026, -$109K on $110K, is the
third-worst market ever).

Size (p. 24): $1K-$10K per market earns +3.3-3.7% at a 58% win rate;
$100K-$250K is the only losing band (-$1.04M, -1.73%, 48%). Every one of the
fifteen worst markets is a one-sided position on a favourite (or a draw) that
failed, at $100K+ (p. 26).

## Time (pp. 8, 22, 31)

- ROI on deployed fell from +6.63% (Aug 2025) to +1.22% (Aug 2026) and -0.93%
  in 1-10 Sep 2026, the first negative month; the pair discount shrank from
  6-8c to 1-2c; the matched share of cost fell from 79% (Jan 2026) to 51%
  (Sep 2026).
- Monthly directional P&L (App. B): +$504K Jan, -$161K Feb, +$385K Mar,
  -$369K Apr, -$245K May, -$423K Jun, +$251K Jul, +$141K Aug, -$411K Sep
  (1-10). Negative in nine of fifteen months.
- The open drawdown, -$312K from the 3 Sep 2026 peak, is the deepest on
  record; seven consecutive losing days (p. 7).

## What this says about the mirror as it stands (pp. 28-30)

The study's own words (p. 28): "mirroring RN1 fills without simultaneously
holding the opposite leg is equivalent to holding RN1's directional residual,
which has returned -0.48% lifetime and -4.7% in September 2026." And (p. 29):
"Copying with delay: the pair discount is captured at fill time; a lagged
copier buys after the price has moved and ends up paying >$1.00 per pair."

What follows for the lanes, without changing the mandate:

1. His NET per market (what the mirror reads and sizes from) is the residual of
   a market-making inventory, not a view. A net that flips sign is his book
   rebalancing; every flip we follow is paid in spread. The drift / flip churn
   the PNL lanes fought (Martinez 529/534, Zheng/Rybakina 1177) is structural.
2. He never sells: our exit rules keyed on his SELLs fire on 0.005% of his
   fills. His exits are merges (a YES+NO pair to cash), which a fills-based
   net reading never sees; his net moves only when he buys the other side.
3. Taking at his price inside a band pays the spread he was paid; the study's
   arithmetic says the one-sided copy has no positive expectation at today's
   1-2c pair discount unless the sport's residual is positive (soccer, ATP,
   WTA, NCAAB) -- and NFL / NBA / MLB / esports residuals are negative.
4. Sizing: our 10% of his per-market position lands mostly in his $1K-$10K
   band, the band that earns; the $2,500 per-order clip keeps us out of the
   band that loses.

The construction the study says captures the +3.2% is a methodology mirror
(hold what he holds on BOTH sides and merge) or a native two-sided quoter in
second-tier European soccer at $1-10K per market with a continuous merge loop
(pp. 29-30). On the US venue a long and a short on the same contract net to
cash at once, so the merge is the venue's own netting. Whether to build it is
the owner's decision; nothing here builds it.

## Measured on our own money

The read-only presets `mirror-by-league` (our books' settled P&L, stake, ROI
and win/loss by league, by contract prefix and in total over 7 days and the
book's life) and `his-matched` (his matched share of cost, pair cost and
residual by league over 7 days, from the fills we hold) are the study's two
metrics on the desk's own data: compare `mirror-by-league`'s ROI by league
against the residual column above.
