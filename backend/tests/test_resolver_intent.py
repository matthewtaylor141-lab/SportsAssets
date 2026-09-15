"""Every resolver mapping names its intent, or is not returned.

The live executor refuses any mapping whose `intent` is empty —
correctly, since on shared-identifier families intent is the ONLY side
selector (venue ground truth 2026-08-24). Audit 2026-08-29: that
refusal ("no side intent") was ~72% of the live reject stream, and the
dominant producers were resolvers that had already picked a UNIQUE side
and then returned it without the stamp:

  - resolve_market (fuzzy): none of its three return sites carried
    intent, so every fuzzy mapping was born unorderable;
  - _spread_exact / resolve_derivative_exact totals: mapping_src is
    "exact", which rides past the quarantine — then died at the intent
    guard anyway, every single one.

These tests pin the fix: each return site stamps the intent derived
from the venue's own side markers (order_intent_for / side_intent) and
ONLY from a verified market shape — a search skeleton whose sides may
simply be omitted from the response never gets a contract stamp.
Candidate SELECTION is unchanged: a winner whose intent cannot be named
still returns, with intent None, and the executor's untouched
refuse-on-unknown guard rejects it exactly as it rejected the entire
class before the stamp. Also pinned: the ABSENCE of any
complement-of-sibling deduction in side_intent (killed by adversarial
review before it ran — see TestNoComplementDeduction), the event
lane's late-verification rule, and the parity path's parent-with-sides
guard.
"""

from sportsassets import pmus
from sportsassets.workers import premap

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


class _Markets:
    def __init__(self, by_slug=None, by_event=None):
        self.by_slug = by_slug or {}
        self.by_event = by_event or {}

    def retrieve_by_slug(self, slug):
        if slug not in self.by_slug:
            raise KeyError(slug)
        return {"market": self.by_slug[slug]}

    def list(self, params):
        out = []
        for s in params.get("eventSlug") or []:
            out.extend(self.by_event.get(s, []))
        return {"markets": out}


class _Search:
    def __init__(self, events=None):
        self.events = events or []

    def query(self, params):
        return {"events": self.events}


class _Client:
    def __init__(self, markets, search=None):
        self.markets = markets
        self.search = search or _Search()


def _use(monkeypatch, client):
    monkeypatch.setattr(pmus, "_get_client", lambda: client)


# ── resolve_market: fuzzy paths stamp intent ─────────────────────────

