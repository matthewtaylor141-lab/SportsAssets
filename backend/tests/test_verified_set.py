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


def _code(fn) -> str:
    """Source with comment lines stripped. Both gates run in
    _mapping_admitted (mirror P1 step 1); maybe_execute calls it and
    keeps a prose summary that repeats both _whale_set calls, so a pin
    read off raw source would outlive the code it names."""
    return "\n".join(ln for ln in inspect.getsource(fn).splitlines()
                     if not ln.lstrip().startswith("#"))


def test_neither_gate_carries_a_second_hard_coded_roster():
    """The drift was two literals for one decision. Pin that the gates
    read the shared helper rather than re-listing whales inline -- in
    the CODE of the function that runs them, and that the copy lane
    still runs them there."""
    gate = _code(le._mapping_admitted)
    assert '_whale_set("LIVE_VERIFIED_WHALES")' in gate
    assert '_whale_set("LIVE_PREMAP_WHALES")' in gate
    assert "await _mapping_admitted(" in _code(le.maybe_execute), \
        "the copy lane must run its gates through the shared helper"
    for fn in (le._mapping_admitted, le.maybe_execute):
        assert '"homerunhazard,0x076daa87"' not in inspect.getsource(fn)


# ── DB roster override (owner order 2026-08-29: "update the variables") ─
# A stale Render env LIVE_VERIFIED_WHALES silently overrode the owner's
# reinstate order for two days (homerunhazard: 724 rejections, $0
# deployed on a +2.57%-at-95% whale). The stored roster now beats the
# env; these pins hold the override's semantics.

import asyncio
import json as _json
import time as _time


class _RosterPool:
    def __init__(self, stored=None, boom=False):
        self.stored = stored
        self.boom = boom

    async def fetchval(self, sql, *a, timeout=None):
        if self.boom:
            raise RuntimeError("db blip")
        return self.stored


def _fresh_roster_state():
    le._roster_override = None
    le._roster_read_at = 0.0


def test_db_roster_beats_the_env(monkeypatch):
    _fresh_roster_state()
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "rn1")  # the stale env
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(
            ["rn1", "homerunhazard", "swisstony"]))))
    got = le._whale_set("LIVE_VERIFIED_WHALES")
    assert got == {"rn1", "homerunhazard", "swisstony"}, \
        "the owner's stored roster must beat the env"
    _fresh_roster_state()


def test_no_stored_roster_falls_to_env_then_default(monkeypatch):
    _fresh_roster_state()
    asyncio.run(le.refresh_whale_overrides(_RosterPool(stored=None)))
    monkeypatch.setenv("LIVE_VERIFIED_WHALES", "rn1")
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}
    monkeypatch.delenv("LIVE_VERIFIED_WHALES")
    assert le._whale_set("LIVE_VERIFIED_WHALES") == set(
        le.VERIFIED_PROFITABLE_DEFAULT.split(","))
    _fresh_roster_state()


def test_comma_string_and_case_are_normalized():
    _fresh_roster_state()
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps("RN1, HomeRunHazard"))))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1", "homerunhazard"}
    _fresh_roster_state()


def test_read_failure_keeps_the_last_adopted_roster():
    _fresh_roster_state()
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(["rn1"]))))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}
    le._roster_read_at = 0.0            # force a re-read attempt
    asyncio.run(le.refresh_whale_overrides(_RosterPool(boom=True)))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}, \
        "a DB blip is not a roster decision"
    _fresh_roster_state()


def test_ttl_skips_the_read_inside_the_window():
    _fresh_roster_state()
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(["rn1"]))))
    # a different stored value inside the TTL window must NOT be adopted
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(["swisstony"]))))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}
    le._roster_read_at = _time.time() - le._ROSTER_TTL_S - 1
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(["swisstony"]))))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"swisstony"}
    _fresh_roster_state()


def test_other_env_sets_are_untouched_by_the_override(monkeypatch):
    _fresh_roster_state()
    asyncio.run(le.refresh_whale_overrides(
        _RosterPool(stored=_json.dumps(["rn1"]))))
    monkeypatch.setenv("LIVE_PREMAP_WHALES", "homerunhazard")
    assert le._whale_set("LIVE_PREMAP_WHALES") == {"homerunhazard"}, \
        "the DB override applies to LIVE_VERIFIED_WHALES only"
    _fresh_roster_state()


def test_boot_read_failure_falls_to_env_and_screams(monkeypatch, caplog):
    """Fleet round 49: a fresh worker whose first roster read failed
    fell to the env/default in total silence while GATES (a different
    process) reported 'db'. The fallback itself is unavoidable — a
    roster we cannot read is not a roster — but it must be LOGGED at
    error level and retried, and the TTL must stay unset so the very
    next event retries the read."""
    import logging

    _fresh_roster_state()
    with caplog.at_level(logging.ERROR, logger="sportsassets.live_executor"):
        asyncio.run(le.refresh_whale_overrides(_RosterPool(boom=True)))
    assert le._roster_override is None
    assert le._roster_read_at == 0.0, "failure must not start the TTL"
    assert any("UNREAD at boot" in r.message for r in caplog.records), \
        "the boot fallback must be visible in the logs"
    _fresh_roster_state()
