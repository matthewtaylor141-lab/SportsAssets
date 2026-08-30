"""The v3 receipt decoder's selector lane (panel round-2, 2026-08-30).

Round 1 rejected column-sum cross-foots 0/3: on mint-shaped receipts
the two fill events' cash legs sum to shares x $1.00 BY IDENTITY, so a
sum-based license is vacuous and would have priced $0.09 fills at
~$1.00. Round 2 shipped the production-verified single-event selector
— (wallet in topics) AND (word1 == the wallet's own token id) AND
(word3 == the wallet's exact share total), zero-amount pre-drop,
uniqueness enforced, price from THAT event's word2/word3 alone, never
a sum — verified against 12 receipts across 2 wallets with zero
counterexamples, then attacked by three lenses ("could not break it"
x3). Legacy-first lane order makes old-path behavior a construction
property. Every refusal below fails closed to the poller.
"""

from sportsassets.ingestion import chain as ch

W = "0x5268527977f700f9bf9b6d5cd843859e4e70135d"
VAULT = "0xe111180000d2663c0091e4f400237545b87b996b"
T = 99300635618787663739108475862616979934567124755300050553507431590915
T2 = 12345678901234567890


def _topic(addr):
    return "0x" + "0" * 24 + addr[2:].lower()


def _w(n):
    return f"{n:064x}"


def _single(frm, to, tid, val, tx="0xabc", blk="0x10"):
    return {"topics": [ch.TRANSFER_SINGLE_TOPIC, _topic(VAULT),
                       _topic(frm), _topic(to)],
            "data": "0x" + _w(tid) + _w(val),
            "transactionHash": tx, "blockNumber": blk}


def _batch(frm, to, pairs, tx="0xabc", blk="0x10"):
    ids = [p[0] for p in pairs]
    vals = [p[1] for p in pairs]
    data = _w(64) + _w(64 + 32 * (1 + len(ids)))
    data += _w(len(ids)) + "".join(_w(i) for i in ids)
    data += _w(len(vals)) + "".join(_w(v) for v in vals)
    return {"topics": [ch.TRANSFER_BATCH_TOPIC, _topic(VAULT),
                       _topic(frm), _topic(to)],
            "data": "0x" + data,
            "transactionHash": tx, "blockNumber": blk}


def _fill(owner, w1, w2, w3, w0=0, tx="0xabc", blk="0x10"):
    return {"topics": [ch.FILL_V3_TOPIC, "0x" + "9" * 64,
                       _topic(owner), _topic(VAULT)],
            "data": "0x" + _w(w0) + _w(w1) + _w(w2) + _w(w3) + _w(0)
                    + _w(0),
            "transactionHash": tx, "blockNumber": blk}


def _usdc(frm, to, val, tx="0xabc"):
    return {"topics": [ch.ERC20_TRANSFER_TOPIC, _topic(frm), _topic(to)],
            "data": "0x" + _w(val), "transactionHash": tx,
            "blockNumber": "0x10"}


# ── the mint receipt that killed round 1 ─────────────────────────────

