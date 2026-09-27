# THE 177 STANDING FAILURES — classified, 2026-09-27

## 0 · The claim being corrected

I wrote that none of the 177 standing test failures affects the capital path, and
the only support I offered was that **they are also present in the baseline**.

> **That supports a different claim.** "Also failing before my changes"
> establishes that **the release did not cause them**. It says nothing at all
> about whether the code they cover can move money. A failure that predates the
> release is still a capital-path failure if it sits on the capital path.

So the support has to be structural. `backend/sportsassets/capital_path.py`
computes it, and `tests/test_which_failures_can_reach_money.py` holds the
computation honest.

---

## 1 · Two measurement defects I made first, both of which flattered the answer

Recorded because each one changed the number materially, and the second changed
it in the direction I would have preferred.

| defect | effect on the answer |
|---|---|
| **matching on the first dotted segment.** `workers.mirror_live` counted as capital-path because the bare package `workers` is reachable — *some* worker is. | **inflated** it: 32 files were false positives, nearly the whole result. |
| **`from sportsassets import X` was dropped entirely.** The parser looked for a module starting `"sportsassets."` or a relative level; that statement has neither. It is the form nearly every test here uses. | **emptied** it: files that plainly import a capital module were classified as importing nothing, and the first-ring count came out as **0 of 177** — exactly the answer I had originally claimed. |

Both are pinned by tests, in the same file as the classification, so the number
cannot silently return to the convenient one.

---

## 2 · Three measures, kept apart

The set of code that can move money is defined by walking the real import graph
from **15 named capital entry points** — deliberately wider than "the thing that
calls the venue", because a module that decides a size, records a fee or reports a
balance misstates money even when no order is sent.

| | measure | what it is | result on the 177 |
|---|---|---|---|
| **A** | **import closure** | every module reachable from an entry point, to any depth (70 modules) | **113 identities / 34 files** inside |
| **B** | **coupling surface** | the named symbols the capital lane imports from outside itself | **2 symbols**; **0 identities** import one by name |
| **C** | **first ring** | the entry points plus what they import *directly* — depth 0 and 1, where a defect is at most one call from money (36 modules) | **107 identities / 30 files** inside |

### Why A is so much wider than C, and why that is not alarm

`bettor_funded_book` imports exactly one name from the legacy copier:

```python
from .live_executor import fill_cash
```

A pure arithmetic function. But importing that module executes it, and it imports
`analytics.mirror_live_rules`, `workers.mirror_shadow`, `api.pmus_account` and the
rest of the copier. **So the whole copier lands in the closure on the strength of
one function.** The closure is the conservative outer bound and is read that way:
*inside* means "must be read individually", never "is a capital defect".

### The whole coupling surface, named

```
live_executor.fill_cash              <- bettor_funded_book
bettor_market_stream._parse_ts       <- bettor_funded_activation
```

`fill_cash`'s own behaviour is covered by `test_price_fidelity`,
`test_mirror_live_ledger`, `test_mirror_live_le_consumers` and
`test_mirror_live_rules` — **none of which has a standing failure.** That narrows
the question; it does not close it, because importing a module is not exercising
the symbol.

---

## 3 · The corrected conclusion

> **107 of the 177 standing failures sit in the first ring.** My claim that none
> of them affects the capital path was **not supported and is withdrawn.**

What replaces it is narrower and true:

* **the release did not cause any of them** — that is what the paired baseline
  comparison establishes, and it still stands;
* **the two symbols the funded lane actually names are covered by passing
  tests**;
* **107 identities require individual reading before funded submission**, and
  that reading is release-blocking work, not a footnote.

The 70 identities outside the closure are recorded as `NO_STATIC_IMPORT_PATH` —
bounded evidence, not a clearance.

## 4 · What static imports cannot see, stated rather than implied

* a module imported at runtime by name (`importlib`, a registry of strings);
* **a lane reached only through the DATABASE** — one writer, another reader, no
  import between them. This is exactly the cross-lane exposure problem, and it is
  why account-wide enforcement is a separate exhibit rather than a corollary of
  this one;
* a subprocess, a job defined outside Python, or a SQL trigger;
* a module outside the closure whose output is copied into a capital table by a
  migration or by hand.

**And none of this says the failures are acceptable.** 177 standing failures are
a debt. This document says which of them could touch money, not that the rest do
not matter.
