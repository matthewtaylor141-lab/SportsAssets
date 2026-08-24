"""Path A2 — new-exchange fills decoded from a receipt's STANDARD events
(the proprietary 0xd543adfd… data layout stays unknown by design)."""

from sportsassets.ingestion.chain import (
    ERC20_TRANSFER_TOPIC,
    FILL_V3_TOPIC,
    TRANSFER_SINGLE_TOPIC,
    decode_fill_v3_receipt,
    v3_owner_candidates,
)

WHALE = "0xf705fa045201391d9632b7f3cde06a5e24453ca7"
OTHER = "0x1ffb493b0042ded0fc2871f4a13590e59d830554"
EXCH = "0xe111180000d2663c0091e4f400237545b87b996b"


def _t(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _w(v: int) -> str:
    return f"{v:064x}"


def _ts(frm, to, tid, val, tx="0xabc", blk="0x1000"):
    return {"topics": [TRANSFER_SINGLE_TOPIC, _t(EXCH), _t(frm), _t(to)],
            "data": "0x" + _w(tid) + _w(val),
            "transactionHash": tx, "blockNumber": blk}


def _erc20(frm, to, val, tx="0xabc", blk="0x1000"):
    return {"topics": [ERC20_TRANSFER_TOPIC, _t(frm), _t(to)],
            "data": "0x" + _w(val),
            "transactionHash": tx, "blockNumber": blk}


def test_owner_candidates_extracts_topic_addresses():
    lg = {"topics": [FILL_V3_TOPIC, _t(OTHER), _t(EXCH), _t(WHALE)]}
    assert WHALE in v3_owner_candidates(lg)


def test_buy_decoded_from_standard_legs():
    # whale pays 55 USDC, receives 100 shares of token 777 → BUY @ 0.55
    logs = [_ts(EXCH, WHALE, 777, 100_000_000),
            _erc20(WHALE, EXCH, 55_000_000)]
    f = decode_fill_v3_receipt(logs, WHALE)
    assert f and f.side == "BUY" and f.token_id == "777"
    assert f.size == 100.0 and abs(f.price - 0.55) < 1e-9


def test_sell_decoded_from_standard_legs():
    logs = [_ts(WHALE, EXCH, 888, 40_000_000),
            _erc20(EXCH, WHALE, 26_000_000)]
    f = decode_fill_v3_receipt(logs, WHALE)
    assert f and f.side == "SELL" and f.token_id == "888"
    assert f.size == 40.0 and abs(f.price - 0.65) < 1e-9


def test_multi_token_bundle_refuses():
    logs = [_ts(EXCH, WHALE, 777, 10_000_000),
            _ts(EXCH, WHALE, 999, 10_000_000),
            _erc20(WHALE, EXCH, 9_000_000)]
    assert decode_fill_v3_receipt(logs, WHALE) is None


def test_other_wallets_legs_ignored():
    logs = [_ts(EXCH, OTHER, 555, 50_000_000),
            _erc20(OTHER, EXCH, 20_000_000),
            _ts(EXCH, WHALE, 777, 100_000_000),
            _erc20(WHALE, EXCH, 55_000_000)]
    f = decode_fill_v3_receipt(logs, WHALE)
    assert f and f.token_id == "777" and abs(f.price - 0.55) < 1e-9


def test_insane_price_refuses():
    logs = [_ts(EXCH, WHALE, 777, 1_000_000),
            _erc20(WHALE, EXCH, 2_000_000)]  # price 2.0 — impossible
    assert decode_fill_v3_receipt(logs, WHALE) is None
