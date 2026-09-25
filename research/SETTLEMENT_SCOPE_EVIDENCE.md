# Settlement scope: what the venue actually says, and what it costs us

Measured 2026-09-25 against the LIVE listing field, read through the same
code production reads. Every earlier version of this document measured a
test fixture; the retractions below say which claims that produced.

**The outcome, first:** the supported market's payoff has a branch that
settles to the contract's last traded price. The only connected probability
source prices the other branch. That is a source/payoff dependency, not a
threshold, and it blocks autonomous entry until one of three named
capabilities exists (§2c).

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

### Retracted again: my own "183 characters, two conditions"

I corrected a live measurement with a fixture, which is backwards.

`bettor_venue_settlement` recorded the listing's `description` at **380
characters, stating one condition**. I replaced that with **183 characters,
stating two** — and measured the 183 from
`backend/tests/fixtures/pmus_settled_market_2026_09_24_az_col.json`. That
file's `description` really is 183 characters. It is a **test fixture**,
hand-abridged, not the venue's payload. The live probe of the same slug on
2026-09-25 reports `description: {"type":"str","len":380}`.

So **the per-condition table in §2 is a measurement of a fixture, not of the
venue**, and every "venue silent" cell in it is unsupported as a statement
about the venue. The five-silence count may be right, wrong, or nearly
right; it is not established either way, and nothing downstream treats it as
established — the entry lane refuses on the live comparison, which is the
only one that gates anything.

**The instrument, not another assertion.**
`bettor_venue_settlement_probe._terms_read` now reads the live `description`
through the same field order `bettor_live_read.read_rules_text` uses, quotes
it **verbatim in full** (its own 6000-character limit, not the 400-character
payload limit that caused this), reports the true length, and runs
`bettor_settlement_terms.read_terms` over it — reporting `stated`,
`not_stated`, `contradicted`, and the sentences the reader saw and could not
use. It reads the **venue side only** and returns no compatibility verdict.
The next entry run prints it for one settled and one open contract.

---

## 2 · What the venue actually says, read live

Three MLB money lines, read 2026-09-25T18:03:33Z through the same field
production reads (`description`, via `bettor_live_read.read_rules_text`).
All three carry the **same four-sentence template**:

> *"This market will settle to the winner of the Arizona Diamondbacks vs
> Colorado Rockies MLB game scheduled for 2026-09-24 at 3:10PM ET. Extra
> innings are included if played. If the game is delayed, postponed, or
> suspended and not rescheduled to a date within two weeks of the
> originally scheduled date, the market will settle to the **last fair
> market price**. Outcome sourced from MLB."*

| slug | chars |
|---|---|
| `aec-mlb-az-col-2026-09-24` | 380 |
| `aec-mlb-cle-kc-2026-09-25` | 381 |
| `aec-mlb-pit-det-2026-09-25` | 376 |

**This is not the fixture's text, and not a shortened version of it.** The
fixture said *void, stakes returned*. The venue pays the contract's **last
traded price**. Entered at 0.56 and last printing 0.20, that returns 0.20.
A stake return is whole. Different cash, not different wording.

### The reader, before and after

`read_terms` returned `{}` — zero of seven conditions — because
`PAYOUT_PROSE` had no class for a price settlement. Added
`PAY_LAST_FAIR_MARKET_PRICE`, never equivalent to a stake return under any
condition. The measured result:

| condition | book | venue | verdict |
|---|---|---|---|
| `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` | returns the stake | **last fair market price** | **MISMATCH** |
| the other six | stated | — | venue silent |

Verdict: **INCOMPATIBLE** under both published quote contexts. Silence
became a stated disagreement; nothing was admitted that was refused before.

### Two sentences still establish nothing, and why

- *"This market will settle to the winner of the … MLB game"* — states a
  payout, names **no condition** (no regulation, no innings count). The
  payout phrase itself also misses, because the team names sit between
  "winner of the" and "game".
- *"Extra innings are included if played."* — names the condition
  `DECIDED_AFTER_REGULATION`, states **no payout**.

Reading them together means attributing one sentence's payout to another
sentence's condition. That is the cross-sentence inference the module
forbids by design, and it is **not applied**. It would also change nothing:
the abandonment mismatch stands either way, so a generous reading still
returns INCOMPATIBLE.

---

## 2b · The triggers are different variables. Checked before trusting the verdict.

`CONDITIONS` are named for what happened to the fixture. **Neither side
conditions its rule on that.** Both key on a clock, and not the same one:

