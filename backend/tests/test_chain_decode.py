"""OrderFilled decoding (Path A) against synthetic logs."""

import pytest

from sportsassets.ingestion.chain import decode_order_filled, order_filled_topic

WHALE = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20
TOKEN_ID = 987654321


def _word(v: int) -> str:
    return format(v, "064x")


def _log(maker, taker, maker_asset, taker_asset, maker_amt, taker_amt, fee=0):
    return {
        "topics": [
            "0x" + "00" * 32,  # topic0 (already filtered by subscription)
            "0x" + "11" * 32,  # orderHash
            "0x" + "0" * 24 + maker[2:],
            "0x" + "0" * 24 + taker[2:],
        ],
        "data": "0x" + "".join(
            _word(v) for v in (maker_asset, taker_asset, maker_amt, taker_amt, fee)
        ),
        "transactionHash": "0xFEED" + "00" * 30,
        "blockNumber": hex(65_000_000),
    }


def test_topic_is_computed_not_hardcoded():
    topic = order_filled_topic()
    assert topic.startswith("0x") and len(topic) == 66


def test_maker_buy():
    # Maker gave 61,000 USDC (id 0), received 100,000 shares → BUY @ 0.61.
    entry = _log(WHALE, OTHER, 0, TOKEN_ID, 61_000_000_000, 100_000_000_000)
    fill = decode_order_filled(entry, {WHALE})
    assert fill is not None
    assert fill.wallet == WHALE and fill.side == "BUY"
    assert fill.token_id == str(TOKEN_ID)
    assert fill.size == pytest.approx(100_000)
    assert fill.price == pytest.approx(0.61)
    assert fill.block_number == 65_000_000


def test_maker_sell():
    entry = _log(WHALE, OTHER, TOKEN_ID, 0, 50_000_000_000, 20_000_000_000)
    fill = decode_order_filled(entry, {WHALE})
    assert fill.side == "SELL"
    assert fill.size == pytest.approx(50_000)
    assert fill.price == pytest.approx(0.40)


def test_taker_perspective_is_inverted():
    # Order owner buys; our whale is the taker → whale sold.
    entry = _log(OTHER, WHALE, 0, TOKEN_ID, 61_000_000_000, 100_000_000_000)
    fill = decode_order_filled(entry, {WHALE})
    assert fill.wallet == WHALE and fill.side == "SELL"


def test_untracked_wallets_ignored():
    entry = _log(OTHER, OTHER, 0, TOKEN_ID, 1_000_000, 2_000_000)
    assert decode_order_filled(entry, {WHALE}) is None


def test_token_for_token_leg_skipped():
    entry = _log(WHALE, OTHER, 111, 222, 1_000_000, 1_000_000)
    assert decode_order_filled(entry, {WHALE}) is None


def test_zero_amount_skipped():
    entry = _log(WHALE, OTHER, 0, TOKEN_ID, 0, 1_000_000)
    assert decode_order_filled(entry, {WHALE}) is None
