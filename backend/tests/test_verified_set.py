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

MEMBERSHIP MOVED 2026-08-25 (owner-granted) when the first
merge-inclusive whale P&L showed the roster was inverted: rn1
(+$222,038) and ferrari (+$217,159) had been cut, swisstony
(-$187,613) was being copied. The unity property below is what this
file protects, not any particular membership — so these fixtures name
whoever is currently certified and will move again when the numbers
do."""

import inspect

from sportsassets import live_executor as le


def test_the_verified_set_names_the_certified_whales():
    """swisstony + homerunhazard REINSTATED 2026-08-27 (owner order).
    The 2026-08-25 removals were graded on the merge-only instrument
    since proven blind to REDEEM exits; the venue's own ledger reads
    swisstony +$23.6M lifetime / +$1.36M 30d and homerunhazard +$2.32M
    / +$869k 30d."""
    assert le.VERIFIED_PROFITABLE_DEFAULT == (
        "0x076daa87,rn1,ferrarichampions2026,swisstony,homerunhazard")


def test_the_cut_whale_is_out_of_both_gates():
    w = le._W2C33
    assert w not in le._whale_set("LIVE_VERIFIED_WHALES"), w
    assert w not in le._whale_set("LIVE_PREMAP_WHALES"), w


def test_the_restored_whales_are_in_both_gates():
    """A restore that leaves a whale out of an allowlist is not a
    restore — that is precisely the 2,897-rejection failure this file
    exists for. All four restorations (rn1/ferrari 2026-08-25,
    swisstony/homerunhazard 2026-08-27) must hold in BOTH gates."""
    for w in ("rn1", "ferrarichampions2026", "swisstony",
              "homerunhazard"):
        assert w in le._whale_set("LIVE_VERIFIED_WHALES"), w
        assert w in le._whale_set("LIVE_PREMAP_WHALES"), w


def test_the_cut_whale_is_in_neither_gate():
    assert le._W2C33 not in le._whale_set("LIVE_VERIFIED_WHALES")
    assert le._W2C33 not in le._whale_set("LIVE_PREMAP_WHALES")


def test_both_gates_default_to_the_same_membership(monkeypatch):
    monkeypatch.delenv("LIVE_VERIFIED_WHALES", raising=False)
    monkeypatch.delenv("LIVE_PREMAP_WHALES", raising=False)
    assert (le._whale_set("LIVE_VERIFIED_WHALES")
            == le._whale_set("LIVE_PREMAP_WHALES")), \
        "a whale certified for one gate must not be refused by the other"


def test_each_gate_still_takes_its_own_override(monkeypatch):
    """Shared DEFAULT, independent OVERRIDE: an asymmetric change stays
    possible on purpose — it just has to be made on purpose."""
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "kch123")
    monkeypatch.delenv("LIVE_VERIFIED_WHALES", raising=False)
    assert le._whale_set("LIVE_PREMAP_WHALES") == {"kch123"}
    assert "rn1" in le._whale_set("LIVE_VERIFIED_WHALES")


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
