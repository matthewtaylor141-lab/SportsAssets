# BETTOR adapters for Profitability Stack V1

RESEARCH / PAPER-SHADOW ONLY. These adapters read SELECT-only research-SQL
extracts of BETTOR's immutable paper records and write receipts. They have no
database, network, credential, order, cancel or capital path (a test enforces
this). They change nothing in production: SMALL LIVE stays SHADOW, historical
PAPER rows are read and never rewritten, and every existing risk, freshness,
settlement and profitability gate is untouched.

The package in `../bettor_profit_stack` is vendored unchanged. Its own 39 tests
pass. The adapters add 23 tests under `tests/`.

| module | adapter | inputs |
|---|---|---|
| Execution Truth | `execution_truth_adapter.py` | `ps_orders_v1` (paper orders, fills, fees, marks) + the Alpha Lab decision dataset (`apl_extract_v1`) |
| Structural Arb | `structural_arb_adapter.py` | `ps_arb_v1` (registry legs, ontology, settlement state, books) |
| Digital Twin | `digital_twin_adapter.py` | `ps_twin_v1` (non-protection orders + every book observation around them) |
| Profitability Governor | `governor_adapter.py` + `positions.py` | `ps_orders_v1` settled positions |
| P&L Attribution | `attribution_adapter.py` + `positions.py` | `ps_orders_v1` settled positions |

`queries/` holds byte-identical copies of the SQL the research-SQL workflow
ran from `research/` on `claude/p0-closeout`. Each receipt records the SHA of
its query.

## Rules every adapter follows

- **Unit of analysis.** The statistical unit is the canonical event (the
  premap `event_slug`, or the market slug with its family prefix and outcome
  suffix removed). Decision rows and unique events are always reported
  separately.
- **Unmeasured costs.** A cost the records do not contain is UNMEASURED. It is
  never treated as zero, and a path that depends on it is not admissible.
- **Settlement identity.** Equivalence is never inferred from labels. Only
  `SETTLEMENT_PROVEN_COMPATIBLE` counts as proven, and
  `SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED` never does.
- **Capital.** A strategy gets capital only with every proof the package
  requires. Otherwise the result is CASH. Nothing is promoted for being least
  negative, and nothing is activated in production.

## Reproduce

```
python -I bettor_adapters/run_receipts.py receipts/<dir> \
  --orders <ps_orders_v1 log> --orders-run <id> \
  --arb <ps_arb_v1 log> --arb-run <id> \
  --twin <ps_twin_v1 log> --twin-run <id> \
  --decisions ../alpha_proof_lab/receipts/2026-10-07_run37625484193/dataset.jsonl.gz \
  --generated-at <iso>
```

The runner refuses a log whose parsed row count differs from psql's
`(N rows)` footer. Given the same inputs, it writes byte-identical receipts.
