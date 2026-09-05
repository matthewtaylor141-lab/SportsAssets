"""The ten extreme merges rank on ROUNDED pnl with ties in MERGE ORDER,
and until now nothing pinned that.

ebc24ce (2026-09-05) replaced the per-merge dict list that finish()
sorted — 400,000 dicts at rn1's 806,085-fill book, +160 MB inside the
2 GiB workers process that was being OOM-killed — with two bounded
five-entry heaps. The ten rows they publish are meant to be exactly what

    sorted(rows, key=pnl)[:5] + sorted(rows, key=-pnl)[:5]

produced over every merge of the walk (for finite pnl): a STABLE sort on the rounded pnl,
so equal-pnl merges keep the order the walk saw them in, in BOTH halves.
The suite passed that change with no assertion on tie order at all. A
heap keyed on the raw pnl, a flipped sign on the sequence number, or a
finish() that reverses the ascending list to get the descending one all
put DIFFERENT merges in the report — and all would have passed. These
pin the tie-break, with the old finish() inlined verbatim as the oracle.
"""

import random

import pytest

from sportsassets.analytics.merge_pnl import replay

# All books here buy 100 of leg 0 at 0.40 and close it by buying 100 of
# leg 1, so a merge's pnl is 100 * (0.60 - complement_price) and the
# price that lands on a wanted pnl is 0.60 - pnl / 100.
ENTRY, SIZE = 0.40, 100.0


def _price_for(pnl):
    return 0.60 - pnl / 100.0


# Six complement prices whose RAW pnls differ (29.997 .. 30.003) but all
# round to 30.00 — the value the row shows and the sort keyed on. Laid
# out so neither ascending nor descending raw order is merge order: a
# heap ranking on the unrounded float would reorder these.
_NEAR_30 = [0.30, 0.30003, 0.29997, 0.30001, 0.29999, 0.30002]


def _fill(cid, idx, size, price):
    return {"condition_id": cid, "outcome_index": idx, "side": "BUY",
            "size": size, "price": price}


def _book(merges):
    """One condition per merge. `merges` is [(cid, complement_price)] in
    the order the complement buys — the merges — happen.

    Entries go in first, in REVERSED order, so that entry order, lexical
    id order and merge order are three different orders: 'first-seen'
    has to mean the merge, not the entry and not the id.
    """
    entries = [_fill(c, 0, SIZE, ENTRY) for c, _ in reversed(merges)]
    exits = [_fill(c, 1, SIZE, p) for c, p in merges]
    return entries + exits


def _rows_in_merge_order(merges):
    """The row each merge produces, in merge order, from the module's
    OWN arithmetic rather than a copy of it here.

    Conditions are independent in the stepper, and a book with a single
    merge puts that one row in both halves, so replay()['rows'][0] on a
    one-merge book is the exact dict the full walk appends for it.
    """
    return [replay(_book([m]))["rows"][0] for m in merges]


def _old_finish_rows(rows):
    """finish() as it stood before ebc24ce, verbatim, over the list of
    every merge's row in merge order. The oracle."""
    return sorted(rows, key=lambda r: r["pnl"])[:5] + \
        sorted(rows, key=lambda r: -r["pnl"])[:5]


def _ids(rows):
    return [r["condition_id"] for r in rows]


class TestSixMergesWithOneRoundedPnl:
    # Ids deliberately not in lexical order, so a tie broken on the
    # condition id would give a different answer from merge order.
    IDS = ["q", "c", "x", "a", "m", "f"]

    def test_the_first_five_seen_are_kept_in_both_halves(self):
        merges = list(zip(self.IDS, _NEAR_30))
        r = replay(_book(merges))
        assert r["n_merges"] == 6
        assert [row["pnl"] for row in r["rows"]] == [30.0] * 10, \
            "the six must tie on the rounded pnl for this to test ties"
        # Reverting to raw-keyed or latest-first ties fails HERE: the
        # kept five must be q c x a m in that order, in both halves.
        assert _ids(r["rows"][:5]) == ["q", "c", "x", "a", "m"]
        assert _ids(r["rows"][5:]) == ["q", "c", "x", "a", "m"]
        assert "f" not in _ids(r["rows"]), "the sixth merge is dropped"

    def test_the_raw_pnls_really_do_differ(self):
        """Guards the test above: if these six ever rounded to different
        values, or their raw order happened to equal merge order, the
        assertion on merge order would stop proving the key is rounded."""
        raw = [SIZE * (1.0 - ENTRY - p) for p in _NEAR_30]
        assert len({round(x, 2) for x in raw}) == 1
        assert len(set(raw)) == 6
        # The property the test above relies on is about the FIRST FIVE:
        # a heap keyed on the raw float must pick a different five, in
        # each direction, than merge order does (review 2026-09-05).
        ids = TestSixMergesWithOneRoundedPnl.IDS
        by_raw_asc = [i for _, i in sorted(zip(raw, ids))]
        by_raw_desc = [i for _, i in sorted(zip(raw, ids), key=lambda t: -t[0])]
        assert by_raw_asc[:5] != ids[:5]
        assert by_raw_desc[:5] != ids[:5]

    @pytest.mark.parametrize("n", [6, 12, 37])
    def test_many_ties_still_keep_the_first_five_seen(self, n):
        """Six ties exercise exactly one heap eviction. Longer runs push
        the evict-latest rule through many pushpops, where a wrong sign
        on the sequence number would let a later merge displace an
        earlier equal one."""
        # (i * 5) % n is a permutation for these n (5 is coprime to 6, 12
        # and 37) and 5 is not 1 modulo any of them, so id order != merge
        # order without a random source. (7 was 1 mod 6: for n=6 the ids
        # came out in merge order and proved nothing -- review 2026-09-05.)
        ids = [f"m{(i * 5) % n:02d}" for i in range(n)]
        assert ids != sorted(ids), "id order must differ from merge order"
        merges = [(c, _NEAR_30[i % 6]) for i, c in enumerate(ids)]
        r = replay(_book(merges))
        assert r["n_merges"] == n
        assert _ids(r["rows"]) == ids[:5] + ids[:5]


