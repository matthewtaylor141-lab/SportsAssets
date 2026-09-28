# The actual operating mode, measured in production

**Source:** `research/operating_state.sql` via `research-sql.yml` against the production database. Run 36373696910, job 108775092874, `psql exit=0`, query sha256 `0ff0a0c70e3de22fc80f75f8b91128ccde95bb8b042b7c0e2f2a4ede550c1ff2`, read at **2026-09-28T03:26:11Z**.

This answers "what is actually running" with rows, not description.

---

## 1 · The scheduled loop is running — continuously, and not in my session

| | |
|---|---|
| Last completed cycle | **2026-09-28 03:25:16.455102+00** (≈1 minute before the read) |
| Cycle state | **LIVE** |
| Writer build | `ad95d69ea73cd398bd9aa802370fadbdd1a0846a` |
| Writer module | `sportsassets.workers.ext_pinnacle_loop` |
| Source hash | `1ccd667f5aac` |
| Control row `ext_pinnacle_shadow` | **true** — armed |

**This matters for the overnight question.** The autonomous loop runs inside the deployed API service on Render, on its own cadence, writing its heartbeat to the database. It does not depend on my session being alive. That is the existing authorized durable job mechanism, and it is already operating.

## 2 · Safety state

| Control | Value | Meaning |
|---|---|---|
| `live_trading_paused` | **true** | the admin kill switch is **engaged** |
| `bettor_live_observation` | false | the observation lane is stopped |
| `research_shadow_uncalibrated` | true | the unfunded research-shadow lane is armed |
| `mirror_loss_stop` | *(row absent)* | the copy-lane loss breaker has not fired |

## 3 · What the loop is actually doing: cycling, admitting nothing

**`cycle_label: ZERO_EVALUATED__INPUT_PATH_BLOCKED`.**

The loop completes a cycle, and **zero candidates reach execution estimation**. That is the honest operating mode: it is running, it is not finding anything it can qualify. The label is deliberately worded so this cannot be read as evidence about available edge — with `evaluated == 0`, the cycle establishes nothing about opportunity. It is an input-path fact.

| | |
|---|---|
| `external_valuations` rows | **1,126** |
| Distinct markets | **54** |
| Newest observation | **2026-09-27 19:35:48+00** |

The newest valuation is ~7.8 hours older than the cycle that just ran. So the loop is cycling without writing new valuations — consistent with `ZERO_EVALUATED`, and the reason sits upstream in the input path, not in the loop.

## 4 · The funded book is empty, and that is a fact not an inference

| | |
|---|---:|
| `bettor_funded_intents` rows | **0** |
| ENTRY rows | **0** |
| UNRESOLVED | **0** |
| Residual total | **0** |

**No funded position exists.** So there is nothing for the servicing lane to manage in production right now. Every lifecycle result I have reported is from the controlled demonstration on an isolated database, and the empty book here is what keeps those two apart.

## 5 · One prediction of my own code, confirmed against production

`carries_a_servicing_decision: f`.

The serving build is `ad95d69`, which predates the commit that persists `funded_servicing` into the cycle heartbeat. `bettor_funded_book.last_scheduled_decision` is written to distinguish exactly this case, and its wording is:

> *"the last cycle recorded no servicing decision. A build older than the one that persists it writes this row without the field, so an absent decision here means the SERVING BUILD, not an idle lane."*

That is precisely what production shows. The reader reports the right cause rather than rendering an empty decision set that would read as "nothing to do".

---

## 6 · So: is this "the completed autonomous system running"?

**No, and I will not present it as one.** What is true:

- The scheduled loop **is** running autonomously, continuously, on its own cadence, in the deployed service.
- It **is** armed, and the kill switch **is** engaged.
- It admits **zero** candidates, so it places no orders and manages no inventory.
- The funded book is **empty**, so no management action has occurred on real money.

A loop that cycles but qualifies nothing is not a working trading system — that was stated to me plainly and it is the correct reading of these rows. The gap is the input path: `ZERO_EVALUATED__INPUT_PATH_BLOCKED`, whose largest measured cause is the execution estimate reaching only 5.2% of evaluations, with `QUOTE_STALE` the most common first refusal.
