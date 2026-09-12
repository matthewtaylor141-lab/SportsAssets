# AUDIT SNAPSHOT DRAW LOG

Every draw attempt is recorded here, including the ones that are not
authoritative. An Actions artifact or a git object inspected later must be
identifiable **from this file alone** as authoritative or not — otherwise a
verified-looking set of bytes with valid internal hashes is indistinguishable
from the real thing, which is the failure mode the whole seal exists to prevent.

Only a draw marked **AUTHORITATIVE** is `U2_SNAPSHOT_V1`.

---

## PRESEAL_DRAW_ATTEMPT_1 — NOT AUTHORITATIVE

| | |
|---|---|
| workflow run | 34668122141 |
| `U2_SNAPSHOT_V1_DRAWN_AT` | 2026-09-12T02:36:29.013249Z |
| source commit | `446db5a` |
| events / conditions / source notional | 214,651 / 17,755 / $48,327,388.19 |
| delta vs RUN 81A ORIGINAL | 0 / 0 / $0.00 |
| guards | all seven zero |
| DB canonical hashes | events `c632bdbb2ae80c5b651bf790a6e9ce03f4365a2ca4e7d0332dc8134f2ed5b3aa`, settlement `b2cb02b6f8a407bce079437fe5fabfcbf49cdb0cd4d9a6aa2a114b1b61fd74c5` |
| manifest hash | `9f4d8209b5560828fc856d038360f57a270280d1546e351ff8093ca6df49fcd7` |
| artifact | 10289059035 (`audit-snapshot-v1`, 90-day retention) |
| **why not authoritative** | **The durable seal failed.** The draw itself completed: guards passed, both canonical hashes were re-derived from the written bytes and matched. The script then died on its last line — the heredoc terminator was indented to stay inside the YAML block and bash only finds it at column 0 — so nothing was committed. The bytes exist only in the Actions artifact, which expires. It also predates the condition-linkage gate. |

## PRESEAL_DRAW_ATTEMPT_2 — NOT AUTHORITATIVE (committed, then superseded)

| | |
|---|---|
| workflow run | 34668306067 |
| `U2_SNAPSHOT_V1_DRAWN_AT` | 2026-09-12T02:40:30.136123Z |
| source commit | `8db1ccd`; committed as `610b2a3` |
| events / conditions / source notional | 214,609 / 17,752 / $48,327,293.76 |
| delta vs RUN 81A ORIGINAL | **−42 / −3 / −$94.43** |
| guards | all seven zero |
| DB canonical hashes | events `f2042fc11d7c6c7ef385f9e100b8cde579f1011de23623ff4afdde8e25eec8c6`, settlement `fb46249e1496c841f45e727eb5dc3377d0c55757bfc6f3823d2fdb2fc1820837` |
| manifest hash | `61203957be4a57c79e802f8a94a8cc87f6ddc3c5462345a061ad4459637ec45e` |
| **why not authoritative** | It drew and committed cleanly, but **it predates the condition-linkage gate**, which the approved design requires before any draw is treated as the permanent sealed baseline. Its settlement universe is derived from `trades.condition_id` alone, so it cannot answer whether a null-condition event is recoverable through token metadata. Superseded in place; its bytes remain in git history under this commit. |

### What these two attempts measured that a single draw could not

They bracket a four-minute window and disagree:

| | drawn at | events | delta vs 81A ORIGINAL |
|---|---|---|---|
| attempt 1 | 02:36:29Z | 214,651 | 0 |
| attempt 2 | 02:40:30Z | 214,609 | **−42** |

RUN 81A ORIGINAL itself ran 02:04–02:10Z at 214,651, and run 80 measured 214,708
at 00:16Z. So the sequence is 214,708 → 214,651 → 214,651 → 214,609: **the loss
is not uniform in time.** `workers/retention.py` deletes in batches
(`BATCH_ROWS = 5000`, capped per cycle, on an interval), so a window can see
none and the next can see 42. That is a direct measurement of burstiness, and it
is the reason a "no drift over 32 minutes" reading would have been wrong to
generalise. The 37-day horizon is unaffected either way.

---

## U2_SNAPSHOT_V1 — AUTHORITATIVE

Recorded on the draw that carries the condition-linkage gate and seals durably.
Until that row is filled in, **no draw is authoritative and none may be cited as
`U2_SNAPSHOT_V1`.**
