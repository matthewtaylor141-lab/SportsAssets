"""The bundle decoder: every fill a wallet has in one settlement receipt.

The single-fill selector refuses whenever a wallet appears more than once
in a receipt -- `multi_token`, `multi_pass`, `mixed_direction` -- and the
venue batches settlements, so those refusals are real fills we decline to
read. These tests pin what the bundle decoder recovers, what it still
refuses, and above all that it stays MEASUREMENT ONLY: no order path may
reach it at this commit.
"""
from __future__ import annotations

import inspect

from sportsassets.ingestion import chain as ch


# ── receipt builders ────────────────────────────────────────────────────
W = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
USDC = 1_000_000            # chain.USDC_DECIMALS


def _topic_for(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:]


def _word(n: int) -> str:
    return f"{n:064x}"


def transfer_single(frm: str, to: str, token: int, value: int,
                    tx: str = "0xfeed", blk: int = 0x10) -> dict:
    return {
        "topics": [ch.TRANSFER_SINGLE_TOPIC, _topic_for(OTHER),
                   _topic_for(frm), _topic_for(to)],
        "data": "0x" + _word(token) + _word(value),
        "transactionHash": tx,
        "blockNumber": hex(blk),
    }


def fill_event(wallet: str, token: int, cash: int, shares: int,
               tx: str = "0xfeed", blk: int = 0x10) -> dict:
    """FILL_V3 with the wallet in topics[1] and (…, token, cash, shares)
    in data words 1..3 — the layout `_decode_v3_selected` reads."""
    return {
        "topics": [ch.FILL_V3_TOPIC, _topic_for(wallet)],
        "data": "0x" + _word(0) + _word(token) + _word(cash) + _word(shares),
        "transactionHash": tx,
        "blockNumber": hex(blk),
    }


def _buy(token: int, shares: int, cash: int) -> list[dict]:
    """One clean BUY leg: shares in, and a fill event that prices it."""
    return [transfer_single(OTHER, W, token, shares),
            fill_event(W, token, cash, shares)]


# ── what it recovers ────────────────────────────────────────────────────
class TestItRecoversWhatTheSelectorRefuses:
    def test_two_tokens_in_one_receipt_are_two_fills(self):
        """`multi_token`: the case the audit comment says kills the lane."""
        logs = _buy(111, 50 * USDC, 20 * USDC) + _buy(222, 30 * USDC, 21 * USDC)

        single, why = ch.decode_fill_v3_receipt_ex(logs, W)
        assert single is None, "the selector is supposed to refuse this"
        assert why in ("multi_token", "no_share_match", "multi_pass"), why

        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert [f.token_id for f in fills] == ["111", "222"]
        assert all(f.side == "BUY" for f in fills)
        assert [f.size for f in fills] == [50.0, 30.0]
        # price = cash/shares, both scaled by the same 1e6, so it is a ratio
        assert fills[0].price == round(20 / 50, 6)
        assert fills[1].price == round(21 / 30, 6)
        assert reasons == {}

    def test_a_buy_and_a_sell_on_different_tokens_both_survive(self):
        """`mixed_direction` across tokens is two fills, not one refusal."""
        logs = (_buy(111, 40 * USDC, 12 * USDC)
                + [transfer_single(W, OTHER, 222, 25 * USDC),
                   fill_event(W, 222, 20 * USDC, 25 * USDC)])
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert {f.token_id: f.side for f in fills} == {"111": "BUY",
                                                       "222": "SELL"}
        assert reasons == {}

    def test_a_single_clean_fill_still_decodes_identically(self):
        """The bundle path must not disagree with the selector where the
        selector works — otherwise arming it later changes live fills."""
        logs = _buy(111, 50 * USDC, 20 * USDC)
        single, why = ch.decode_fill_v3_receipt_ex(logs, W)
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert len(fills) == 1 and reasons == {}
        if single is not None:            # selector agreed: fields must match
            assert fills[0].token_id == single.token_id
            assert fills[0].side == single.side
            assert fills[0].size == single.size
            assert fills[0].price == single.price

    def test_another_wallets_legs_are_not_ours(self):
        logs = _buy(111, 50 * USDC, 20 * USDC) + [
            transfer_single(OTHER, "0x" + "ef" * 20, 999, 5 * USDC),
            fill_event("0x" + "ef" * 20, 999, 2 * USDC, 5 * USDC)]
        fills, _ = ch.decode_fills_v3_bundle(logs, W)
        assert [f.token_id for f in fills] == ["111"]


