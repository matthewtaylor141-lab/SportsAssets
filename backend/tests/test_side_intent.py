"""THE ROOT CAUSE (venue ground truth, 2026-08-24).

The venue was asked directly and answered: on the aec- family BOTH
sides of a match carry the SAME identifier, equal to the market slug:

  side[0] identifier='aec-atp-martop-migdam-2026-08-24' 'Marko Topo'
  side[1] identifier='aec-atp-martop-migdam-2026-08-24' 'Miguel Damas'

CreateOrderParams has no side field, so the slug alone cannot name a
side — only `intent` can (BUY_LONG vs BUY_SHORT). Every copy this
engine placed sent BUY_LONG, so on that entire family the side was
chosen by the venue rather than by the whale's pick. Every previous
wrong-side fix assumed side identifiers were distinct, so none of them
could have closed this.

These tests pin the contract: name the side, or refuse.
"""

import asyncio

from sportsassets import pmus
from sportsassets.workers import premap


def _shared_market():
    """The venue's real tennis shape: two sides, one identifier."""
    slug = "aec-atp-martop-migdam-2026-08-24"
    return {"slug": slug, "question": "Marko Topo vs. Miguel Damas",
            "marketSides": [
                {"identifier": slug, "description": "Marko Topo",
                 "long": True},
                {"identifier": slug, "description": "Miguel Damas",
                 "long": False}]}


def _distinct_market():
    """A family whose sides DO carry distinct identifiers."""
    base = "atc-epl-ars-che-2026-08-24"
    return {"slug": base, "question": "Arsenal vs. Chelsea",
            "marketSides": [
                {"identifier": base + "-ars", "description": "Arsenal"},
                {"identifier": base + "-che", "description": "Chelsea"}]}


class TestSideIntent:
    def test_long_flag_selects_the_intent(self):
        m = _shared_market()
        sides = m["marketSides"]
        assert premap.side_intent(sides[0], sides) == \
            "ORDER_INTENT_BUY_LONG"
        assert premap.side_intent(sides[1], sides) == \
            "ORDER_INTENT_BUY_SHORT"

    def test_market_side_type_is_honoured_when_long_is_absent(self):
        sides = [{"identifier": "x", "description": "A",
                  "marketSideType": "MARKET_SIDE_TYPE_LONG"},
                 {"identifier": "x", "description": "B",
                  "marketSideType": "MARKET_SIDE_TYPE_SHORT"}]
        assert premap.side_intent(sides[0], sides) == \
            "ORDER_INTENT_BUY_LONG"
        assert premap.side_intent(sides[1], sides) == \
            "ORDER_INTENT_BUY_SHORT"

    def test_shared_identifier_without_a_marker_REFUSES(self):
        """The dangerous shape: two sides, one identifier, no long/short
        marker. There is no field left that could name the side, so the
        only safe answer is None."""
        sides = [{"identifier": "aec-x-2026-08-24", "description": "A"},
                 {"identifier": "aec-x-2026-08-24", "description": "B"}]
        assert premap.side_intent(sides[0], sides) is None
        assert premap.side_intent(sides[1], sides) is None

    def test_distinct_identifiers_default_to_buy_long(self):
        """When the identifier itself names the side, the historical
        BUY_LONG is correct and must keep working."""
        m = _distinct_market()
        sides = m["marketSides"]
        assert premap.side_intent(sides[0], sides) == \
            "ORDER_INTENT_BUY_LONG"


class TestBothSidesSurviveTheSweep:
    def test_shared_identifier_market_yields_two_distinct_rows(self):
        """us_premap keyed on identifier alone collapsed both sides of
        every aec- match into ONE row — one player silently overwrote
        the other. The rows must stay distinct and each must carry its
        own intent."""
        rows = premap._market_rows({"slug": "e", "title": "t"},
                                   _shared_market())
        assert len(rows) == 2
        assert {r["side_norm"] for r in rows} == {"marko topo",
                                                  "miguel damas"}
        by_side = {r["side_norm"]: r for r in rows}
        assert by_side["marko topo"]["intent"] == "ORDER_INTENT_BUY_LONG"
        assert by_side["miguel damas"]["intent"] == "ORDER_INTENT_BUY_SHORT"
        # both point at the same orderable slug — the intent is what
        # separates them
        assert len({r["identifier"] for r in rows}) == 1


