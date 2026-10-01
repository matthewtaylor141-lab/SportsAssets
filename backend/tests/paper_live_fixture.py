"""Fixtures for the paper vertical slice: a SYNTHETIC entry-experiment
valuation with a fresh Pinnacle reading, a controllable book transport behind
the real PaperMarketDataClient, and the real research-model training path
(tests/test_derek_research_observations_break_the_deadlock._train_register).
ALL DATA SYNTHETIC, on scratch test accounts; nothing is written into the live
paper account."""
from __future__ import annotations

import json
import time
import uuid

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_model as FM
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_session as S

from tests import paper_harness as H

LONG = "ORDER_INTENT_BUY_LONG"
SYN = "paper-live-syn-"


class Transport:
    """The network side of the PaperMarketDataClient, substituted: returns
    the book set for a slug with a controlled observation instant."""

    def __init__(self, t0: float):
        self.books = {}
        self.t = float(t0)
        self.step = 1.0
        self.calls = []

    def set(self, slug, *, bids=(), offers=()):
        self.books[slug] = H.md(bids=bids, offers=offers)

    def __call__(self, slug):
        self.calls.append(slug)
        self.t += self.step
        md = self.books.get(slug)
        if md is None:
            return {"marketData": None, "error": "NO_BOOK_FIXTURE",
                    "observed_at": self.t}
        return {"marketData": json.loads(json.dumps(md)),
                "observed_at": self.t}


def client(transport) -> G.PaperMarketDataClient:
    return G.PaperMarketDataClient(transport)


def config(**over) -> dict:
    cfg = S.default_config()
    cfg["cadence"] = dict(cfg["cadence"], pass_budget_s=60.0)
    for k, v in over.items():
        cfg[k] = dict(cfg[k], **v)
    return cfg


async def valuation(conn, *, slug=None, p_pin=0.62, decided_at=None,
                    pin_age_s=5.0, purpose="CALIBRATION_ONLY",
                    compatibility="COMPATIBLE") -> dict:
    """One entry-experiment valuation of a PMUS moneyline contract whose
    Pinnacle reading is `pin_age_s` old at `decided_at`."""
    at = float(decided_at if decided_at is not None else time.time())
    slug = slug or "%s%s" % (SYN, uuid.uuid4().hex[:10])
    vid = await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, decision, admissible, refusals, why, "
        " payout_event, payout_is_complement, buy_intent, ladder_side, "
        " record_purpose, decided_at, event_key, settlement_comparison, "
        " calibration_only_evidence) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',$2,$2,'HOME',"
        " 'baseball','h2h','FULL_GAME','{}'::jsonb,2,2,to_timestamp($3),"
        " to_timestamp($3 + 1),$4,'NO_TRADE',false,"
        " ARRAY['VENUE_BOOK_CURRENCY_NOT_ESTABLISHED'],'synthetic',"
        " 'HOME',false,$5,'ASK',$6,to_timestamp($7),'e-' || $2,$8::jsonb,"
        " $9::jsonb) "
        "RETURNING id", ext.EXPERIMENT_ID, slug, at - float(pin_age_s),
        float(p_pin), LONG, purpose, at,
        json.dumps({"compatibility": compatibility}),
        (json.dumps({"usable_for_orders": False,
                     "venue_read_refusal": "VENUE_BOOK_CURRENCY_NOT_"
                                           "ESTABLISHED"})
         if purpose == "CALIBRATION_ONLY" else None))
    return {"valuation_id": vid, "slug": slug, "decided_at": at}


async def settle_valuation(conn, vid: int, *, outcome: int,
                           basis: str = "VENUE_SETTLEMENT_PRICE") -> None:
    await conn.execute(
        "UPDATE external_valuations SET outcome_known = TRUE, outcome=$2, "
        " outcome_basis=$3, outcome_at=now() WHERE id=$1", vid, outcome,
        basis)


async def purge_research_models(conn) -> None:
    await conn.execute("DELETE FROM bettor_funded_models WHERE model_key=$1",
                       FM.KEY_ENTRY_PAYOUT)


