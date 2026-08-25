"""One certification decision, one list.

Two gates ask the same question — "has TRUEEDGE verified this whale
profitable?" — and each carried its own hard-coded default:

    LIVE_VERIFIED_WHALES  "homerunhazard,0x076daa87,swisstony"
    LIVE_PREMAP_WHALES    "homerunhazard,0x076daa87"

On 2026-08-24 swisstony was certified on TRUEEDGE-FAST, his hold was
lifted, and he was added to the verified set. The premap allowlist was
not updated. He was reported as resumed and could not place a single
order: 2,897 rejections, $0 deployed, refused by a list nobody had
touched. The observable symptom looked like a mapping problem, which
sent the investigation somewhere else entirely.

The two gates still run independently and each still takes its own env
override. What they can no longer do is disagree about who has been
certified.
"""

import inspect

from sportsassets import live_executor as le


def test_the_verified_set_names_the_three_certified_whales():
    assert le.VERIFIED_PROFITABLE_DEFAULT == (
        "homerunhazard,0x076daa87,swisstony")


def test_swisstony_is_in_the_default_set():
    """He is the whale the system is built around; a resume that leaves
    him out of any allowlist is not a resume."""
    assert "swisstony" in le._whale_set("LIVE_VERIFIED_WHALES")
    assert "swisstony" in le._whale_set("LIVE_PREMAP_WHALES")


def test_both_gates_default_to_the_same_membership(monkeypatch):
    monkeypatch.delenv("LIVE_VERIFIED_WHALES", raising=False)
    monkeypatch.delenv("LIVE_PREMAP_WHALES", raising=False)
    assert (le._whale_set("LIVE_VERIFIED_WHALES")
            == le._whale_set("LIVE_PREMAP_WHALES")), \
        "a whale certified for one gate must not be refused by the other"


def test_each_gate_still_takes_its_own_override(monkeypatch):
    """Shared DEFAULT, independent OVERRIDE: an asymmetric change stays
    possible on purpose — it just has to be made on purpose."""
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "homerunhazard")
    monkeypatch.delenv("LIVE_VERIFIED_WHALES", raising=False)
    assert le._whale_set("LIVE_PREMAP_WHALES") == {"homerunhazard"}
    assert "swisstony" in le._whale_set("LIVE_VERIFIED_WHALES")


def test_an_empty_override_disables_that_gate_not_the_other(monkeypatch):
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "")
    monkeypatch.delenv("LIVE_PREMAP_WHALES", raising=False)
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set()
    assert le._whale_set("LIVE_PREMAP_WHALES")


def test_no_cut_whale_is_ever_in_the_verified_default():
    """The TRUEEDGE cuts are negative at their own prices. Unifying the
    lists must never have widened either one to a cut book."""
    verified = le._whale_set("LIVE_VERIFIED_WHALES")
    assert not (verified & {w.lower() for w in le.COPY_CUT_WHALES})


def test_neither_gate_carries_a_second_hard_coded_roster():
    """The drift was two literals for one decision. Pin that the gates
    read the shared helper rather than re-listing whales inline."""
    src = inspect.getsource(le.maybe_execute)
    assert '_whale_set("LIVE_VERIFIED_WHALES")' in src
    assert '_whale_set("LIVE_PREMAP_WHALES")' in src
    assert '"homerunhazard,0x076daa87"' not in src