# ── what it still refuses, per token ────────────────────────────────────
class TestItRefusesPerTokenRatherThanWholesale:
    def test_one_bad_token_does_not_lose_the_good_one(self):
        """The whole point: a refusal is scoped to its own token now."""
        logs = (_buy(111, 50 * USDC, 20 * USDC)
                + [transfer_single(OTHER, W, 222, 30 * USDC)])  # no fill event
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert [f.token_id for f in fills] == ["111"]
        assert reasons == {"no_fill_events": 1}

    def test_both_directions_on_one_token_is_refused_not_netted(self):
        logs = [transfer_single(OTHER, W, 111, 30 * USDC),
                transfer_single(W, OTHER, 111, 10 * USDC),
                fill_event(W, 111, 8 * USDC, 20 * USDC)]
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert fills == []
        assert reasons == {"mixed_direction": 1}

    def test_two_fill_events_claiming_one_tokens_shares_is_undecidable(self):
        logs = [transfer_single(OTHER, W, 111, 50 * USDC),
                fill_event(W, 111, 20 * USDC, 50 * USDC),
                fill_event(W, 111, 25 * USDC, 50 * USDC)]
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert fills == []
        assert reasons == {"multi_pass": 1}

    def test_a_price_outside_zero_to_one_is_refused(self):
        logs = [transfer_single(OTHER, W, 111, 10 * USDC),
                fill_event(W, 111, 40 * USDC, 10 * USDC)]     # price 4.0
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert fills == []
        assert reasons == {"px_oob": 1}

    def test_a_malformed_receipt_refuses_and_never_raises(self):
        logs = [{"topics": [ch.TRANSFER_SINGLE_TOPIC, _topic_for(OTHER),
                            _topic_for(OTHER), _topic_for(W)],
                 "data": "0x00"}]                              # truncated
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert fills == []
        assert reasons == {"malformed_1155": 1}

    def test_no_flow_for_this_wallet_is_empty_not_an_error(self):
        logs = [transfer_single(OTHER, "0x" + "ef" * 20, 999, 5 * USDC)]
        assert ch.decode_fills_v3_bundle(logs, W) == ([], {})

    def test_an_id_seen_only_at_value_zero_is_not_a_fill(self):
        logs = [transfer_single(OTHER, W, 111, 0)]
        fills, reasons = ch.decode_fills_v3_bundle(logs, W)
        assert fills == [] and reasons == {}


# ── it is measurement only ──────────────────────────────────────────────
class TestItIsCountedAndNotTraded:
    def test_the_only_caller_counts_and_places_nothing(self):
        """A decoder that quietly gained an order path is the failure this
        test exists to prevent. The one call site must sit in the refusal
        branch and touch only counters."""
        src = inspect.getsource(ch)
        calls = [ln for ln in src.splitlines()
                 if "decode_fills_v3_bundle(" in ln and "def " not in ln]
        assert len(calls) == 1, f"expected one call site, found {len(calls)}"

        handler = inspect.getsource(ch.ChainListener._handle_v3)
        assert "decode_fills_v3_bundle" in handler
        after = handler.split("decode_fills_v3_bundle", 1)[1]
        # everything the measurement does, up to the end of its guard
        block = after.split("except Exception", 1)[0]
        for forbidden in ("maybe_execute", "execute_copy", "emit", "claim(",
                          "_record_fill", "await self._save", "INSERT"):
            assert forbidden not in block, \
                f"the bundle measurement reaches {forbidden!r}"

    def test_the_measurement_cannot_raise_into_the_listener(self):
        handler = inspect.getsource(ch.ChainListener._handle_v3)
        lines = handler.splitlines()
        idx = next(i for i, ln in enumerate(lines)
                   if "decode_fills_v3_bundle" in ln)
        # The last statement opened before the call's own line must be
        # `try:`. A comment block sits between them, so scan backwards
        # past comments and blanks rather than indexing a fixed offset.
        prior = [ln.strip() for ln in lines[:idx]
                 if ln.strip() and not ln.strip().startswith("#")]
        assert prior and prior[-1] == "try:", \
            f"the bundle call is not inside a try block (preceded by {prior[-1]!r})"
        assert "v3_bundle_err" in handler, "no counter for a decoder fault"