| side | trigger variable | window, as published |
|---|---|---|
| book | hours between scheduled start and actual start | *"If a fixture isn't started **12 hours** after its scheduled starting time all bets on that fixture will be voided."* |
| venue | whether a make-up date exists inside a named window | *"…not rescheduled to a date **within two weeks** of the originally scheduled date…"* |

Three regions follow, and only one of them is a clean conflict:

| region | book pays | venue pays | status |
|---|---|---|---|
| not started within 12 h **and** no make-up inside two weeks — the ordinary rainout | stake returned | last fair market price | **conflict, established** |
| not started within 12 h **but** a make-up inside two weeks — the common MLB case | stake returned (bet voided at the 12-hour mark) | *unstated in prose*; the contract survives to the make-up game | **not established** |
| played normally | winner on the final score | winner (across two sentences) | agrees in substance, unread by the sentence rule |

So `compare` now records, per condition, both sides' qualifiers, an
alignment verdict, and — where they differ —
`mismatch_scope: ESTABLISHED_ON_THE_NON_EMPTY_INTERSECTION_OF_TWO_DIFFERENT_TRIGGERS`
plus what is *not* established. The INCOMPATIBLE verdict rests on region 1,
which is non-empty and ordinary. It does **not** claim the two sides
described the same set of games.

---

## 2c · The dependency that actually blocks, and it is not a threshold

A de-vigged Pinnacle money line is **P(win | the bet has action)**. The
book's own rule voids a lost fixture out of its sample, so the price carries
no information about how often that happens or what it would be worth. The
venue contract does not void — it pays a number its order book prints, at a
time nobody can name in advance. So

```
V = P(A)·P(win | A)  +  P(¬A)·E[last fair market price | ¬A]
```

and `PINNACLE_DEVIG_V1` supplies **one of those four quantities**. Using it
alone as V is arithmetically identical to asserting **P(¬A) = 0** — a claim
about weather, scheduling and venue behaviour that nothing here has
measured.

| term | state |
|---|---|
| `P_win_given_action` | **HELD** — this source |
| `P_the_price_settlement_branch_fires` | **MISSING** — no feed reports postponements or make-up scheduling; the book price cannot contain it |
| `E_last_fair_market_price_given_that_branch` | **MISSING** — a function of the venue's book at an unknown instant, not a sports outcome |
| `the_two_windows_are_the_same_variable` | **MISSING** — 12 hours vs two weeks |

Three ways forward, each a capability rather than an adjustment:
a supported model of the alternative outcomes; a reference source whose own
rule matches the venue's; or a market with no price-settled branch. Declared
in `bettor_pinnacle_devig.WAYS_FORWARD`, carried on every `describe()`, and
a test greps the package for invented rate names so a "small postponement
factor" cannot appear.

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
| `docs.polymarket.us/sitemap.xml` | 2026-09-25T16:06Z | **200**, 618 enumerated URLs — the documentation IS published |
| `docs.polymarket.us/robots.txt` | same | **200**, `ai-train=yes, search=yes, ai-input=yes` — and readable |
| contract `description` field, **live** | continuously, per cycle | **200**, **380 chars** (probe field_shapes, 2026-09-25T16:51Z); which conditions it states is being read, not claimed |
| contract `description` field, **fixture** | test data | 183 chars, 2 conditions accepted — **a fixture, not the venue** |
| contract `assetPriceTerms` field | same | `None` on every contract read |

Recorded in `bettor_venue_settlement.VENUE_TERMS_CAPTURE_ATTEMPTS`.

---

## 4 · Supported documentation routes — answered

Guessing paths was the wrong method, and the supported route answered
immediately:

| Source | Result |
|---|---|
| `docs.polymarket.us/sitemap.xml` | **200**, **618 enumerated URLs** |
| `docs.polymarket.us/robots.txt` | **200**, `Content-Signal: ai-train=yes, search=yes, ai-input=yes` |

**The documentation is published and explicitly readable.** "The venue does
not publish this" is retracted in the code as well as here
(`bettor_venue_settlement.VENUE_TERMS_NOT_YET_LOCATED`, renamed from
`VENUE_TERMS_NOT_PUBLISHED` because the old name asserted the retracted
claim).

What 618 URLs establish is that **pages exist**. Not one word of their
content has been read, and no settlement condition is answered by a URL —
so nothing here infers compatibility from the sitemap, and the entry lane's
refusal is unchanged by it. The next step is mechanical and is instrumented:
the capture step now recognises a sitemap as an *index* rather than running
a prose extractor over it, enumerates every `<loc>`, and lists the paths
whose spelling could plausibly carry settlement terms. Those pages are then
fetched and read like any other source, and only a read sentence can change
a condition's state.

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
