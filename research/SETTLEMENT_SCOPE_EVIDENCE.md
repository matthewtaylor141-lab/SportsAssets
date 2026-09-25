# Settlement scope: what is actually missing, and two retractions

Measured 2026-09-25 against the real captured payload, not invented prose.

---

## 1 · Two things I claimed that were wrong. Both retracted.

### Retracted: "the venue does not publish this"

I tested **eight guessed URL paths**. Five returned 404, which proves those
five paths are wrong — not that no document exists at a path I did not
guess. Worse, `docs.polymarket.us/` returned HTTP 200 with only **406 words
of extractable prose**, which means the documentation site is *itself*
client-rendered: my extractor could not read its content either, so I never
saw its navigation, never enumerated its pages, and never followed a
sitemap.

The defensible statement is narrower: **no per-condition settlement
document was found at eight guessed paths, and the documentation site
renders client-side so its contents were not readable by plain HTTP
retrieval.** Whether one exists is still open. Supported routes are being
tried (§4).

### Retracted: "one document clears 464 candidates"

Wrong on two counts.

1. **The venue side is only half the comparison.** `compare` needs *both*
   sides per condition. Even a complete venue document leaves any condition
   the book is silent on unresolved.
2. **Settlement scope is one stage of eight.** `NO_ACTION_HAS_POSITIVE_NET_EDGE`
   (445 of 464) and `EXECUTION_ESTIMATE_NOT_IDENTIFIED` (442) are
   independent and would still refuse most candidates. Clearing the void
   rule clears **one gate**, not 464 candidates. What it would do is let
   candidates reach the economics — where most would then be refused on
   their merits, which is the correct outcome and not a win.

### Also corrected: "380 characters, stating one condition"

The real `description` on the captured contract is **183 characters** and
states **three** conditions' worth of rule, two of which the comparison
accepts. I quoted a length from a different market and understated what the
venue does say.

---

## 2 · The actual per-condition state

`aec-mlb-az-col-2026-09-24`, the venue's own `description` verbatim:

> *"This market settles on the final result of the game, including any
> extra innings. If the game is abandoned or postponed and never completed
> the market is void and stakes are returned."*

`assetPriceTerms: None`. Run through `compare_prose` with
`phase=REGULAR_SEASON`, `game_format=STANDARD_NINE_INNING`:

| Condition | Verdict | Book | Venue |
|---|---|---|---|
| `DECIDED_AFTER_REGULATION` | **MATCH** | pays on final score | pays on final score |
| `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` | **MATCH** | returns stake | returns stake |
| `COMPLETED_IN_REGULATION` | venue silent | pays on final score | — |
| `CALLED_AND_GRADED_WITHOUT_RESUMPTION_AFTER_THE_MINIMUM` | venue silent | last completed period, except a bottom-half home lead | — |
| `STOPPED_BEFORE_THE_MINIMUM` | venue silent | returns stake | — |
| `SUSPENDED_AND_RESUMED_WITHIN_THE_PUBLISHED_WINDOW` | venue silent | pays on final score | — |
| `SUSPENDED_TO_RESUME_BEYOND_THE_PUBLISHED_WINDOW` | venue silent | pays on last completed period | — |

**`mismatched_conditions: []` — nothing conflicts.** Verdict is `UNKNOWN`
purely from silence, and the book side is complete on all seven.

So the gap is **five conditions where the venue states no rule**, and they
are not arbitrary: conditions 2–5 are all *"the game started and did not
finish normally"* — exactly where a money line's action turns, and exactly
what a 183-character blurb does not address.

### One of the five is probably a reader gap, not a venue gap

`COMPLETED_IN_REGULATION` reads venue-silent, yet the sentence *"settles on
the final result of the game, including any extra innings"* plainly covers
a game that finished in regulation. `read_terms` attributed that sentence
to `DECIDED_AFTER_REGULATION` only, because "extra innings" is the phrase
it matched.

**I have not changed the reader.** Attributing one sentence to two
conditions is a widening of settlement compatibility, and that needs to be
a reviewed decision rather than something I slip in while fixing something
else. Recorded here as a candidate with its evidence; it would reduce the
missing set from five to four and does not on its own change the verdict.

---

## 3 · Exact source attempts

