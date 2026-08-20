"""Fast-lane polling tier (owner latency push 2026-08-20): the pinned
COPY whales — the wallets the executor actually trades — get their own
short re-poll cycle. The tier is selected by username against the
canonical COPY_WHALES allowlist, so a platform-tracked wallet that is
not a copy source never spends fast-lane request budget."""

from sportsassets.ingestion.poller import priority_whales


def _w(username):
    return {"id": 1, "address": "0xabc", "username": username}


def test_pinned_copy_whales_ride_the_fast_lane():
    roster = [_w("RN1"), _w("SwissTony"), _w("kch123"),
              _w("HomeRunHazard"),
              _w("0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465")]
    assert len(priority_whales(roster)) == 5


def test_non_copy_wallets_stay_on_the_roster_rotation():
    roster = [_w("RN1"), _w("some-tracked-wallet"), _w(None), _w("")]
    fast = priority_whales(roster)
    assert [w["username"] for w in fast] == ["RN1"]
