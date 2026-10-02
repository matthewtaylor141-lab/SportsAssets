# Derek: larger starting positions and more training opportunities

The owner requested more dollars deployed and more trades in the existing
$500,000 simulated experiment, while preserving Xavier's working management.
This is a SEPARATE trading-policy candidate from the five-capability workbench.
Do not change your frozen v5 or feed gate to include it.

I read `paper_explore.py` at your `7a896b0`: its $100 entry cap, $5,000 aggregate
cap, $1,000 loss stop, sampling target 6 and floor .35 match this patch's base.
The session CODE defaults already target $5,000 for investment entries, but
the running session freezes its configuration at creation: read the actual
session before claiming that target is active. Small training orders arise
from the separate exploration cap, not necessarily the investment setting.

## Implemented V2

| Setting | Existing training V1 | Proposed training V2 |
|---|---:|---:|
| Maximum entry cost including fees | $100 | $250 |
| Per-sport sampling target in the six-hour frame | 6 | 12 |
| Minimum inclusion probability | 35% | 70% |
| Aggregate open training exposure + reservations | $5,000 | $5,000 |
| Gross realized training-loss stop | $1,000 | $1,000 |

These are a modest initial implementation of the owner's directional request,
not numbers the owner dictated. Review the concrete values with this receipt.
At the full $250 cap, the existing aggregate budget supports about 20 concurrent
training positions. It does NOT promise 200–500 daily fills or unrestricted
deployment of the full account.

Policy label: `PINNACLE_EXPLORATION_PAPER_V2`. The sampling method is V2, while
the random fixture draw deliberately retains the original V1 seed. This means
raising inclusion probability never redraws an earlier eligible fixture out.
Each new decision records the new inclusion probability and the retained draw
version. Preserve these per-record weights when comparing cohorts.

Existing decision idempotency keys are unchanged; old decisions are not replayed,
old orders/positions are not resized, and the funding entry/session is untouched.
Future valuations receive V2 metadata. Xavier's code is unchanged. No real-money
execution is enabled and no venue request is added. The investment policy still
requires its existing 0.5pp gross edge and positive after-fee EV. Training may
have negative expected value and remains labelled accordingly.

Sizing remains bounded by fresh observed best-level depth, consumed liquidity,
available cash, maximum fees and remaining training headroom. The same limits
are checked again under the account lock. One exploration position per fixture,
cross-strategy exclusion, freshness and contract matching remain in place.

## The separate throughput dependency

Your latest production report says the PinnAPI feed is observe-only and has no
decision consumer. This patch does not wire C2/C3. A larger clip increases
deployment per fill; broader sampling increases inclusion only where eligible
fixtures exist. Neither creates a second book, an exact contract match, fresh
quotes or executable depth. If the sample already includes every eligible game,
the sampling change alone will add no trades.

Continue the subscribed-sport census correction and C2/C3 integration: overlay
Pinnacle as one book beside the existing second book, then measure actual
receipt-to-evaluation latency and compatible live/prematch coverage. Publish
refusals and missed evaluations by market type. Do not present a refreshed UI,
socket connection or 70% sampling floor as all-market, once-per-second coverage.

## Tests and release

Local tests: see `python-receipt.txt`. The new tests cover five price levels,
depth, fees, monotonic inclusion, old fixture draws, stable decision IDs, and
the entry/exposure/loss/fixture lock checks. The existing real lifecycle test now
requires an entry and fill costing **more than $100 and no more than $250**;
its debit, handoff and reconciliation checks remain. The old pure $100 sizing
proof is retained. Real-Postgres cases skipped locally and must run in your gate.

Apply this patch to a separate candidate on your accepted current backend.
Keep your newer book/freshness/feed changes and all critical-list entries.
Run the exact-SHA full gate, including the new critical ramp tests, existing
exploration lifecycle, concurrent ownership and shared-ledger proofs. Deploy API
only if accepted; no worker/edge-shadow deploy. Do not toggle maker entry.

Post-release report:

- serving SHA and V2 policy/version metadata;
- actual running session risk/entry settings;
- eligible → sampled → attempted → filled funnel for the new version;
- first new fill's quantity, price, fees, reservation/debit, source book age,
  Xavier handoff/protection and Audrey finding IDs;
- aggregate exposure and loss-stop headroom in one ledger snapshot;
- comparisons by policy version, excluding pre-release V1 rows and distinguishing
  training activity from positive-EV investment entries.

No production limit, environment, ledger, deployment or trade was changed by
Codex here. The local patch is ready for your integration and release gate.