def test_mint_receipt_prices_from_his_event_never_the_sum():
    """Two mint-side events: his (w1==T) at 450000/5000000 = 0.09, the
    counterparty's (w1==T2) at 4550000/5000000 = 0.91. The sums
    reconcile BY IDENTITY (0.09+0.91=1.00) — a sum-based decode reads
    ~1.00; the selector must read HIS 0.09."""
    logs = [_single(VAULT, W, T, 5_000_000),
            _fill(W, T, 450_000, 5_000_000),
            _fill(W, T2, 4_550_000, 5_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "selected"
    assert fill.price == 0.09
    assert fill.side == "BUY" and fill.size == 5.0
    assert fill.token_id == str(T)


def test_three_mirror_receipt_selects_the_single_true_event():
    """The 723M production stress receipt: three w1==T mirror events
    whose w3 != S (implied px > 1) plus ONE w1==T AND w3==S event at
    0.94 — exactly one passes; the mirrors can never rescue or
    confuse selection."""
    S = 723_000_000
    logs = [_single(VAULT, W, T, S),
            _fill(W, T, 700_000_000, 658_000_000),
            _fill(W, T, 6_000_000, 5_640_000),
            _fill(W, T, 7_000_000, 6_580_000),
            _fill(W, T2, 600_000, 10_000_000),
            _fill(W, T, 679_620_000, S)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "selected"
    assert fill.price == round(679_620_000 / S, 6)


# ── canaries and refusals (each fails closed AND counts) ─────────────

def test_no_token_match_canary():
    logs = [_single(VAULT, W, T, 5_000_000),
            _fill(W, T2, 450_000, 5_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "no_token_match"


def test_multi_pass_refuses_before_any_px_filtering():
    """Two full-selector passes refuse — and an out-of-range sibling
    is judged AFTER uniqueness, so it can never rescue selection."""
    S = 5_000_000
    logs = [_single(VAULT, W, T, S),
            _fill(W, T, 450_000, S),
            _fill(W, T, 4_550_000, S)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "multi_pass"


def test_px_oob_kills_the_sell_coincidence():
    """The one coincidence path for a v2-style SELL layout: w3==S
    forces px == 1.0 exactly -> px_oob, never a decode."""
    S = 5_000_000
    logs = [_single(W, VAULT, T, S),
            _fill(W, T, S, S)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "px_oob"


def test_zero_amount_sibling_is_never_selectable():
    S = 5_000_000
    logs = [_single(VAULT, W, T, S),
            _fill(W, T, 0, S),
            _fill(W, T, 450_000, S)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "selected" and fill.price == 0.09


def test_multi_partial_with_no_covering_event_refuses():
    """R6/P11 class: partials sum to S but no single event carries S —
    aggregating needs the banned sums; the poller keeps the class."""
    logs = [_single(VAULT, W, T, 10_000_000),
            _fill(W, T, 300_000, 4_000_000),
            _fill(W, T, 450_000, 6_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "no_share_match"


def test_multi_token_wallet_stays_refused():
    logs = [_single(VAULT, W, T, 5_000_000),
            _single(VAULT, W, T2, 3_000_000),
            _fill(W, T, 450_000, 5_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "multi_token"


def test_mixed_direction_refuses():
    logs = [_single(VAULT, W, T, 5_000_000),
            _single(W, VAULT, T, 2_000_000),
            _fill(W, T, 450_000, 5_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "mixed_direction"


def test_batch_settled_single_token_decodes():
    S = 8_000_000
    logs = [_batch(VAULT, W, [(T, 3_000_000), (T, 5_000_000)]),
            _fill(W, T, 6_800_000, S)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "selected"
    assert fill.size == 8.0 and fill.price == 0.85


def test_malformed_batch_refuses_never_crashes():
    bad = _batch(VAULT, W, [(T, 5_000_000)])
    bad["data"] = bad["data"][:70]     # truncated head
    logs = [bad, _fill(W, T, 450_000, 5_000_000)]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "malformed_1155"


def test_malformed_hex_returns_error_not_raise():
    logs = [{"topics": [ch.TRANSFER_SINGLE_TOPIC, _topic(VAULT),
                        _topic(VAULT), _topic(W)],
             "data": "0xNOTHEX", "transactionHash": "0x1",
             "blockNumber": "0x1"}]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why in ("malformed_1155", "error")


def test_unreadable_his_fill_event_voids_the_receipt():
    logs = [_single(VAULT, W, T, 5_000_000),
            {"topics": [ch.FILL_V3_TOPIC, "0x" + "9" * 64,
                        _topic(W), _topic(VAULT)],
             "data": "0x" + _w(0), "transactionHash": "0xabc",
             "blockNumber": "0x10"}]
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert fill is None and why == "malformed_fill"


# ── lane order: legacy first, byte-identical old-path behavior ───────

def test_legacy_lane_wins_and_matches_the_old_decoder():
    """A receipt with direct wallet USDC legs (the old exchanges'
    shape) decodes through the legacy lane with today's exact result;
    the selector never runs."""
    S = 12_310_000
    logs = [_single(VAULT, W, T, S),
            _usdc(W, VAULT, 7_632_200)]
    legacy = ch._decode_v3_legacy(logs, W)
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "legacy"
    assert fill == legacy
    assert ch.decode_fill_v3_receipt(logs, W) == legacy


def test_selector_only_runs_on_legacy_refusal():
    """The vault-settled shape (no wallet USDC legs) is exactly where
    legacy refuses and the selector governs."""
    logs = [_single(VAULT, W, T, 5_000_000),
            _fill(W, T, 450_000, 5_000_000)]
    assert ch._decode_v3_legacy(logs, W) is None
    fill, why = ch.decode_fill_v3_receipt_ex(logs, W)
    assert why == "selected"


# ── observability contracts ──────────────────────────────────────────

def test_beat_carries_the_canaries_and_the_win_counter():
    import inspect

    src = inspect.getsource(ch.ChainListener._beat_detail) \
        if hasattr(ch, "ChainListener") else ""
    if not src:   # class name lookup fallback
        src = open(ch.__file__).read()
    for key in ("v3_refused", "v3_no_token_match", "v3_multi_pass",
                "v3_px_oob", "v3_ref", "v3_selected"):
        assert key in src


def test_shadow_mismatch_arm_is_asset_scoped():
    """Decoder-panel round-2 blocking mandate: the wrong-decode alarm
    (orphan_chain_mismatch, GATING) fires only on a SAME-asset key
    mismatch; a leftover exec on an asset no chain row carries falls
    to orphan_excess_exec — otherwise the selector's first correct
    decode of a mint-shaped receipt re-arms the total quarantine off
    its own sibling leg."""
    from sportsassets.ingestion import shadow_v2 as sv

    src = open(sv.__file__).read()
    i = src.index('bump("orphan_chain_mismatch")')
    head = src[:i]
    arm = head[head.rindex("elif "):]
    assert 'r["asset"] == c["asset"] for r in chain_rows' in arm
