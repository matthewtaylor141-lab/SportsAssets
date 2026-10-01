# What filled the database disk, and how to stop it recurring

Measured 2026-10-01 01:40–01:43Z with `research/disk_consumption.sql` (read-only), after the owner raised storage from 25 GB to 50 GB (recovered 01:23:46Z). Database size: **23 GB**.

## What consumed the 25 GB

| Relation | Total | Heap | Indexes | TOAST | What it is |
|---|---|---|---|---|---|
| `notification_outbox` | 8.8 GB | 7.95 GB | 0.84 GB | — | Push/telegram/ntfy delivery queue |
| `trades` | 6.7 GB | 3.6 GB | 3.0 GB | — | Whale trade history, 6,542,232 rows since 2024-11-16 |
| `pmus_activity_archive` | 2.6 GB | 0.09 GB | 0.07 GB | 2.4 GB | Venue activity archive (financial record) |
| `copy_probes` | 1.0 GB | 0.84 GB | 0.16 GB | — | Copy-probe evidence |
| `mirror_shadow` | 0.8 GB | 0.71 GB | 0.12 GB | — | Mirror shadow records |

**`notification_outbox` is the cause.** It holds 8,390,174 rows, every one already sent (`sent = true`, none pending, none collapsed):

| Channel | Rows | Payload | Since |
|---|---|---|---|
| webpush | 2,806,642 | 2.35 GB | 2026-07-24 |
| telegram | 2,806,566 | 2.30 GB | 2026-07-24 |
| ntfy | 2,776,966 | 2.13 GB | 2026-08-02 |

It grows by **118,000–257,000 rows a day, about 100–205 MB of payload a day** (last 30 days). Nothing ever removes a delivered row. `trades` adds 26,000–86,000 rows a day.

The combined growth is roughly 0.2–0.3 GB a day, indexes included (an estimate from the daily counts above). At that rate the 27 GB now free on the 50 GB volume lasts about 3–4 months.

## Proposal (owner decisions; nothing has been deleted)

1. **Turn on storage autoscaling now.** On Render, this grows the disk by 50% when it reaches 90% full, so a full disk can't suspend production again. It's a dashboard toggle on the database's Plan page and is billed per GB.
2. **Disk alerts.** Raise an alert at 70% and 85% of capacity. It can read `pg_database_size()` against the provisioned size on the API's existing heartbeat, or use Render's notification settings. Flag any table growing faster than the trend.
3. **Retention for the outbox only.** A delivered notification is a finished delivery-queue entry. It is not a financial, research or audit record. Proposed: a scheduled job deletes rows that are `sent = true` and older than 14 days, in batches, followed by plain `VACUUM` so the space is reused.
   - That reclaims about 7.5 GB for reuse within the database.
   - If a delivery history must be kept, first copy a daily per-channel count, plus any delivery IDs you need, to a small summary table.
   - Returning the space to the operating system would need `VACUUM FULL` or `pg_repack`, which locks the table. That isn't needed if autoscaling is on.
4. **No deletions elsewhere.** `trades`, `pmus_activity_archive`, `copy_probes`, `mirror_shadow` and every paper, funded, research and model table are financial, research or audit records and stay. If space ever matters, move `trades` older than a cutoff to compressed cold storage (a parquet export). That's an owner decision, not an automatic cleanup.
5. **Fix Blueprint drift.** `render.yaml` still declares `plan: basic-4gb` and `diskSizeGB: 25`. The live instance is `4c-16g` with 50 GB. A Blueprint sync would try to change the plan back (which means downtime) and would fail trying to shrink the disk. Update `render.yaml` to the live values, or keep Auto Sync off. Changing `render.yaml` means a push to the auto-deploying branch, so it needs your approval.
6. **Revisit the compute plan.** The emergency change from `basic_4gb` to `4c-16g` did not cause the recovery; the storage increase did. Moving back to a smaller plan means a few minutes of downtime, so schedule it if you decide to.