class TestResolveRefusesUnorderableSides:
    class _Pool:
        def __init__(self, rows):
            self.rows = rows

        async def fetch(self, sql, *a):
            keys = set(a[0])
            return [r for r in self.rows if set(r["event_keys"]) & keys]

    def _row(self, **over):
        keys = premap.event_keys_for("Marko Topo vs. Miguel Damas",
                                     "aec-atp-martop-migdam-2026-08-24")
        r = {"identifier": "aec-atp-martop-migdam-2026-08-24",
             "side_norm": "miguel damas", "kind": "side", "line": "",
             "question": "Marko Topo vs. Miguel Damas",
             "event_title": "", "event_keys": keys,
             "intent": "ORDER_INTENT_BUY_SHORT"}
        r.update(over)
        return r

    def test_row_with_intent_resolves_and_carries_it(self):
        pool = self._Pool([self._row()])
        hit = asyncio.run(premap.resolve(
            pool, "Marko Topo vs. Miguel Damas", None, "Miguel Damas",
            "atp-martop-migdam-2026-08-24"))
        assert hit is not None
        assert hit["intent"] == "ORDER_INTENT_BUY_SHORT"

    def test_row_without_intent_is_refused(self):
        """An unorderable side must never be returned — returning it
        hands side selection back to the venue."""
        pool = self._Pool([self._row(intent=None)])
        hit = asyncio.run(premap.resolve(
            pool, "Marko Topo vs. Miguel Damas", None, "Miguel Damas",
            "atp-martop-migdam-2026-08-24"))
        assert hit is None


class TestSubmitCarriesTheIntent:
    def _client(self, seen):
        class _Orders:
            def preview(self, params):
                return {"order": {}}

            def create(self, params):
                seen.append(params)
                return {"id": "o1", "executions": []}

        class _C:
            orders = _Orders()

        return _C()

    def test_buy_short_reaches_the_venue(self, monkeypatch):
        seen = []
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: self._client(seen))
        pmus.submit_fok("aec-atp-martop-migdam-2026-08-24", 0.5, 10,
                        intent="ORDER_INTENT_BUY_SHORT")
        assert seen and seen[0]["intent"] == "ORDER_INTENT_BUY_SHORT"

    def test_default_is_still_buy_long(self, monkeypatch):
        seen = []
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: self._client(seen))
        pmus.submit_fok("atc-epl-ars-che-2026-08-24-ars", 0.5, 10)
        assert seen and seen[0]["intent"] == "ORDER_INTENT_BUY_LONG"

    def test_a_bogus_intent_never_reaches_the_venue(self, monkeypatch):
        seen = []
        monkeypatch.setattr(pmus, "_get_client",
                            lambda: self._client(seen))
        out = pmus.submit_fok("x", 0.5, 10, intent="ORDER_INTENT_SELL_SHORT")
        assert out["ok"] is False and out["status"] == "bad_intent"
        assert seen == []


class TestEchoIndependence:
    """Leak-hunt round 2: the echo re-derived inside the market the
    suspect premap row itself named, so an internally consistent but
    WRONG row certified itself. A second opinion now re-resolves the
    whale's own signal through a different resolver; disagreement is a
    mismatch even when the first check said ok."""

    def test_disagreement_is_a_mismatch(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(
            pmus, "resolve_market_exact",
            lambda cands, outcome: {
                "market_slug": "aec-atp-OTHER-2026-08-24",
                "intent": "ORDER_INTENT_BUY_LONG"})
        v, d = asyncio.run(le._independent_check(
            "aec-atp-martop-migdam-2026-08-24", "Miguel Damas",
            "Marko Topo vs. Miguel Damas",
            "atp-martop-migdam-2026-08-24", "ORDER_INTENT_BUY_SHORT"))
        assert v == "mismatch" and "OTHER" in d

    def test_same_slug_but_opposite_intent_is_a_mismatch(self,
                                                         monkeypatch):
        """The subtle one: right market, WRONG SIDE — only the intent
        differs, which is exactly the failure this whole build is
        about."""
        from sportsassets import live_executor as le

        slug = "aec-atp-martop-migdam-2026-08-24"
        monkeypatch.setattr(
            pmus, "resolve_market_exact",
            lambda cands, outcome: {"market_slug": slug,
                                    "intent": "ORDER_INTENT_BUY_LONG"})
        v, d = asyncio.run(le._independent_check(
            slug, "Miguel Damas", "Marko Topo vs. Miguel Damas",
            "atp-martop-migdam-2026-08-24", "ORDER_INTENT_BUY_SHORT"))
        assert v == "mismatch" and "BUY_LONG" in d

    def test_agreement_reads_ok(self, monkeypatch):
        from sportsassets import live_executor as le

        slug = "aec-atp-martop-migdam-2026-08-24"
        monkeypatch.setattr(
            pmus, "resolve_market_exact",
            lambda cands, outcome: {"market_slug": slug,
                                    "intent": "ORDER_INTENT_BUY_SHORT"})
        v, _ = asyncio.run(le._independent_check(
            slug, "Miguel Damas", "Marko Topo vs. Miguel Damas",
            "atp-martop-migdam-2026-08-24", "ORDER_INTENT_BUY_SHORT"))
        assert v == "ok"

    def test_resolver_silence_is_unverified_not_ok(self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(pmus, "resolve_market_exact",
                            lambda cands, outcome: None)
        v, _ = asyncio.run(le._independent_check(
            "x", "Miguel Damas", "Marko Topo vs. Miguel Damas",
            "atp-martop-migdam-2026-08-24", "ORDER_INTENT_BUY_SHORT"))
        assert v == "unverified"