def test_slug_parity_contract_carries_buy_long(monkeypatch):
    _use(monkeypatch, _Client(_Markets(by_slug={
        "g1": {"slug": "g1", "title": "Red Sox to win",
               "outcome": "Red Sox", "closed": False}})))
    r = pmus.resolve_market("g1", None, "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is not None and r["matched_by"] == "slug"
    assert r["intent"] == LONG


def test_slug_parity_parent_with_sides_routes_to_side_selection(monkeypatch):
    """The parity step used to return a two-sided PARENT verbatim —
    ordering it sideless hands side selection to the venue (incident
    2026-08-23). Now the parent is judged by the same side-selection
    loop as every candidate, and the winning SIDE (with its intent) is
    what comes back."""
    parent = {
        "slug": "aec-x", "title": "A vs B", "closed": False,
        "question": "Who will win A vs B?",
        "outcome": "A vs B",
        "marketSides": [
            {"identifier": "aec-x", "description": "Alpharetta FC",
             "long": True},
            {"identifier": "aec-x", "description": "Betelgeuse United",
             "long": False},
        ]}
    _use(monkeypatch, _Client(_Markets(by_slug={"aec-x": parent})))
    r = pmus.resolve_market("aec-x", None, "Alpharetta FC vs. Betelgeuse "
                            "United", None, "Betelgeuse United")
    assert r is not None
    assert r["market_slug"] == "aec-x"
    assert r["intent"] == SHORT


def test_event_path_side_win_carries_the_sides_intent(monkeypatch):
    m = {"slug": "aec-y", "closed": False,
         "question": "Who wins?",
         "marketSides": [
             {"identifier": "aec-y", "description": "Carlos Alcaraz",
              "long": True},
             {"identifier": "aec-y", "description": "Jannik Sinner",
              "long": False},
         ]}
    _use(monkeypatch, _Client(_Markets(by_event={"e": [m]})))
    r = pmus.resolve_market(None, "e", "Alcaraz vs. Sinner", None,
                            "Jannik Sinner")
    assert r is not None and r["matched_by"] == "event"
    assert r["intent"] == SHORT
    r2 = pmus.resolve_market(None, "e", "Alcaraz vs. Sinner", None,
                             "Carlos Alcaraz")
    assert r2 is not None and r2["intent"] == LONG


def test_event_path_distinct_identifiers_default_long(monkeypatch):
    m = {"slug": "g1", "closed": False, "question": "Who wins?",
         "marketSides": [
             {"identifier": "g1-a", "description": "Yankees"},
             {"identifier": "g1-b", "description": "Red Sox"},
         ]}
    _use(monkeypatch, _Client(_Markets(by_event={"e": [m]})))
    r = pmus.resolve_market(None, "e", "Yankees vs. Red Sox", None,
                            "Red Sox")
    assert r is not None and r["market_slug"] == "g1-b"
    assert r["intent"] == LONG


def test_search_path_hydrated_market_carries_intent(monkeypatch):
    """The skeleton from search hydrates via a full lookup — the full
    shape is what earns the contract stamp."""
    full = {"slug": "s-red-sox", "outcome": "Red Sox", "closed": False,
            "title": "Red Sox to win"}
    _use(monkeypatch, _Client(
        _Markets(by_slug={"s-red-sox": full}),
        _Search(events=[{"title": "Yankees vs. Red Sox",
                         "markets": [{"slug": "s-red-sox"}]}])))
    r = pmus.resolve_market(None, None, "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is not None and r["matched_by"] == "search"
    assert r["intent"] == LONG


def test_search_skeleton_never_gets_a_contract_stamp(monkeypatch):
    """A search market that never hydrated has an UNVERIFIED shape:
    absent marketSides may mean the search response simply omits them.
    Stamping BUY_LONG would make a possibly-two-sided PARENT orderable
    (the venue then picks the side — incident 2026-08-23). It still
    wins selection, but carries intent None so the executor's
    no-side-intent guard refuses it exactly as before the stamp."""
    _use(monkeypatch, _Client(
        _Markets(),  # hydration lookup 404s -> skeleton is scored
        _Search(events=[{"title": "Yankees vs. Red Sox",
                         "markets": [{"slug": "s-red-sox",
                                      "outcome": "Red Sox",
                                      "closed": False}]}])))
    r = pmus.resolve_market(None, None, "Yankees vs. Red Sox",
                            "Yankees vs. Red Sox", "Red Sox")
    assert r is not None and r["matched_by"] == "search"
    assert r["intent"] is None


def test_unmarked_shared_identifier_wins_but_carries_no_intent(monkeypatch):
    """No marker on EITHER side + shared identifier: the venue never
    named a side and the complement deduction has nothing to deduce
    from. Selection is unchanged — the side still wins — but the
    mapping carries intent None and the executor refuses it. A
    lower-scored orderable candidate must never be silently promoted
    over it."""
    m = {"slug": "aec-z", "closed": False, "question": "Who wins?",
         "marketSides": [
             {"identifier": "aec-z", "description": "Team Alpha"},
             {"identifier": "aec-z", "description": "Team Bravo"},
         ]}
    _use(monkeypatch, _Client(_Markets(by_event={"e": [m]})))
    r = pmus.resolve_market(None, "e", "Alpha vs. Bravo", None,
                            "Team Alpha")
    assert r is not None and r["market_slug"] == "aec-z"
    assert r["intent"] is None


# ── derivative exact paths stamp intent ──────────────────────────────

_TOTALS = {"tsc-mlb-nyy-bos-2026-07-22-8pt5": {
    "slug": "tsc-mlb-nyy-bos-2026-07-22-8pt5", "closed": False,
    "question": "Yankees vs Red Sox: O/U 8.5",
    "marketSides": [
        {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-over",
         "description": "Over"},
        {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-under",
         "description": "Under"},
    ]}}


def test_totals_exact_carries_intent(monkeypatch):
    _use(monkeypatch, _Client(_Markets(by_slug=_TOTALS)))
    r = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-o8pt5",
                                      "Over 8.5")
    assert r is not None and r["matched_by"] == "derivative_exact"
    assert r["intent"] == LONG


def test_spread_exact_carries_intent(monkeypatch):
    table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
        "slug": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5",
        "closed": False,
        "question": "Manchester United -1.5",
        "marketSides": [
            {"identifier": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5-yes",
             "description": "Yes"},
            {"identifier": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5-no",
             "description": "No"},
        ]}}
    _use(monkeypatch, _Client(_Markets(by_slug=table)))
    r = pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5")
    assert r is not None and r["matched_by"] == "spread_exact_yes"
    assert r["intent"] == LONG


