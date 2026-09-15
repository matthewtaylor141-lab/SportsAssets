"""/trades is one page of 100 with no cursor. Say so when it is full.

The poller fetches `{"user": addr, "limit": 100}` and never follows a
next page. So a whale who trades more than 100 times between two polls
of his wallet loses the overflow PERMANENTLY: the next poll starts from
the newest again and the older rows have fallen off the end. Nothing
downstream can detect it — the trades simply never exist, and every
edge interval we publish would rest on a denominator quietly missing
the busiest minutes.

SIZED BEFORE BUILT, so this is not an alarm dressed as a finding. At
poll_interval_seconds=5.0 a page holds five seconds of flow, so
overflow needs 20 trades/second. rn1 — the busiest book on the roster
at a median 11,514 trades/day — averages 0.13/s. Headroom is about
150x and this has probably never fired.

It is instrumented anyway because the failure mode is silent and
unrecoverable, which is the combination that earns a counter even at
low probability. A full page is not proof of loss (exactly 100 could
be exactly 100), so what is counted is a SUSPICION.

E26 (FILL lane 26, 2026-09-09): the page is his newest 100 whatever
their age, so the full-page line fired on every poll of every whale
with 100 lifetime trades (22 lines in 7 s of hard2/wlog_6m_1734.txt)
and said nothing. The count stays (`_PAGE_FULL`, never logged); the
line that says something moved to the overflow walk: a full page whose
EVERY key is new is followed with `offset` to a row already seen, and
"POLLER page overflow ... the venue may hold older rows this poll did
not reach" fires only when the bound was reached all-new. The pins
that read the log line follow it there (test_e26_poll_overflow.py
holds the walk's own).
"""
import inspect

from sportsassets.ingestion import poller as P


class TestTheSuspicionIsCounted:
    def setup_method(self):
        P._PAGE_FULL.clear()

    def teardown_method(self):
        P._PAGE_FULL.clear()

    def test_a_full_page_is_recorded_per_whale(self):
        P._PAGE_FULL["rn1"] = P._PAGE_FULL.get("rn1", 0) + 1
        assert P.page_full_counts()["rn1"] == 1

    def test_the_counter_is_exported(self):
        assert callable(getattr(P, "page_full_counts", None))

    def test_the_threshold_matches_the_request_limit(self):
        """If the limit is ever raised, the detector must move with it
        or it becomes permanently silent."""
        src = inspect.getsource(P.Poller.poll_wallet)
        assert '"limit": 100' in src
        assert "len(page) >= 100" in src, (
            "the detector threshold and the request limit have drifted "
            "apart; a full page would no longer be noticed")
        # E26: the overflow walk opens on the same threshold, and only
        # when no key of the page is seen
        assert "if len(page) >= 100 and keys and not probe_failed and not any(k in seen for k in keys):" in src
        assert "len(page2) < 100" in src, "the walk stops on a short page at the same limit"

    def test_it_fires_before_the_all_junk_guard(self):
        """The all-junk guard RAISES. A full page of junk is both a
        dead carrier and a possible truncation, and the truncation
        would be lost if the raise came first."""
        src = inspect.getsource(P.Poller.poll_wallet)
        assert src.index("len(page) >= 100") < src.index("bad == len(page)")


class TestItIsHonestAboutWhatItKnows:
    def test_the_log_says_may_not_did(self):
        """Exactly 100 trades in a window is a full page and no loss.
        Claiming loss would be a false alarm every time it is a
        coincidence. E26 moved the line to the overflow walk (the
        window used to open at `len(page) >= 100`, where the warning
        was; the full-page block now only counts): it still says MAY."""
        src = inspect.getsource(P.Poller.poll_wallet)
        i = src.index('"POLLER page overflow for %s')
        window = src[i:i + 600]
        assert "may hold" in window
        assert "did lose" not in window and "lost" not in window
        # the full-page count itself no longer claims anything: no line
        full = src[src.index("        if len(page) >= 100:"):
                   src.index("        if page and bad == len(page):")]
        assert "log." not in full and "_PAGE_FULL[" in full

    def test_the_reasoning_records_the_headroom(self):
        """A counter with no sizing beside it gets read as a live
        problem the first time anyone greps it."""
        src = inspect.getsource(P.Poller.poll_wallet)
        assert "150x" in src or "0.13" in src


class TestItCannotChangeIngestion:
    def test_no_row_is_dropped_or_added_by_the_check(self):
        """It counts and logs. If it could `continue` or `return`, a
        full page would ingest differently from a partial one."""
        src = inspect.getsource(P.Poller.poll_wallet)
        i = src.index("len(page) >= 100")
        block = src[i:src.index("if page and bad == len(page):")]
        for verb in ("return", "continue", "raise", "events."):
            assert verb not in block, (
                f"the full-page check does {verb} — it must only count")

    def test_the_overflow_walk_adds_rows_and_never_ends_the_cycle(self):
        """E26: the walk DOES add rows (that is its purpose: the same
        containment, pre-probe and ingest as the first page's), and it
        may not `return`, `raise` or `continue` -- an unreadable
        overflow page leaves the first page's rows standing and the
        cycle running. The old pin's `events.` clause moved here as
        its opposite."""
        src = inspect.getsource(P.Poller.poll_wallet)
        i = src.index("int(POLL_OVERFLOW_PAGES)")
        block = src[i:src.index("        new = 0\n")]
        assert "events.extend(events2)" in block
        for verb in ("return", "continue", "raise "):
            assert verb not in block, (
                f"the overflow walk does {verb} — it must only add rows and count")