class TestEqualAndDistinctValuesMixed:
    """The cut falls INSIDE a tie group on both sides: three merges at
    +10 compete for one worst-list seat, five at +30 for four best-list
    seats. Which of the equals get the seats is the whole question."""

    # (id, pnl) in merge order. Ids again not in lexical order.
    MERGES = [("h", 30), ("t", 10), ("b", 30), ("w", -20), ("e", 10),
              ("p", 30), ("n", -20), ("j", 5), ("s", 10), ("d", 30),
              ("y", -20), ("a", 50), ("g", 30)]

    def _replay(self):
        return replay(_book([(c, _price_for(p)) for c, p in self.MERGES]))

    def test_worst_is_pnl_ascending_then_first_seen(self):
        r = self._replay()
        assert r["n_merges"] == 13
        assert [row["pnl"] for row in r["rows"][:5]] == [-20, -20, -20, 5, 10]
        # The one +10 seat goes to t (merge 2), not e (5) or s (9).
        assert _ids(r["rows"][:5]) == ["w", "n", "y", "j", "t"]

    def test_best_is_pnl_descending_then_first_seen(self):
        r = self._replay()
        assert [row["pnl"] for row in r["rows"][5:]] == [50, 30, 30, 30, 30]
        # The four +30 seats go to h b p d (merges 1, 3, 6, 10); g (13)
        # is the +30 that misses. Reversing the ascending order to build
        # this half would seat g and drop h — that is what fails here.
        assert _ids(r["rows"][5:]) == ["a", "h", "b", "p", "d"]

    def test_it_matches_the_old_sort(self):
        merges = [(c, _price_for(p)) for c, p in self.MERGES]
        assert replay(_book(merges))["rows"] == \
            _old_finish_rows(_rows_in_merge_order(merges))


class TestFewerThanFiveMerges:
    @pytest.mark.parametrize("n", [1, 2, 3, 4])
    def test_all_equal_merges_are_all_kept_in_merge_order(self, n):
        ids = ["v", "k", "r", "b"][:n]
        merges = list(zip(ids, _NEAR_30))
        r = replay(_book(merges))
        assert r["n_merges"] == n
        assert len(r["rows"]) == 2 * n, "nothing may be dropped below five"
        assert _ids(r["rows"]) == ids + ids

    def test_distinct_values_are_all_kept_sorted_each_way(self):
        merges = [("v", _price_for(10)), ("k", _price_for(-20)),
                  ("r", _price_for(30)), ("b", _price_for(5))]
        r = replay(_book(merges))
        assert _ids(r["rows"]) == ["k", "b", "v", "r", "r", "v", "b", "k"]

    def test_no_merges_means_no_rows(self):
        assert replay(_book([]))["rows"] == []


class TestOneMergeCanSitInBothHalves:
    """Eight merges, six of them at +10 around one +50 and one -20. Four
    of the +10s belong in BOTH lists, and it must be the SAME four — the
    first four seen — on each side. The other two sit in neither."""

    MERGES = [("u", 10), ("z", 50), ("o", 10), ("l", -20), ("i", 10),
              ("c", 10), ("x", 10), ("f", 10)]

    def test_it_matches_the_old_sort_exactly(self):
        merges = [(c, _price_for(p)) for c, p in self.MERGES]
        got = replay(_book(merges))["rows"]
        assert got == _old_finish_rows(_rows_in_merge_order(merges))

    def test_the_same_first_four_ties_appear_on_both_sides(self):
        merges = [(c, _price_for(p)) for c, p in self.MERGES]
        got = replay(_book(merges))["rows"]
        assert _ids(got[:5]) == ["l", "u", "o", "i", "c"]
        assert _ids(got[5:]) == ["z", "u", "o", "i", "c"]
        assert "x" not in _ids(got) and "f" not in _ids(got)
        # The trap the old key=-pnl avoided and a reversed ascending
        # list walks into: best would read z f x c i.
        assert _ids(got[5:]) != ["z", "f", "x", "c", "i"]


class TestSeededBooksAgreeWithTheOldSort:
    """Forty books of 6..30 merges drawn from a seven-value alphabet, so
    every book is mostly ties and every heap eviction is a tie-break.
    Fixed seed: the same forty books every run."""

    def test_forty_seeded_books(self):
        rng = random.Random(20260905)
        alphabet = [-20, -20, 5, 10, 30, 30, 50]
        for trial in range(40):
            n = rng.randint(6, 30)
            ids = rng.sample(range(100000, 999999), n)
            merges = [(f"c{cid}", _price_for(rng.choice(alphabet)))
                      for cid in ids]
            got = replay(_book(merges))["rows"]
            want = _old_finish_rows(_rows_in_merge_order(merges))
            assert got == want, f"trial {trial}: {merges}"