async def cleanup_seed(conn) -> None:
    """Undo what `train_model`'s Derek-harness seed wrote (its catalogue,
    fixture rows and the CONTROLLED_INTEGRATION_TEST source-calibration
    measurement), so later proofs in a shared database start clean."""
    from tests import test_derek_enters_on_conservative_agreement as DT
    await DT._cleanup(conn)


async def train_model(conn, monkeypatch, *, model_id: str) -> None:
    """The real research-model fit and registration (a CANDIDATE), through
    the existing proof's helpers."""
    from tests import test_derek_research_observations_break_the_deadlock \
        as DRT
    await DRT.DT._seed(conn)
    await DRT._ensure_schema(conn)
    prior = await DRT._pause_backfill(conn)
    try:
        await DRT._purge(conn)
        await DRT._train_register(conn, monkeypatch, tag="paper",
                                  rule=DRT.RULE_INFORMATIVE,
                                  model_id=model_id)
    finally:
        await DRT._restore_backfill(conn, prior)


async def purge_everything(conn) -> None:
    """Everything the paper live proofs put into tables OTHER proofs read.

    `train_model` seeds through the Derek research harness, which inserts a
    fresh CONTROLLED_INTEGRATION_TEST source-calibration row, fixture rows
    and synthetic valuations; `valuation` inserts synthetic valuations. Left
    in a shared test database they made the calibration schedule see a
    recent row (and skip its critical cases) and the held-position census
    count a candidate that is not one. Remove them -- and only them."""
    from tests import test_derek_research_observations_break_the_deadlock \
        as DRT
    try:
        await DRT._purge(conn)
    except Exception:                                           # noqa: BLE001
        pass
    await DRT.DT._cleanup(conn)
    async with conn.transaction():
        # paper_decisions keep their valuation id; the paper tables are the
        # proofs' own records, so the valuation rows go without the FK check
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM external_valuations WHERE us_market_slug LIKE $1",
            SYN + "%")
        await conn.execute(
            "DELETE FROM external_source_calibration WHERE measured_by = $1",
            "CONTROLLED_INTEGRATION_TEST")


async def drop_today_run(conn, at: float) -> None:
    """The daily research run the no-model proof triggered, removed so a
    shared test database's other proofs see today's run as not yet made."""
    from sportsassets.agents import derek_research as DR
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("DELETE FROM derek_research_model_runs "
                           " WHERE run_day=$1", DR._day_of(at))
        # the day's first run, and any same-day run after it (181)
        await conn.execute("DELETE FROM derek_research_model_attempts "
                           " WHERE run_id=$1 OR run_id LIKE $1 || ':%'",
                           "derek-research-run:%s" % DR._day_of(at))


async def new_account(conn, tag, *, now, cfg=None):
    return await H.new_account(conn, tag, config=cfg or config(), now=now)


TWO_MODEL_ENTRIES_KEY = "PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2"


async def two_model_entries(conn, enabled: bool):
    """Set the two-model strategy's paper entry switch (migration 182, off by
    default: only the benchmark opens new entries). Returns the previous
    value (None when the row was absent) for `restore_two_model_entries`."""
    prev = await conn.fetchval("SELECT enabled FROM paper_control WHERE "
                               " control_key=$1", TWO_MODEL_ENTRIES_KEY)
    await conn.execute(
        "INSERT INTO paper_control (control_key, enabled, why, updated_by) "
        "VALUES ($1, $2, 'test', 'test') ON CONFLICT (control_key) DO UPDATE"
        " SET enabled = EXCLUDED.enabled", TWO_MODEL_ENTRIES_KEY,
        bool(enabled))
    return prev


async def restore_two_model_entries(conn, prev) -> None:
    if prev is None:
        await conn.execute("DELETE FROM paper_control WHERE control_key=$1",
                           TWO_MODEL_ENTRIES_KEY)
    else:
        await conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                           " control_key=$1", TWO_MODEL_ENTRIES_KEY,
                           bool(prev))
