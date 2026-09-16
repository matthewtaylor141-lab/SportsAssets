# MANAGEMENT EV EXPLAINER

Plain English. No jargon that isn't defined. Nothing here is a profitability
claim, and the last section says what we still cannot claim at all.

*Nothing described here is switched on. No live trading, no capital, no orders.*

---

**1. Are we still using the whales?**

Yes — but as *evidence*, not as instructions. We no longer copy their trades.
We use what their history proves about which economic mechanisms worked, and we
use it as a starting assumption that our own system is expected to grow out of.

**2. What exactly did we stop copying?**

We stopped copying their individual fills and their timing. The honest reason:
we never actually saw their strategy. Our data shows the trades that *happened*
— it does not show the orders they placed that never filled, the orders they
cancelled, or the opportunities they looked at and declined. A fill is the
meeting point of their intention and somebody else's. Millions of fills do not
reveal one decision they made. So "copy their policy" was never available to
us; only "copy their fills" was, and that is what we stopped.

**3. What exactly are we preserving from RN1?**

RN1 is the reference for passive two-sided market making: quoting both sides,
completing the position, and recycling the capital. The specific thing RN1 did
better than anyone else in our data is **discipline about what it paid for a
completed position**. Across all six price bands, RN1 never paid more than
$0.998 for a pair that redeems $1.00. Every other account, in its expensive
bands, paid *more than a dollar* for a dollar. That is the habit we are keeping.

**4. What exactly are we preserving from Ferrari?**

Mostly a warning, and it is the most valuable single number in the study.
In one price band Ferrari's completion mechanism earned **+$3.84M** — and its
leftover, unfinished positions lost **−$4.44M**, for a net of **−$0.56M**. The
mechanism worked and the leftovers ate it.

So from Ferrari we preserve the mechanism and we permanently separate the two
ledgers: money made from completing positions, and money made or lost on
leftovers, are never added together into one headline. A single number would
have hidden this completely.

**5. What exactly are we preserving from HomeRunHazard?**

Its role is directional pricing — betting a contract is simply mispriced — and,
just as importantly, it serves as our negative control. It stops us assuming
that "completing a position" is automatically profitable. HomeRunHazard's
completion economics were negative in its expensive bands too. Directional value
and completion value are different things and are never mixed.

**6. What exactly are we preserving from SwissTony?**

Live, in-play pricing and its exit behaviour — how it hedged and closed
positions rather than only waiting for settlement. We treat its history as
reference evidence about mechanics, not as proof of a live edge today. It is
also flagged out of our consensus figures on an unresolved data discrepancy.

**7. Where does BETTOR's Day-1 EV come from?**

Four sources, kept separate and labelled on every decision:

- **whale history** — coarse prior on which mechanisms pay, and where
- **current market measurement** — the live book, spread, depth, fees
- **independent fair value** — our own probability estimate, from sources other
  than the market we are trading
- **public research methodology** — the *shape* of the calculation, not numbers

**8. What portion is directly supported by whale history?**

Less than the volume of data suggests, and we would rather say so now. The
retained whale files are *summaries*, not trade-by-trade records. They support
exactly two breakdowns: by account and price band, and by account and how long
a position sat unfinished. There is **no** breakdown by sport, league, market
type, or time to kickoff.

And the support is on **direction, not size**. We can say "the whales' evidence
points this way in this price band." We cannot say "and it is worth 2.3 cents."

**9. What portion is current-market measurement?**

Everything about execution: the spread, the depth, the fees, the queue, whether
our order is likely to fill. This is measured live and owes nothing to the
whales.

**10. What portion is independent BETTOR fair value?**

The probability estimate itself. Critically, **we refuse to use the price of the
market we are trading as evidence that the market is mispriced.** That would be
circular — the market would always be correct by construction and no edge could
ever exist. This refusal is enforced in code, not just policy.

**11. What portion is public-research-derived methodology?**

The structure: how to score a probability forecast honestly, how to avoid
counting the same cost twice, how to handle small samples without inventing
confidence, how to validate without fooling ourselves. Research supplies shapes.
It never supplies a number that goes into a trade.

**12. How does BETTOR improve on each whale?**

- **On RN1** — same mechanism, better market selection and inventory control,
  and an explicit cost for capital tied up.
- **On Ferrari** — the failure is a leftover-inventory problem, so leftovers get
  their own cap, their own exit logic and their own ledger.
- **On HomeRunHazard** — directional value and completion value are never summed.
- **On SwissTony** — entries, hedges, closes, merges and settlements are
  labelled as different actions rather than all counted as "trades."

One honest caveat: we can claim an **architectural** improvement. We cannot
claim a **historical P&L** improvement, because we do not have the prices that
were actually available to them at the moments they chose not to act. Where that
is missing, we write `COUNTERFACTUAL_PNL_IMPROVEMENT = NOT_IDENTIFIED` rather
than inventing an exit price that would have saved them.

**13. What happens when BETTOR sees a market unlike anything the whales traded?**

It goes to shadow — logged and measured, no capital — unless it qualifies on its
own as a structural opportunity whose edge does not depend on an unproven
prediction. Given how coarse the whale data actually is, **a lot of markets will
fall here**, and that is the correct, unexciting answer rather than a failure.

**14. How does BETTOR gradually earn autonomy?**

Each component — how often positions complete, what closing costs, how leftovers
perform — carries a weight between whale history and BETTOR's own observations.
As our own evidence accumulates on a component, its weight shifts to us
automatically. Two safeguards: the size of the whale archive gives it **no**
extra voting power, so it cannot outvote us forever; and if we declare that
market conditions have changed, the old evidence is dropped rather than faded,
because it is evidence about a different world.

**15. What would cause BETTOR to refuse to trade despite whale support?**

Any of: our own expected value is negative; the uncertainty in our edge is wide
enough that the conservative estimate no longer clears the price; any option we
could take is unpriced (the system declines rather than guessing); the market
looks information-toxic right now; the fee schedule is ambiguous; or we are
already at our exposure limit for that **event** — not that market, that event,
because contracts on one game are one risk.

**16. What evidence would management need before believing native BETTOR EV is
beating the whale reference?**

All of the following, and not fewer:

- **Out-of-sample**, on time-ordered data, with all contracts from one event
  kept together — never a result chosen after the fact.
- **A final holdout scored exactly once.**
- **Five separate numbers**, not one: forecast accuracy, edge before execution,
  execution quality, incentive income, and total P&L. A model can be rejected
  for weak statistical evidence *even while showing a profit* — that rejection
  must remain possible.
- **A multiple-testing correction**, with the full list of things we tried
  declared beforehand.
- **Enough independent events** — not fills. One decision sliced into eighteen
  fills is one observation, and in this data that ratio is roughly 18 to 1.

Until then the right description is: *BETTOR is a whale-anchored system with an
independent EV engine under construction* — not *BETTOR has its own alpha.*
