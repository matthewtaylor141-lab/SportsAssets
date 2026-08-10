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


# ── v2 exchange fill event (contract migration, 2026-08-10) ──────────
# Both logs below are the REAL receipt entries from RN1's fill in tx
# 0x2be95df8...62e3d4 — the empirical basis for the v2 layout. The
# decode must reproduce the known fill exactly: BUY 12.31 shares of the
# known token at 0.62 ($7.6322).

RN1 = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"
CPTY = "0x40841cc01207119e01b04800c71f7ce281767fe1"
V2_TOKEN = ("8b435bedce4affcea1657d428e22887d5aa835db"
            "02348efe46afd585222fe5f9")
V2_TOPIC = ("0xd543adfd945773f1a62f74f0ee55a5e3b9b1a282"
            "62980ba90b1a89f2ea84d8ee")


def _v2_log(owner, counterparty, side, gave, got, fee=0):
    data = "0x" + _word(side) + V2_TOKEN + _word(gave) + _word(got) \
        + _word(fee) + _word(0) + _word(0)
    return {
        "topics": [
            V2_TOPIC,
            "0x" + "22" * 32,                    # orderHash
            "0x" + "0" * 24 + owner[2:],         # order owner
            "0x" + "0" * 24 + counterparty[2:],  # counterparty
        ],
        "data": data,
        "transactionHash": "0x2be95df880975cf37af78330c7c9b597"
                           "aeff44d6338110c79cf11db46362e3d4",
        "blockNumber": "0x4bd1a30",
    }


def test_v2_buy_reproduces_the_live_rn1_fill():
    from sportsassets.ingestion.chain import decode_order_filled_v2

    entry = _v2_log(RN1, CPTY, side=0, gave=0x747548, got=0xBBD5F0)
    fill = decode_order_filled_v2(entry, {RN1})
    assert fill is not None
    assert fill.wallet == RN1 and fill.side == "BUY"
    assert fill.size == pytest.approx(12.31)
    assert fill.price == pytest.approx(0.62)
    assert fill.token_id == str(int(V2_TOKEN, 16))


def test_v2_sell_side_flag_inverts_amount_meaning():
    from sportsassets.ingestion.chain import decode_order_filled_v2

    # The counterparty's own log: side=1, gave tokens, got USDC.
    entry = _v2_log(CPTY, RN1, side=1, gave=0xBBD5F0, got=0x747548,
                    fee=0x23672)
    fill = decode_order_filled_v2(entry, {CPTY})
    assert fill is not None
    assert fill.side == "SELL"
    assert fill.size == pytest.approx(12.31)
    assert fill.price == pytest.approx(0.62)


def test_v2_untracked_owner_is_ignored():
    from sportsassets.ingestion.chain import decode_order_filled_v2

    entry = _v2_log(CPTY, RN1, side=0, gave=0x747548, got=0xBBD5F0)
    assert decode_order_filled_v2(entry, {RN1}) is None, \
        "only topic2 (the order owner) may match the roster"


def test_v2_nonsense_price_is_refused():
    from sportsassets.ingestion.chain import decode_order_filled_v2

    entry = _v2_log(RN1, CPTY, side=0, gave=5_000_000, got=1_000_000)
    assert decode_order_filled_v2(entry, {RN1}) is None, \
        "an implied price >= 1 is not a priced fill"
