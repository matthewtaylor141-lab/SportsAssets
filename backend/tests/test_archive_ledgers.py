"""The in-memory archive is the folded ledgers, not the rows (design D).

The v3 snapshot key was rolled back on 2026-09-03 (commit aca938c)
because the full 531,313-row archive, held as a list of slim dicts,
put the API at 1.25-1.75 GB against the 2 GB kill line and its heavy
endpoints answered 502; the truncated 302,901-row v2 snapshot serves
3,393 settled where the account has 3,700. build() only ever reads
three per-slug dicts derived from those rows, so the archive now holds
the dicts -- folded by build()'s OWN code, lifted out into
_fold_trade / _fold_resolution -- plus a bounded id memory for the
window dedupe and the raw rows the fold cannot classify.

What these tests pin:

  * build(ledgers=None) is untouched: every existing test in the suite
    still passes against the same source.
  * build(ledgers=archive) is BYTE-IDENTICAL to the row form on
    randomized fixtures (a smaller run of the 600-fixture harness that
    proved 14,400 builds identical), whether the ledgers come straight
    from the fold or through the snapshot's JSON round trip, and the
    ledgers come out of every build exactly as they went in.
  * the snapshot is O(slugs), not O(rows), lives under a NEW key, and
    any foreign shape (a v2/v3 row list, another fold_version, a
    truncated record) reads as None: re-grind, never misread.
  * the id memory is bounded the way the design says and fails closed
    on an id it cannot place in time.
  * the request path dedupes the window against the id memory in one
    synchronous block, serves the promotion buffer as ledgers, and
    discloses the archive's form and sizes.
  * the hydrate folds chunk by chunk on the loop, checkpoints every 20
    chunks with the cursor, resumes a partial checkpoint, discards a
    stale complete one, and never runs twice at once.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import random
import time
from datetime import datetime, timezone

import pytest

from sportsassets.api import track_record as tr

TS0 = 1785542400.0        # 2026-08-01T00:00:00Z
DAY = 86400.0
NOON = 16 * 3600
TS_AUG2 = TS0 + DAY + NOON
SINCE_VALUES = (TS0 - 10 * DAY, TS0, TS0 + 20 * DAY)
_T = "ACTIVITY_TYPE_TRADE"
_R = "ACTIVITY_TYPE_POSITION_RESOLUTION"


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    """Same contract as tests/test_track_record.py: an unreachable
    database HANGS asyncpg rather than failing, so every DB helper is
    kept off the wire here."""
    from sportsassets import db

    async def _none(*_a, **_k):
        return None

    async def _no_pool(*_a, **_k):
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(db, "get_pool", _no_pool)
    for fn in ("_load_persisted", "_load_legacy_persisted",
               "_persist_payload"):
        monkeypatch.setattr(tr, fn, _none, raising=False)
    # Process-wide state other modules' tests leave behind must not
    # decide these outcomes.
    monkeypatch.setitem(tr._persist_state, "settled", -1.0)
    monkeypatch.setitem(tr._persist_state, "stake", -1.0)
    monkeypatch.setitem(tr._persist_state, "total", 0.0)
    monkeypatch.setitem(tr._payload_cache, "data", None)
    monkeypatch.setitem(tr._hydrate_progress, "ledgers", None)
    monkeypatch.setitem(tr._hydrate_progress, "last", "")
    monkeypatch.setitem(tr._hydrate_progress, "running", False)
    monkeypatch.setitem(tr._hydrate_err, "chunks", 0)
    monkeypatch.setitem(tr._snap_state, "at", 9e12)
    tr._refused.update(streak=0, stakes=[], settled=[])


# ── fixtures: the randomized generator of the equivalence harness ────

_SIDES = ["BUY", "SELL", "TRADE_SIDE_SELL", "TRADE_SIDE_BUY", None, ""]
_QTYS = [0, 1, 2, 3, 5, 10, 25, 100, 240, 587, 0.5]
_PRICES = [0.03, 0.09, 0.225, 0.25, 0.30, 0.34, 0.44, 0.5, 0.54, 0.56,
           0.585, 0.6, 0.85, 0.0, 1.0, 1.5, -0.1]
_RPS = [0.0, 0.0, 0.0, 0.0, 0.4, -0.6, 0.05, 5.04, -435.07, 1.0, 140.0]


def _amount(rng, v):
    return rng.choice([{"value": v}, {"value": str(v)},
                       {"value": v, "currency": "USD"}, v, str(v)])


def _rand_trade(rng, slug, i, ts):
    qty, price, rp = rng.choice(_QTYS), rng.choice(_PRICES), rng.choice(_RPS)
    t = {"marketSlug": slug, "qty": _amount(rng, qty),
         "price": _amount(rng, price)}
    if rng.random() < 0.85:
        t["realizedPnl"] = rng.choice([_amount(rng, rp), None])
    layout = rng.choice(["top", "deep", "deep", "none", "both"])
    if layout in ("top", "both"):
        t["side"] = rng.choice(_SIDES)
    if layout in ("deep", "both"):
        if layout == "deep":
            t["side"] = None
        k = rng.choice(["aggressorExecution", "passiveExecution"])
        t[k] = {"order": {"side": rng.choice(
            ["ORDER_SIDE_SELL", "ORDER_SIDE_BUY", None])}}
    act = {"id": f"t{i}", "type": _T, "trade": t}
    if ts is not None:
        where = rng.choice(["top", "trade", "trade", "both"])
        if where in ("top", "both"):
            act["timestamp"] = rng.choice([ts * 1000, ts])
        if where in ("trade", "both"):
            t["createTime"] = (ts + (999 if where == "both" else 0)) * 1000
    return act


def _rand_resolution(rng, slug, i, ts):
    realized = rng.choice([0.0, 0.0, 0.4, -1.0, 4.0, 150.0, -120.0, 180.0])
    cost = rng.choice([0.0, 0.0, 1.0, 1.01, 2.0, 5.0, 6.0, 60.0])
    res: dict = {"marketSlug": slug}
    if rng.random() < 0.8:
        res["afterPosition"] = {
            "realized": _amount(rng, realized),
            "marketMetadata": rng.choice([{"title": "Late"}, {"title": None},
                                          {}, {"title": "AIK vs Örgryte"}])}
    if rng.random() < 0.8:
        res["beforePosition"] = {
            "cost": _amount(rng, cost),
            "realized": rng.choice([_amount(rng, 0.0), _amount(rng, realized),
                                    None]),
            "marketMetadata": rng.choice([{"title": "Tigers ML"}, {}])}
    act = {"id": f"r{i}", "type": _R, "positionResolution": res}
    if ts is not None:
        if rng.random() < 0.34:
            act["timestamp"] = ts * 1000
        else:
            res["createTime"] = datetime.fromtimestamp(
                ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return act


def _rand_junk(rng, slug, i, ts):
    kind = rng.choice(["notype", "deposit", "trade-noslug", "res-noslug",
                       "trade-noid", "res-noid", "empty-trade"])
    if kind == "notype":
        return {"id": f"j{i}", "timestamp": (ts or TS0) * 1000, "x": 1}
    if kind == "deposit":
        return {"id": f"j{i}", "type": "ACTIVITY_TYPE_DEPOSIT",
                "timestamp": (ts or TS0) * 1000, "deposit": {"amount": 5}}
    if kind == "trade-noslug":
        a = _rand_trade(rng, slug, i, ts)
        a["id"] = f"j{i}"
        del a["trade"]["marketSlug"]
        return a
    if kind == "res-noslug":
        a = _rand_resolution(rng, slug, i, ts)
        a["id"] = f"j{i}"
        a["positionResolution"]["marketSlug"] = None
        return a
    if kind == "trade-noid":
        a = _rand_trade(rng, slug, i, ts)
        a["id"] = None
        return a
    if kind == "res-noid":
        a = _rand_resolution(rng, slug, i, ts)
        del a["id"]
        return a
    return {"id": f"j{i}", "type": _T, "trade": None}


def _rand_ts(rng):
    if rng.random() < 0.06:
        return None
    return TS0 + rng.uniform(-5 * DAY, 40 * DAY)


def _rand_fixture(rng):
    n_slugs = rng.randint(3, 20)
    slugs = [rng.choice(["aec-mlb", "atc-nba", "tsc-wta", "astatc-nfl",
                         "asc-ekst", "atc-mlb-min-sea-2026-08-02-f5"])
             + f"-{i}-2026-08-{rng.randint(1, 28):02d}" for i in range(n_slugs)]
    acts = []
    for i in range(rng.randint(10, 110)):
        slug = rng.choice(slugs)
        r = rng.random()
        if r < 0.62:
            acts.append(_rand_trade(rng, slug, i, _rand_ts(rng)))
        elif r < 0.88:
            acts.append(_rand_resolution(rng, slug, i, _rand_ts(rng)))
        else:
            acts.append(_rand_junk(rng, slug, i, _rand_ts(rng)))
    # Duplicated fills (same row, new id, random position): the ledgers
    # sum floats in list order, and the order must be the row form's.
    for k in range(rng.randint(0, 6)):
        dup = copy.deepcopy(rng.choice(acts))
        if dup.get("id"):
            dup["id"] = f"d{k}-{dup['id']}"
        acts.insert(rng.randint(0, len(acts)), dup)
    positions = {}
    for slug in rng.sample(slugs, rng.randint(0, len(slugs))):
        positions[slug] = {
            "netPosition": _amount(rng, rng.choice([-10, 0, 0, 2, 5, 100, 500])),
            "cost": _amount(rng, rng.choice([0.0, 0.03, 1.0, 2.0, 6.0, 17.55,
                                             50.0, 150.0])),
            "cashValue": _amount(rng, rng.choice([0.0, 1.1, 5.5, 190.0])),
            "realized": _amount(rng, rng.choice([0.0, 0.0, 0.4, 0.7, -1.0,
                                                  4.0, 150.0])),
            "expired": rng.random() < 0.4,
            "marketMetadata": rng.choice([{"title": "T", "outcome": "Yes"},
                                          {"title": None}, {}])}
    if rng.random() < 0.3:
        positions["aec-mlb-mystery-2026-08-02"] = {
            "netPosition": 2, "cost": 1.0, "cashValue": 1.1}
    kwargs = {}
    if rng.random() < 0.5:
        kwargs["attributed"] = set(rng.sample(slugs, rng.randint(0, len(slugs))))
    if rng.random() < 0.5:
        kwargs["copy_slugs"] = set(rng.sample(slugs, rng.randint(0, len(slugs))))
    if rng.random() < 0.4:
        kwargs["manual_slugs"] = set(rng.sample(slugs, rng.randint(0, 2)))
    if rng.random() < 0.5:
        kwargs["max_stake"] = rng.choice([5.0, 100.0])
    if rng.random() < 0.6:
        kwargs["max_abs_pnl"] = rng.choice([100.0, 500.0])
    return positions, acts, kwargs


def _dump(payload):
    payload = dict(payload)
    payload.pop("generated_at", None)
    return json.dumps(payload, default=str)


def _state(led):
    return copy.deepcopy((led.entries, led.sold, led.resolutions, led.ids,
                          led.leftover, led.rows, led.unknown_ts))


def _roundtrip(led):
    return tr._ArchiveLedgers.from_rows(
        tr._unpack_rows(tr._pack_rows(led.to_rows())))


def _fill(aid, slug="s", qty=2, price=0.5, ts=TS_AUG2):
    return {"id": aid, "type": _T,
            "trade": {"marketSlug": slug, "qty": qty, "price": {"value": price},
                      "createTime": ts * 1000}}


def _res(aid, slug, ts, realized, cost):
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": aid, "type": _R,
            "positionResolution": {
                "marketSlug": slug, "createTime": iso,
                "afterPosition": {"realized": {"value": realized}},
                "beforePosition": {"cost": {"value": cost}}}}


def _meta(**over):
    """A meta record this fold WOULD write, so a foreign-shape case
    below fails for the reason it names and not for a missing digest
    or record count."""
    m = {"k": "meta", "form": "ledgers", "fold_version": tr._FOLD_VERSION,
         "fold_digest": tr._FOLD_DIGEST, "rows": 1, "unknown_ts": 0,
         "records": 2}
    m.update(over)
    return m


def _seven_record_archive():
    """1 meta + 2 entries + 1 sold + 1 resolution + 1 id chunk + 1
    leftover = 7 records, representing 5 rows."""
    led = tr._ArchiveLedgers()
    sell = _fill("c", "s3")
    sell["trade"]["side"] = "TRADE_SIDE_SELL"
    sell["trade"]["realizedPnl"] = {"value": 0.25}
    led.fold_many([tr._slim(_fill("a", "s1")), tr._slim(_fill("b", "s2")),
                   tr._slim(sell), tr._slim(_res("r", "s4", TS_AUG2, 1.0, 1.0)),
                   {"id": "j", "type": _T, "timestamp": TS_AUG2,
                    "trade": {"marketSlug": None}}])
    recs = led.to_rows()
    assert len(recs) == 7 and led.rows == 5
    return led, recs


class TestTheFoldIsBuildsOwnCode:
    """Anti-drift: the archive folds rows with the SAME two functions
    build() calls. If build() grew its own inline fold again, the
    archive and the request path would compute different ledgers from
    the same rows, and nothing in production would say so."""

    def test_build_calls_the_lifted_fold_and_holds_no_inline_copy(self):
        src = inspect.getsource(tr.build)
        assert "_fold_trade(act, entries, sold)" in src
        assert "_fold_resolution(act, resolutions)" in src
        assert "entries.setdefault(" not in src
        assert "resolutions[slug] = {" not in src

    def test_the_archive_calls_the_same_two_functions(self):
        src = inspect.getsource(tr._ArchiveLedgers.fold)
        assert "_fold_trade(act, self.entries, self.sold)" in src
        assert "_fold_resolution(act, self.resolutions)" in src

    def test_the_fold_reads_the_nested_resolution_time(self):
        assert '"ts": _any_ts(act),' in inspect.getsource(tr._fold_resolution)

    def test_build_without_ledgers_starts_from_empty_dicts(self):
        src = inspect.getsource(tr.build)
        assert "ledgers: _ArchiveLedgers | None = None" in src
        assert "entries, sold, resolutions = {}, {}, {}" in src


class TestLedgersBuildTheIdenticalRecord:
    """The equivalence harness at test size. Every fixture is split at
    a random point into archive and window, the window re-shows some
    archived rows (the venue's sliding window always overlaps), the
    archive half is slimmed / filtered / raw and the window half raw or
    slimmed, and each combination is built for three since values
    through the ledgers straight from the fold and through the
    snapshot's JSON round trip. Byte-identical json.dumps, and the
    ledgers deep-compare equal before and after."""

    def test_randomized_equivalence(self):
        rng = random.Random(20260903)
        builds = 0
        for fx in range(80):
            positions, acts, kwargs = _rand_fixture(rng)
            k = rng.randint(0, len(acts))
            archive_src, window_src = acts[:k], acts[k:]
            overlap = rng.sample(archive_src,
                                 rng.randint(0, min(5, len(archive_src))))
            window_src = [copy.deepcopy(a) for a in overlap] + window_src
            for arch_form, win_form in (("slim", "raw"), ("slim", "slim"),
                                        ("raw", "raw"), ("relevant", "raw")):
                if arch_form == "slim":
                    archive = [tr._slim(a) for a in archive_src]
                elif arch_form == "relevant":
                    archive = tr._slim_relevant(archive_src)
                else:
                    archive = archive_src
                window = ([tr._slim(a) for a in window_src]
                          if win_form == "slim" else window_src)
                # The row form: today's request path, verbatim.
                seen_ids = {str(a.get("id") or "") for a in archive}
                rest_rows = [a for a in window
                             if str(a.get("id") or "") not in seen_ids]
                led = tr._ArchiveLedgers()
                led.fold_many(archive)
                assert led.rows == len(archive)
                for form in ("direct", "json"):
                    L = led if form == "direct" else _roundtrip(led)
                    assert L is not None
                    before = _state(L)
                    rest = [w for w in window
                            if str(w.get("id") or "") not in L.ids]
                    assert rest == rest_rows
                    for since in SINCE_VALUES:
                        ref = _dump(tr.build(positions, archive + rest_rows,
                                             since, **kwargs))
                        out = _dump(tr.build(positions, L.leftover + rest,
                                             since, ledgers=L, **kwargs))
                        assert ref == out, (fx, arch_form, win_form, form, since)
                        builds += 1
                    assert _state(L) == before, "ledgers mutated by build()"
                    assert _state(L) == _state(led), "round trip changed them"
        assert builds == 80 * 4 * 2 * 3

    def test_since_is_applied_after_the_fold(self):
        """One archive serves every since: the default epoch, the
        ?since=2026-08-01 view, the AUDIT_SINCE reconciliations. The
        fold never sees since_ts; build()'s row walks apply it."""
        acts = [_fill("a", "old", ts=TS0 - 3 * DAY), _res("b", "old", TS0 - 2 * DAY, 1.0, 1.0),
                _fill("c", "new", ts=TS_AUG2), _res("d", "new", TS_AUG2 + 3600, -0.5, 1.0)]
        led = tr._ArchiveLedgers()
        led.fold_many([tr._slim(a) for a in acts])
        assert "since" not in inspect.signature(tr._ArchiveLedgers.fold).parameters
        for since in (TS0 - 10 * DAY, TS0):
            ref = _dump(tr.build({}, [tr._slim(a) for a in acts], since))
            assert _dump(tr.build({}, led.leftover, since, ledgers=led)) == ref
        early = tr.build({}, led.leftover, TS0 - 10 * DAY, ledgers=led)
        late = tr.build({}, led.leftover, TS0, ledgers=led)
        assert early["summary"]["settled"] == 2 and late["summary"]["settled"] == 1
        # The VENUE-BASIS totals never windowed; they read the same dicts.
        assert early["venue_totals"] == late["venue_totals"]
        assert late["venue_totals"]["settled"] == 2


class TestTheLedgersAreNeverMutated:
    def test_a_window_fill_on_an_archived_market_lands_in_the_copy(self):
        led = tr._ArchiveLedgers()
        led.fold(tr._slim(_fill("a", qty=2)))
        before = _state(led)
        out = tr.build({"s": {"netPosition": 5, "cost": 2.5, "cashValue": 2.7}},
                       [_fill("w", qty=3)], TS0, ledgers=led)
        assert out["trades"][0]["fills"] == 2 and out["trades"][0]["qty"] == 5
        assert _state(led) == before
        assert led.entries["s"]["fills"] == 1 and led.entries["s"]["qty"] == 2.0

    def test_a_later_resolution_in_the_window_replaces_only_in_the_copy(self):
        led = tr._ArchiveLedgers()
        led.fold(tr._slim(_res("r1", "m", TS_AUG2, 1.0, 1.0)))
        before = _state(led)
        out = tr.build({}, [_res("r2", "m", TS_AUG2 + 60, -1.0, 1.0)], TS0,
                       ledgers=led)
        assert out["trades"][0]["pnl"] == -1.0
        assert _state(led) == before and led.resolutions["m"]["realized"] == 1.0


class TestTheSnapshotIsLedgersNotRows:
    def _archive(self, n_rows=5_000, n_slugs=10, junk=3):
        rng = random.Random(4)
        led = tr._ArchiveLedgers()
        for i in range(n_rows):
            slug = f"aec-mlb-{rng.randrange(n_slugs)}-2026-08-02"
            if i % 5 == 0:
                led.fold(tr._slim(_res(f"r{i}", slug, TS_AUG2 + i, 1.0, 1.0)))
            elif i % 7 == 0:
                sell = _fill(f"t{i}", slug, ts=TS_AUG2 + i)
                sell["trade"]["side"] = "TRADE_SIDE_SELL"
                sell["trade"]["realizedPnl"] = {"value": 0.1}
                led.fold(tr._slim(sell))
            else:
                led.fold(tr._slim(_fill(f"t{i}", slug, ts=TS_AUG2 + i)))
        for j in range(junk):
            led.fold({"id": f"j{j}", "type": _T, "timestamp": TS_AUG2,
                      "trade": {"marketSlug": None}})
        return led

    def test_round_trip_through_the_unchanged_packer(self):
        led = self._archive()
        back = _roundtrip(led)
        assert back is not None and _state(back) == _state(led)
        assert back.rows == 5_003 and back.slugs() == 10
        assert [a["id"] for a in back.leftover] == ["j0", "j1", "j2"]

    def test_the_record_count_is_slugs_not_rows(self):
        """The memory property, in a deterministic form: 5,003 rows
        on 10 slugs are 1 meta + 3 ledgers x 10 slugs + ids/500 +
        3 leftover records, never 5,003."""
        led = self._archive()
        recs = led.to_rows()
        n_ledger = len(led.entries) + len(led.sold) + len(led.resolutions)
        n_ids = -(-len(led.ids) // tr._IDS_PER_RECORD)
        assert len(recs) == 1 + n_ledger + n_ids + 3
        assert len(recs) < 60
        assert recs[0] == {"k": "meta", "form": "ledgers",
                           "fold_version": tr._FOLD_VERSION,
                           "fold_digest": tr._FOLD_DIGEST,
                           "rows": 5_003, "unknown_ts": 0,
                           "records": len(recs)}
        assert all(len(r["v"]) <= tr._IDS_PER_RECORD
                   for r in recs if r["k"] == "i")

    def test_to_rows_hands_the_packer_copies(self):
        led = self._archive(50, 3, 0)
        recs = led.to_rows()
        for r in recs:
            if r["k"] == "e":
                assert r["v"] is not led.entries[r["s"]]
                assert r["v"] == led.entries[r["s"]]

    _GOOD_E = {"k": "e", "s": "x", "v": {"first_ts": 1.0, "qty": 1.0,
                                         "notional": 1.0, "fills": 1}}

    @pytest.mark.parametrize("bad", [
        None, "not a list", {}, [], [{"k": "e", "s": "x", "v": {}}],
        [_meta(form="rows", rows=0, records=1)],
        [_meta(fold_version=tr._FOLD_VERSION + 1, rows=0, records=1)],
        [_meta(fold_digest="0" * 64, rows=0, records=1)],
        [_meta(rows=-1, records=1)],
        [_meta(), {"k": "zzz", "v": 1}],
        [_meta(), {"k": "e", "s": "x", "v": {"first_ts": 1.0, "qty": 1.0}}],
        [_meta(), {"k": "e", "s": "x", "v": {"first_ts": 1.0, "qty": 1.0,
                                             "notional": 1.0, "fills": 1.0}}],
        [_meta(), {"k": "i", "v": [["a"]]}],
        [_meta(), {"k": "l", "v": "row"}],
        [_meta(), {"k": "s", "s": "x"}],
        # A meta that claims more records than follow it (a cut-off
        # write), fewer (an appended one), a missing count, a count of
        # the wrong type: the writer's count and the list must agree.
        [_meta(records=3), _GOOD_E],
        [_meta(records=1), _GOOD_E],
        [_meta(records=2.0), _GOOD_E],
        [_meta(records=True), _GOOD_E],
        [{k: v for k, v in _meta().items() if k != "records"}, _GOOD_E],
        [{k: v for k, v in _meta().items() if k != "fold_digest"}, _GOOD_E],
    ])
    def test_a_foreign_shape_reads_as_none(self, bad):
        assert tr._ArchiveLedgers.from_rows(bad) is None

    def test_the_meta_alone_is_not_a_foreign_shape(self):
        """The negative cases above are only meaningful if the meta
        helper reads back on its own: nothing here fails for the
        wrong reason."""
        led = tr._ArchiveLedgers.from_rows([_meta(records=1)])
        assert led is not None and led.rows == 1

    def test_a_truncated_record_list_reads_as_none(self):
        """The review case: three records of a seven-record archive
        used to read back as an archive of every row the meta claimed,
        and that figure feeds the 98% promotion gate and archive_rows."""
        led, recs = _seven_record_archive()
        assert tr._ArchiveLedgers.from_rows(recs[:3]) is None
        for cut in range(1, len(recs)):
            assert tr._ArchiveLedgers.from_rows(recs[:cut]) is None, cut
        assert tr._ArchiveLedgers.from_rows(recs + [recs[-1]]) is None
        back = tr._ArchiveLedgers.from_rows(recs)
        assert back is not None and _state(back) == _state(led)
        assert recs[0]["records"] == 7

    def test_a_v2_row_list_reads_as_none(self):
        rows = [tr._slim(_fill(f"a{i}")) for i in range(50)]
        assert tr._ArchiveLedgers.from_rows(tr._unpack_rows(tr._pack_rows(rows))) is None

    def test_the_save_writes_the_v4_key_and_form(self):
        class Pool:
            def __init__(self):
                self.writes = []

            async def execute(self, q, *a):
                self.writes.append(a)

        pool = Pool()
        led = self._archive(40, 4, 1)
        asyncio.run(tr._save_snapshot(pool, led.to_rows(), complete=False,
                                      last="cursor-9"))
        (key, val), = pool.writes
        assert key == "track_record_archive_ledgers_v4" == tr._SNAP_KEY
        obj = json.loads(val)
        assert obj["form"] == "ledgers_v4" and obj["rows"] == 41
        assert obj["complete"] is False and obj["last"] == "cursor-9"
        back = tr._ArchiveLedgers.from_rows(tr._unpack_rows(obj["gz"]))
        assert back is not None and _state(back) == _state(led)

    def test_the_load_returns_ledgers_or_none_never_rows(self):
        class Pool:
            def __init__(self, gz):
                self.gz = gz

            async def fetchval(self, q, *a):
                assert a == (tr._SNAP_KEY,)
                return json.dumps({"at": time.time(), "complete": True,
                                   "last": "", "gz": self.gz})

        led = self._archive(30, 3, 0)
        snap = asyncio.run(tr._load_snapshot(Pool(tr._pack_rows(led.to_rows()))))
        assert snap["complete"] and _state(snap["ledgers"]) == _state(led)
        v2 = asyncio.run(tr._load_snapshot(
            Pool(tr._pack_rows([tr._slim(_fill("a"))]))))
        assert v2["ledgers"] is None, "a row list is not an archive"


# The digest this tree's fold produces. Recorded on purpose: see the
# failure message in the test that asserts it.
_RECORDED_FOLD_DIGEST = (
    "ea3918bfd5f591f715ba70cec1630567fff7d2efea1ef454a0c6a40c9ea001f1")


class TestTheFoldIdentityIsDerivedFromTheCode:
    """_FOLD_VERSION is a promise nobody enforces: from_rows checked
    only the key SET of each ledger entry, so a change to the values a
    fold writes -- the 2026-08-19 nested-side fix, the 2026-09-02
    _any_ts resolution-time fix -- would have read the old ledgers as
    valid, the rolling save would have re-persisted them every 6 h,
    and no re-grind would ever fire. The snapshot now carries a digest
    of the fold's own source and from_rows refuses any other."""

    def test_a_snapshot_written_under_a_different_digest_reads_as_none(
            self, monkeypatch):
        led, recs = _seven_record_archive()
        assert recs[0]["fold_digest"] == tr._FOLD_DIGEST
        # The same snapshot, read by a process whose fold differs.
        monkeypatch.setattr(tr, "_FOLD_DIGEST", "f" * 64)
        assert tr._ArchiveLedgers.from_rows(recs) is None
        assert tr._ArchiveLedgers.from_rows(
            tr._unpack_rows(tr._pack_rows(recs))) is None
        # And a snapshot stamped with a digest this process did not
        # produce, whatever its version says.
        monkeypatch.undo()
        foreign = copy.deepcopy(recs)
        foreign[0]["fold_digest"] = "0" * 64
        assert foreign[0]["fold_version"] == tr._FOLD_VERSION
        assert tr._ArchiveLedgers.from_rows(foreign) is None
        assert tr._ArchiveLedgers.from_rows(recs) is not None

    def test_the_boot_regrinds_rather_than_serve_a_foreign_fold(self, monkeypatch):
        """End to end through _hydrate_all: a complete, fresh snapshot
        written by another fold is not the hydrate; the table is
        ground from the start."""
        led0, recs = _seven_record_archive()
        snap = json.dumps({"at": time.time(), "complete": True, "last": "",
                           "gz": tr._pack_rows(recs)})
        monkeypatch.setattr(tr, "_save_snapshot",
                            lambda *a, **k: asyncio.sleep(0))
        monkeypatch.setattr(tr, "_FOLD_DIGEST", "e" * 64)
        pool = _GrindPool(_table(2), snap=snap)
        led = asyncio.run(tr._hydrate_all(pool))
        (q, args), = pool.queries
        assert args[0] == "" and led.rows == 6
        assert _state(led) != _state(led0)

    def test_the_digest_changes_when_a_fold_helper_changes(self, monkeypatch):
        before = tr._fold_digest()
        assert before == tr._FOLD_DIGEST

        def _amt(a):          # a different reading of the same field
            return 0.0

        monkeypatch.setattr(tr, "_amt", _amt)
        after = tr._fold_digest()
        assert after != before
        monkeypatch.undo()
        assert tr._fold_digest() == before

    @pytest.mark.parametrize("name", tr._FOLD_SOURCES)
    def test_every_fold_source_moves_the_digest(self, monkeypatch, name):
        before = tr._fold_digest()

        def _other(*a, **k):
            return None

        monkeypatch.setattr(tr, name, _other)
        assert tr._fold_digest() != before, name

    def test_the_sources_cover_the_fold_and_what_it_reads_through(self):
        assert set(tr._FOLD_SOURCES) == {
            "_fold_trade", "_fold_resolution", "_slim", "_slim_relevant",
            "_any_ts", "_act_ts", "_amt"}
        for name in tr._FOLD_SOURCES:
            assert callable(getattr(tr, name))

    def test_the_recorded_digest(self):
        assert tr._FOLD_DIGEST == _RECORDED_FOLD_DIGEST, (
            "the fold's source changed (one of "
            f"{', '.join(tr._FOLD_SOURCES)}). That is allowed, and this "
            "is the consequence to know about: the next boot of the API "
            "refuses the persisted ledgers snapshot and re-grinds the "
            "full archive from the table (531k rows, minutes, "
            "checkpointed) BY DESIGN. A fold that silently read the old "
            "ledgers is exactly how the 2026-08-19 nested-side fix and "
            "the 2026-09-02 _any_ts resolution-time fix would have "
            "applied to new rows only, with every archived row left on "
            "the old fold for good. If the change is intended, record "
            f"the new digest here: {tr._FOLD_DIGEST}")


class TestTheIdMemoryIsBounded:
    def test_recent_newest_and_unknown_are_kept_and_the_rest_dropped(self, monkeypatch):
        monkeypatch.setattr(tr, "_ID_MEMORY_NEWEST", 100)
        now = TS0 + 60 * DAY
        led = tr._ArchiveLedgers()
        for i in range(400):                       # old: 60..20 days ago
            led.ids[f"old{i}"] = now - 60 * DAY + i * 0.1 * DAY
        for i in range(30):                        # inside the 7-day window
            led.ids[f"recent{i}"] = now - i * 3600
        led.ids["timeless"] = 0.0
        led.ids[""] = now - 59 * DAY
        dropped = led.prune_ids(now)
        assert dropped == 400 + 30 + 2 - len(led.ids)
        assert all(f"recent{i}" in led.ids for i in range(30))
        assert "timeless" in led.ids and "" in led.ids
        newest = sorted((ts, aid) for aid, ts in led.ids.items()
                        if aid and ts)[-100:]
        assert len(newest) == 100
        assert all(f"old{i}" not in led.ids for i in range(200))
        assert "old399" in led.ids, "the newest old ids survive by rank"
        assert len(led.ids) == 100 + 2

    def test_prune_is_a_no_op_below_the_rank_floor(self):
        led = tr._ArchiveLedgers()
        for i in range(500):
            led.ids[f"a{i}"] = 1.0
        assert led.prune_ids(9e12) == 0 and len(led.ids) == 500

    def test_the_constants_cover_the_deepest_sweep(self):
        assert tr._ID_MEMORY_NEWEST >= 3 * 8_000
        assert tr._ARCHIVED_ID_WINDOW_S >= 7 * 24 * 3600

    def test_an_unknown_time_is_kept_and_counted(self):
        led = tr._ArchiveLedgers()
        led.fold({"id": "x", "type": _T, "trade": {"marketSlug": "s", "qty": 1,
                                                    "price": 0.5}})
        assert led.ids == {"x": 0.0} and led.unknown_ts == 1
        assert led.prune_ids(9e12) == 0 and "x" in led.ids

    def test_a_repeat_id_is_refused_and_an_idless_row_is_not(self):
        led = tr._ArchiveLedgers()
        led.fold(tr._slim(_fill("a", qty=2)))
        led.fold(tr._slim(_fill("a", qty=2)))
        assert led.rows == 1 and led.entries["s"]["fills"] == 1
        noid = tr._slim(_fill(None, qty=1))
        led.fold(noid)
        led.fold(copy.deepcopy(noid))
        assert led.rows == 3 and led.entries["s"]["fills"] == 3
        assert "" in led.ids, "the empty key drops id-less window rows, as seen_ids did"

    def test_leftover_is_only_what_the_fold_cannot_classify(self):
        led = tr._ArchiveLedgers()
        rows = [{"id": "1", "x": 1},
                {"id": "2", "type": "ACTIVITY_TYPE_DEPOSIT"},
                {"id": "3", "type": _T, "trade": {"marketSlug": None}},
                {"id": "4", "type": _R, "positionResolution": {}},
                tr._slim(_fill("5")),
                {"id": "6", "type": _T,
                 "trade": {"marketSlug": "s", "qty": 5, "price": 7.0}}]
        led.fold_many(rows)
        assert [a["id"] for a in led.leftover] == ["1", "2", "3", "4"]
        assert led.rows == 6 and set(led.ids) == {"1", "2", "3", "4", "5", "6"}


def _serve(monkeypatch, archive, window, positions=None, progress=None,
           known=("k1",), since=None):
    class Cfg:
        pmus_key_id = "k"
        pmus_secret_key = "s"

    monkeypatch.setattr(tr, "settings", lambda: Cfg())
    monkeypatch.setattr(tr, "_archived_ids", set(known))
    monkeypatch.setitem(tr._archive_cache, "data", archive)
    monkeypatch.setitem(tr._hydrate_progress, "ledgers", progress)
    monkeypatch.setitem(tr._raw_cache, "data",
                        {"positions": positions or {}, "activities": window})
    monkeypatch.setitem(tr._raw_cache, "ts", 9e12)
    return asyncio.run(tr.track_record(since=since))


class TestTheRequestPathServesTheLedgers:
    def test_the_window_is_deduped_against_the_id_memory(self, monkeypatch):
        """A window row the archive already folded is NOT folded again
        on top of it; a new window row is. The payload equals the row
        form's build over archive rows + window remainder."""
        archived = [tr._slim(_fill("a1", "s", qty=2)),
                    tr._slim(_res("r1", "gone", TS_AUG2 + 60, 1.0, 1.0))]
        led = tr._ArchiveLedgers()
        led.fold_many(archived)
        before = _state(led)
        window = [_fill("a1", "s", qty=2), _fill("a2", "s", qty=3)]
        positions = {"s": {"netPosition": 5, "cost": 2.5, "cashValue": 2.7,
                           "marketMetadata": {"title": "S"}}}
        out = _serve(monkeypatch, led, window, positions)
        assert "error" not in out
        since_ts = datetime.strptime(tr.DEFAULT_SINCE, "%Y-%m-%d") \
            .replace(tzinfo=timezone.utc).timestamp()   # as track_record parses
        ref = tr.build(positions, archived + [window[1]], since_ts,
                       max_abs_pnl=tr.PNL_DISPLAY_CAP)
        for k in ("summary", "trades", "daily", "venue_totals", "sold_markets",
                  "account", "excluded_undatable"):
            assert out[k] == ref[k], k
        assert out["venue_totals"]["settled"] == 1
        assert _state(led) == before

    def test_the_source_block_discloses_the_form_and_sizes(self, monkeypatch):
        led = tr._ArchiveLedgers()
        led.fold_many([tr._slim(_fill("a1", "s")), tr._slim(_fill("a2", "t")),
                       {"id": "j", "type": _T, "trade": {}},
                       {"id": "u", "type": _T, "trade": {"marketSlug": "s",
                                                         "qty": 1, "price": 0.5}}])
        out = _serve(monkeypatch, led, [_fill("w1", "s")])
        assert out["activities_source"] == "archive+window"
        assert out["archive_form"] == "ledgers_v4"
        assert out["archive_rows"] == 4 and out["window_rows"] == 1
        assert out["archive_slugs"] == 2 and out["archive_ids"] == 4
        # "j" (no slug) is leftover; "j" and "u" carry no time at all.
        assert out["archive_leftover"] == 1 and out["archive_unknown_ts"] == 2

    def test_no_archive_still_serves_the_window_and_says_so(self, monkeypatch):
        out = _serve(monkeypatch, None, [_fill("w1", "s")],
                     {"s": {"netPosition": 2, "cost": 1.0, "cashValue": 1.1}},
                     since=tr.AUDIT_SINCE)
        assert out["activities_source"] == "venue_window"
        assert out["archive_form"] is None and out["archive_rows"] == 0
        assert out["summary"]["trades"] == 1

    def test_an_empty_archive_reads_as_no_archive(self, monkeypatch):
        out = _serve(monkeypatch, tr._ArchiveLedgers(), [_fill("w1", "s")],
                     {"s": {"netPosition": 2, "cost": 1.0, "cashValue": 1.1}},
                     since=tr.AUDIT_SINCE)
        assert out["activities_source"] == "venue_window"
        assert out["summary"]["trades"] == 1

    def test_since_on_the_request_path_uses_the_same_archive(self, monkeypatch):
        led = tr._ArchiveLedgers()
        led.fold_many([tr._slim(_res("r1", "aug", TS_AUG2, 1.0, 1.0))])
        default = _serve(monkeypatch, led, [])
        audit = _serve(monkeypatch, led, [], since=tr.AUDIT_SINCE)
        assert default["summary"]["settled"] == 0, "August is before the epoch"
        assert audit["summary"]["settled"] == 1
        assert default["venue_totals"] == audit["venue_totals"]

    def test_the_dedupe_and_the_copy_share_one_synchronous_block(self):
        src = inspect.getsource(tr.track_record)
        blk = src[src.index("if archive:\n        acts = archive.leftover"):
                  src.index("ledgers=archive or None")]
        assert "await" not in blk
        assert 'str(w.get("id") or "") not in archive.ids' in blk


class TestTheEmergencyPromotionServesLedgers:
    def test_progress_covering_known_history_is_served(self, monkeypatch):
        progress = tr._ArchiveLedgers()
        progress.fold_many([tr._slim(_fill(f"a{i}", "s")) for i in range(50)])
        out = _serve(monkeypatch, None, [_fill("w1", "s")],
                     progress=progress, known=[f"a{i}" for i in range(50)])
        assert out["activities_source"] == "archive+window"
        assert out["archive_rows"] == 50 and out["archive_form"] == "ledgers_v4"

    def test_progress_short_of_the_gate_is_not_served(self, monkeypatch):
        progress = tr._ArchiveLedgers()
        progress.fold_many([tr._slim(_fill(f"a{i}", "s")) for i in range(10)])
        out = _serve(monkeypatch, None, [_fill("w1", "s")],
                     progress=progress, known=[f"a{i}" for i in range(40)])
        assert out["activities_source"] == "venue_window"

    def test_the_promotion_checkpoints_never_completes(self, monkeypatch):
        from sportsassets import db

        saves = []

        async def _record(pool, records, *, complete, last=""):
            saves.append((records, complete, last))

        class Pool:
            pass

        async def _pool():
            return Pool()

        monkeypatch.setattr(tr, "_save_snapshot", _record)
        monkeypatch.setattr(db, "get_pool", _pool)
        monkeypatch.setitem(tr._snap_state, "at", 0.0)
        monkeypatch.setitem(tr._hydrate_progress, "last", "cursor-777")
        progress = tr._ArchiveLedgers()
        progress.fold_many([tr._slim(_fill(f"a{i}", "s")) for i in range(50)])

        class Cfg:
            pmus_key_id = "k"
            pmus_secret_key = "s"

        monkeypatch.setattr(tr, "settings", lambda: Cfg())
        monkeypatch.setattr(tr, "_archived_ids", {f"a{i}" for i in range(50)})
        monkeypatch.setitem(tr._archive_cache, "data", None)
        monkeypatch.setitem(tr._hydrate_progress, "ledgers", progress)
        monkeypatch.setitem(tr._raw_cache, "data",
                            {"positions": {}, "activities": []})
        monkeypatch.setitem(tr._raw_cache, "ts", 9e12)

        async def run():
            out = await tr.track_record()
            for _ in range(5):
                await asyncio.sleep(0)
            return out

        out = asyncio.run(run())
        assert out["archive_rows"] == 50
        (records, complete, last), = saves
        assert complete is False and last == "cursor-777"
        assert records[0]["k"] == "meta" and records[0]["rows"] == 50
        assert isinstance(records, list), "materialised before the packer"


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _GrindPool:
    """A table streamed in chunks. `snap` is what ingestion_state holds
    under the snapshot key (a JSON string), or None."""

    def __init__(self, chunks, snap=None):
        self.chunks = list(chunks)
        self.snap = snap
        self.queries: list = []
        self.execs: list = []
        self.mid_grind: list = []

    async def fetchval(self, q, *a):
        return self.snap

    async def execute(self, q, *a):
        self.execs.append(a)

    def acquire(self):
        pool = self

        class _Cur:
            def __init__(self):
                self.i = 0

            async def fetch(self, n):
                led = tr._hydrate_progress["ledgers"]
                pool.mid_grind.append((type(led).__name__,
                                       led.rows if led is not None else None,
                                       tr._hydrate_progress["running"]))
                if self.i >= len(pool.chunks):
                    return []
                c = pool.chunks[self.i]
                self.i += 1
                return c

        class _Conn:
            def transaction(self):
                return _Tx()

            async def cursor(self, q, *a):
                pool.queries.append((q, a))
                return _Cur()

        class _Acq:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *a):
                return False

        return _Acq()


def _table(n_chunks, per_chunk=3, start=0, as_str=True):
    chunks, k = [], start
    for _ in range(n_chunks):
        chunk = []
        for _ in range(per_chunk):
            act = _fill(f"act-{k:05d}", f"m{k % 4}", ts=TS_AUG2 + k)
            payload = json.dumps(act) if as_str else act
            chunk.append({"id": f"id-{k:05d}", "payload": payload})
            k += 1
        chunks.append(chunk)
    return chunks


class TestTheHydrateFoldsChunkByChunk:
    def test_folds_on_the_loop_and_checkpoints_every_twenty_chunks(self, monkeypatch):
        saves = []

        async def _record(pool, records, *, complete, last=""):
            saves.append((records[0]["rows"], complete, last))

        monkeypatch.setattr(tr, "_save_snapshot", _record)
        pool = _GrindPool(_table(45))
        led = asyncio.run(tr._hydrate_all(pool))
        assert led.rows == 135 and led.slugs() == 4
        assert led.entries["m0"]["fills"] == 34
        # The buffer the request path can promote is the ledgers being
        # folded, and it grows chunk by chunk (never a row list).
        names = {n for n, _, _ in pool.mid_grind}
        assert names == {"_ArchiveLedgers"}
        assert [r for _, r, _ in pool.mid_grind][:4] == [0, 3, 6, 9]
        assert all(running for _, _, running in pool.mid_grind)
        assert tr._hydrate_progress["running"] is False
        assert tr._hydrate_progress["ledgers"] is None
        assert saves == [(60, False, "id-00059"), (120, False, "id-00119"),
                         (135, True, "")]
        (q, args), = pool.queries
        assert "payload->>'type' = ANY($2::text[])" in q
        assert args == ("", list(tr.ARCHIVE_TYPES))

    def test_resumes_a_partial_checkpoint_from_its_cursor(self, monkeypatch):
        partial = tr._ArchiveLedgers()
        partial.fold_many([tr._slim(_fill(f"act-{k:05d}", f"m{k % 4}",
                                          ts=TS_AUG2 + k)) for k in range(9)])
        snap = json.dumps({"at": time.time(), "complete": False,
                           "last": "id-00008",
                           "gz": tr._pack_rows(partial.to_rows())})
        saves = []

        async def _record(pool, records, *, complete, last=""):
            saves.append((records[0]["rows"], complete))

        monkeypatch.setattr(tr, "_save_snapshot", _record)
        pool = _GrindPool(_table(2, start=9), snap=snap)
        led = asyncio.run(tr._hydrate_all(pool))
        (q, args), = pool.queries
        assert args[0] == "id-00008", "the grind resumes from the checkpoint"
        assert led.rows == 15 and saves == [(15, True)]
        full = tr._ArchiveLedgers()
        full.fold_many([tr._slim(_fill(f"act-{k:05d}", f"m{k % 4}",
                                       ts=TS_AUG2 + k)) for k in range(15)])
        assert _state(led) == _state(full)

    def test_a_fresh_complete_snapshot_is_the_hydrate(self, monkeypatch):
        led0 = tr._ArchiveLedgers()
        led0.fold(tr._slim(_fill("a")))
        snap = json.dumps({"at": time.time(), "complete": True, "last": "",
                           "gz": tr._pack_rows(led0.to_rows())})
        pool = _GrindPool(_table(3), snap=snap)
        led = asyncio.run(tr._hydrate_all(pool))
        assert pool.queries == [] and _state(led) == _state(led0)
        src = inspect.getsource(tr._hydrate_all)
        assert 'snap["complete"]' in src

    def test_a_stale_complete_snapshot_is_discarded_not_doubled(self, monkeypatch):
        """The row form resumed from a complete-but-stale snapshot with
        an empty cursor and appended the whole table onto it again."""
        led0 = tr._ArchiveLedgers()
        led0.fold(tr._slim(_fill("act-00000", "m0", ts=TS_AUG2)))
        snap = json.dumps({"at": time.time() - 2 * tr._SNAP_MAX_AGE_S,
                           "complete": True, "last": "",
                           "gz": tr._pack_rows(led0.to_rows())})
        monkeypatch.setattr(tr, "_save_snapshot",
                            lambda *a, **k: asyncio.sleep(0))
        pool = _GrindPool(_table(2), snap=snap)
        led = asyncio.run(tr._hydrate_all(pool))
        assert led.rows == 6 and led.entries["m0"]["fills"] == 2
        (q, args), = pool.queries
        assert args[0] == ""

    def test_a_row_list_under_the_key_forces_a_grind(self, monkeypatch):
        rows = [tr._slim(_fill(f"a{i}")) for i in range(20)]
        snap = json.dumps({"at": time.time(), "complete": True, "last": "",
                           "gz": tr._pack_rows(rows)})
        monkeypatch.setattr(tr, "_save_snapshot",
                            lambda *a, **k: asyncio.sleep(0))
        pool = _GrindPool(_table(2, as_str=False), snap=snap)
        led = asyncio.run(tr._hydrate_all(pool))
        assert len(pool.queries) == 1 and led.rows == 6

    def test_two_grinds_cannot_fold_into_one_archive(self, monkeypatch):
        monkeypatch.setitem(tr._hydrate_progress, "running", True)
        with pytest.raises(RuntimeError):
            asyncio.run(tr._hydrate_all(_GrindPool(_table(1))))

    def test_the_retry_loop_waits_out_a_running_grind_quietly(
            self, monkeypatch, caplog):
        """The single-flight guard above is right; the retry loop used
        to trip it every 15 s for as long as a grind ran and log each
        refusal as a full traceback. While a grind is running the loop
        must not call _hydrate_all at all, and must call it once the
        grind is over."""
        from sportsassets import db

        calls: list = []
        sleeps: list = []

        class _Fast:
            """asyncio with a sleep that yields instead of waiting."""

            def __getattr__(self, name):
                return getattr(asyncio, name)

            async def sleep(self, s):
                sleeps.append(s)
                if len(sleeps) == 4:
                    tr._hydrate_progress["running"] = False
                await asyncio.sleep(0)

        async def _hydrate(pool):
            calls.append(tr._hydrate_progress["running"])
            led = tr._ArchiveLedgers()
            led.fold(tr._slim(_fill("a")))
            return led

        async def _pool():
            return object()

        monkeypatch.setattr(tr, "asyncio", _Fast())
        monkeypatch.setattr(tr, "_hydrate_all", _hydrate)
        monkeypatch.setattr(db, "get_pool", _pool)
        monkeypatch.setattr(tr, "_hydrate_task", None)
        monkeypatch.setitem(tr._archive_cache, "data", None)
        monkeypatch.setitem(tr._hydrate_progress, "running", True)

        async def run():
            tr._ensure_hydrate_retry()
            await asyncio.wait_for(tr._hydrate_task, timeout=5)

        with caplog.at_level("DEBUG"):
            asyncio.run(run())
        assert calls == [False], "no hydrate attempt while a grind runs"
        assert sleeps == [15, 15, 15, 15], "three quiet ticks, then one grind"
        assert tr._archive_cache["data"].rows == 1
        assert not [r for r in caplog.records
                    if "retry failed" in r.getMessage()]
        assert not [r for r in caplog.records if r.exc_info]

    def test_a_refresh_leaves_a_running_grind_alone(self, monkeypatch):
        from sportsassets import db

        calls = []

        async def _hydrate(pool):
            calls.append(pool)
            return tr._ArchiveLedgers()

        class Pool:
            async def execute(self, *a):
                pass

            async def executemany(self, *a):
                pass

        async def _pool():
            return Pool()

        monkeypatch.setattr(db, "get_pool", _pool)
        monkeypatch.setattr(tr, "_hydrate_all", _hydrate)
        monkeypatch.setattr(tr, "_archive_ready", True)
        monkeypatch.setattr(tr, "_archived_ids", set())
        monkeypatch.setattr(tr, "_hydrate_task", None)
        monkeypatch.setitem(tr._archive_cache, "data", None)
        monkeypatch.setitem(tr._hydrate_progress, "running", True)

        async def run():
            out = await tr._archive_and_union([_fill("w1")])
            assert out is None and calls == []
            assert tr._hydrate_task is not None
            tr._hydrate_task.cancel()

        asyncio.run(run())

    def test_the_checkpoint_prunes_before_it_serialises(self):
        src = inspect.getsource(tr._hydrate_all)
        assert src.count("led.prune_ids(time.time())") == 2
        assert "led.to_rows()" in src
        assert "await asyncio.to_thread(_parse)" in src
        assert "led.fold_many(await asyncio.to_thread(_parse))" in src