| Source | Asked | Result |
|---|---|---|
| `www.pinnacle.com/en/future/betting-rules` | 2026-09-24T20:30:22Z | **200**, 629 lines, per-condition baseball rules captured with citations |
| `support.pinnacle.com/.../How-bets-are-graded` | 2026-09-24T20:30:23Z, 2026-09-25T14:42:46Z | **403** both times; nothing taken from it |
| `docs.polymarket.us/settlement` | 2026-09-25T14:42:46Z | **404** (117 KB doc-shell body) |
| `docs.polymarket.us/rules` | same | **404** |
| `docs.polymarket.us/market-rules` | same | **404** |
| `docs.polymarket.us/resolution` | same | **404** |
| `docs.polymarket.us/sports-rules` | same | **404** |
| `docs.polymarket.us/` | 2026-09-25T14:42:49Z | **200**, 406 words, 0 settlement keywords — client-rendered, contents unread |
| `polymarket.us/rules` | 2026-09-25T14:42:49Z | **200**, 236 KB HTML / 80 words — client-rendered shell |
| `polymarket.us/terms` | 2026-09-25T14:42:50Z | **200**, same 80 words |
| contract `description` field | continuously, per cycle | **200**, 183 chars, 2 conditions accepted |
| contract `assetPriceTerms` field | same | `None` on every contract read |

Recorded in `bettor_venue_settlement.VENUE_TERMS_CAPTURE_ATTEMPTS`.

---

## 4 · Supported documentation routes, in flight

Guessing paths was the wrong method. The supported routes for a
client-rendered documentation site are being tried instead:
`/llms.txt`, `/llms-full.txt`, `/sitemap.xml`, `/openapi.json`,
`/robots.txt`, and a known-good deep path
(`/api-reference/markets/get-market-by-slug`, cited in
`calibration_fees.SCHEDULE`) to confirm which paths the site does serve.
Result appended when it returns.

`assetPriceTerms` is also worth noting as an *existing contract-specific
route the venue defined and left empty*. It is the field on the market
object that would naturally carry per-contract terms, and it is `None` on
every contract read. That is a more specific question to ask than "where
are your rules".

---

## 5 · The question to send, if you want to send it

Two asks, in order of usefulness. Both are answerable without disclosing
anything commercial.

> **Subject: Per-condition settlement terms for MLB moneyline contracts**
>
> We price your MLB moneyline markets against a bookmaker reference and
> reconcile the two on settlement terms condition by condition before
> taking any position. Your contract `description` covers two cases
> clearly — final result including extra innings, and void with stakes
> returned when a game is abandoned or postponed and never completed.
>
> **1. Is there a published document, or an API field, giving the
> settlement rule for a game that starts and does not finish normally?**
> Specifically, for an MLB moneyline contract such as
> `aec-mlb-az-col-2026-09-24` or `aec-mlb-cle-kc-2026-09-25`, how does the
> market settle when:
>
> - the game is **called and made official short of the scheduled nine
>   innings** (does the last completed inning grade it, and is there an
>   exception when the home team leads in a bottom half?);
> - the game is **stopped before the minimum for an official game**;
> - the game is **suspended and resumed later** — and does the answer
>   depend on how long the resumption takes? (Our bookmaker reference uses
>   a 12-hour window for pre-game bets and 30 hours for in-play.)
>
> **2. Is `assetPriceTerms` intended to carry these terms?** It is present
> on the market object and `null` on every contract we have read. If it is
> the intended home for per-contract settlement terms, knowing that is
> enough — we will read it per contract rather than relying on a document.
>
> We are not asking for anything beyond what settles a contract. A link to
> a rules page that renders server-side, or the field name, resolves this.

**Affected contract examples** (both read live today, both currently
refused for exactly this reason):

- `aec-mlb-az-col-2026-09-24` — resolved, `settlement: 1`, Arizona
  `long: true` at price `1`. The captured regression fixture.
- `aec-mlb-cle-kc-2026-09-25` — open and quoting, `bestBidQuote 0.5600`,
  `bestAskQuote 0.445`, `minimumTradeQty 0.01`,
  `orderPriceMinTickSize 0.01`, `feeCoefficient 0.0695`.

---

## 6 · What continues meanwhile

Nothing in the engineering work waits on this answer:

- the **unfunded research-shadow lane** is built and waives only
  `MODEL_TRUST_DRIFT` — settlement compatibility still blocks, so this
  investigation does not gate it and it does not weaken this;
- the **P&L display** is complete and deploying;
- the loop keeps recording refusals every cycle, which is the calibration
  evidence the other gate is waiting for.

If the five conditions stay unanswered, supported-market autonomous entry
stays blocked on settlement scope. That is the truthful state and no part
of this repository asserts otherwise.