def test_totals_shared_identifier_without_marker_stays_unorderable(
        monkeypatch):
    """An exact totals hit whose sides share an identifier with no
    marker on any of them is unorderable: it returns with intent None
    and the executor's guard refuses it. (>2 shared sides also defeats
    the two-sided complement deduction — no marker, no order.)"""
    slug = "tsc-mlb-nyy-bos-2026-07-22-8pt5"
    table = {slug: {
        "slug": slug, "closed": False,
        "question": "Yankees vs Red Sox: O/U 8.5",
        "marketSides": [
            {"identifier": slug, "description": "Over"},
            {"identifier": slug, "description": "Under"},
            {"identifier": slug, "description": "Push"},
        ]}}
    _use(monkeypatch, _Client(_Markets(by_slug=table)))
    r = pmus.resolve_derivative_exact(
        "mlb-nyy-bos-2026-07-22-o8pt5", "Over 8.5")
    assert r is None or r["intent"] is None


# ── event lane: late verification of the contract stamp ─────────────

def test_event_lane_contract_stamp_needs_the_full_lookup(monkeypatch):
    """markets.list rows are UNVERIFIED shapes — an abbreviated row
    that omits marketSides must not be stamped BUY_LONG on the list
    row's word alone (a two-sided PARENT would become orderable and
    the venue would pick our side — incident 2026-08-23, adversarial
    review 2026-08-29). The stamp is earned by ONE full lookup: a
    truly sideless market gets BUY_LONG; a market whose full shape
    carries sides stays intent None and the executor refuses it."""
    listed = {"slug": "g1-red-sox", "outcome": "Red Sox",
              "closed": False}  # no marketSides key — omission
    sideless_full = {"slug": "g1-red-sox", "outcome": "Red Sox",
                     "closed": False, "title": "Red Sox to win"}
    _use(monkeypatch, _Client(_Markets(by_event={"e": [listed]},
                                       by_slug={"g1-red-sox":
                                                sideless_full})))
    r = pmus.resolve_market(None, "e", "Yankees vs. Red Sox", None,
                            "Red Sox")
    assert r is not None and r["intent"] == LONG

    two_sided_full = {"slug": "g1-red-sox", "closed": False,
                      "title": "Yankees vs Red Sox",
                      "marketSides": [
                          {"identifier": "g1-red-sox",
                           "description": "Yankees"},
                          {"identifier": "g1-red-sox",
                           "description": "Red Sox"},
                      ]}
    _use(monkeypatch, _Client(_Markets(by_event={"e": [listed]},
                                       by_slug={"g1-red-sox":
                                                two_sided_full})))
    r2 = pmus.resolve_market(None, "e", "Yankees vs. Red Sox", None,
                             "Red Sox")
    assert r2 is not None and r2["intent"] is None

    # unreadable lookup: fail closed, no stamp
    _use(monkeypatch, _Client(_Markets(by_event={"e": [listed]})))
    r3 = pmus.resolve_market(None, "e", "Yankees vs. Red Sox", None,
                             "Red Sox")
    assert r3 is not None and r3["intent"] is None


# ── side_intent: NO complement deduction (adversarial review) ────────

class TestNoComplementDeduction:
    """A sibling's marker never names THIS side. The deduction was
    written and killed by its own adversarial review before it ran:
    it fed the live premap lane with venue-unstated intents, the side
    echo re-derives through the same function (self-certifying), and
    the manual desk would pass a deduced BUY_SHORT to submit_fok with
    no LIVE_ALLOW_SHORT gate. Explicit marker on the side itself, or
    a unique identifier, or refuse."""

    def test_sibling_marked_long_does_not_name_this_side(self):
        sides = [{"identifier": "aec-m", "description": "A", "long": True},
                 {"identifier": "aec-m", "description": "B"}]
        assert premap.side_intent(sides[1], sides) is None

    def test_sibling_marked_short_does_not_name_this_side(self):
        sides = [{"identifier": "aec-m", "description": "A",
                  "marketSideType": "MARKET_SIDE_TYPE_SHORT"},
                 {"identifier": "aec-m", "description": "B"}]
        assert premap.side_intent(sides[1], sides) is None

    def test_two_unmarked_sides_refuse(self):
        sides = [{"identifier": "aec-m", "description": "A"},
                 {"identifier": "aec-m", "description": "B"}]
        assert premap.side_intent(sides[0], sides) is None
        assert premap.side_intent(sides[1], sides) is None

    def test_unique_identifier_default_is_unchanged(self):
        sides = [{"identifier": "g-a", "description": "A", "long": True},
                 {"identifier": "g-b", "description": "B"}]
        assert premap.side_intent(sides[0], sides) == LONG
        assert premap.side_intent(sides[1], sides) == LONG
