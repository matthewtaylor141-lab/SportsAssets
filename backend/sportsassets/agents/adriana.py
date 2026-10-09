"""ADRIANA'S DESK: THE SHADOW ARBITRAGE CENSUS OVER RECORDED BOOKS (265).

Adriana (Head of Arbitrage, SHADOW_ONLY) runs one census pass at a time:

  1. READ the newest recorded book of every market the system observed in
     the last BOOK_WINDOW_S (paper_book_observations: the read-only market
     data the paper path recorded, with our receipt instant) and that
     market's catalogue identity (us_premap). Nothing is fetched from a
     venue here: no network, no credential.
  2. BUILD contracts with explicit payoff maps over a proved outcome space:
       TOTALS   tsc-<lg>-<a>-<b>-<date>[-fh|-sh]-<L>: YES = the total is
                more than L, NO = it is not (the venue's own question);
       SPREADS  asc-<lg>-<a>-<b>-<date>[-seg]-(neg|pos)-<L>: YES = team a
                covers (the margin a-b is above +L for neg, -L for pos).
     The integer underlying is cut by every listed half-point line of the
     same event, family and period (adriana_arb.line_buckets): exhaustive by
     construction. Any other family, or a slug the grammar does not read, is
     counted by name and skipped -- never guessed.
  3. EVALUATE every pair of contracts of one event / family / period across
     markets with the pure engine (adriana_arb.pair_scanner): settlement and
     payoff equivalence, synchronized freshness, executable depth on every
     leg, every fee, slippage and cost, the largest profitable matched size.
  4. VOID TERMS FROM EACH CONTRACT'S OWN CAPTURED RULES, ELSE FAIL CLOSED.
     What a venue pays when an event is cancelled or postponed decides
     whether a structure can lose. (RC6) Each contract's terms are read from
     ITS OWN published rules as the market plane captured them
     (market_plane_rules: the venue's text, its sha256, the source and the
     registry parser version), re-parsed here with the current parser and
     accepted only when the text still hashes to the recorded fingerprint
     (`contract_void_terms`). Polymarket US states, for every totals and
     spreads line in production (research-sql 37873179784 G: 4,166 spread
     and 2,640 total rows, all "delayed"), that a game "delayed, postponed,
     or suspended and not rescheduled to a date within two weeks [two
     calendar days on NBA / NHL lines] ... will settle to the last fair
     market price": ONE market's price, a number in [0, 1] nobody names in
     advance. Two markets' fair prices are never summed to $1
     (settlement_pair_policy, owner directive RC5), so on established terms
     a structure over two markets is guaranteed only the sum of each leg's
     LOWER bound in those states -- 0 -- and is REFUSED by its priced
     void-state floor (R_VOID_STATE_FLOOR), never an opportunity. A contract
     whose terms are not captured, not stated, ambiguous (manual review) or
     whose text no longer hashes to the record keeps the declared
     HYPOTHESIS (both legs settle 50-50) only to measure the economics, and
     its structures are REFUSED with VOID_TERMS_NOT_ESTABLISHED -- a
     structure that would be proven under that hypothesis is counted as a
     CONDITIONAL candidate, never as an opportunity. An opportunity row is
     written only for a structure whose every outcome reconciles on terms
     actually established. `ESTABLISHED_VOID_TERMS` stays the explicit
     family-level declaration (empty: none is declared); when a caller
     passes `void_terms` the per-contract read is not used.
  5. KALSHI / POLYMARKET (international): the engine prices them, but no
     book of either venue is recorded in Postgres, so the census states
     each venue UNAVAILABLE with the reason -- never an empty success.
  6. RECORD the pass (adriana_arb_scans), every opportunity
     (adriana_arb_opportunities) and the refusals (adriana_arb_refusals,
     the first MAX_RECORDED_REFUSALS ranked by how close they came; the
     scan row carries the full counts and how many were recorded, so no cap
     is silent), all in one transaction declared as ADRIANA: the database
     refuses any order, intent, fill, approval or control write in it.
  7. COLLABORATE through records only: a HANDOFF to Archer (execution
     review) and a REVIEW_REQUEST to Karen (challenge) per new opportunity;
     a daily REVIEW_REQUEST to Audrey citing the latest census; and one
     open task per structural blocker (terms not established, a venue with
     no recorded books) on her own queue.

NO AUTHORITY: this module imports no venue, order, credential or capital
module, opens no connection and writes only Adriana's own tables plus her
messages and tasks. `assert_no_authority` holds the engine to the same.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .. import settlement_rule_registry as SRR
from . import adriana_arb as A
from . import pos_authority as PA
from . import registry as R

VERSION = "ADRIANA_CENSUS_V1"
AGENT = R.ADRIANA
BOOK_WINDOW_S = 900
MAX_MARKETS = 800
MAX_RECORDED_REFUSALS = 150
MAX_AGE_S = A.DEFAULT_MAX_AGE_S
MAX_SKEW_S = A.DEFAULT_MAX_SKEW_S
#: (RC6) the claim-first scan's own census keys recorded beside the counts
#: (bounded: tallies and at most a handful of examples each)
SCAN_SUMMARY_KEYS = ("pairs_not_complementary", "near_complement_pairs",
                     "same_market_pairs_excluded", "same_market_only_pairs",
                     "settlement_pair_policy", "by_topology", "book_sources",
                     "exceptional_state_refusals_proven_under_hypothesis",
                     "claim_engine", "scope")

R_NO_SCHEMA = "MIGRATION_265_NOT_APPLIED"
VOID_TERMS_NOT_ESTABLISHED = "VOID_TERMS_NOT_ESTABLISHED"
#: (venue, family) whose cancellation / postponement payout is DECLARED
#: established for the whole family. Empty: none is declared; each contract's
#: own captured rules are read instead (`contract_void_terms`).
ESTABLISHED_VOID_TERMS: dict = {}
#: the hypothesis the economics are MEASURED under (never a verdict)
HYPOTHESIS = {"void_payout": Decimal("0.5"), "postponed_payout": Decimal("0.5"),
              "label": "HYPOTHESIS_BOTH_LEGS_SETTLE_50_50_ON_VOID_OR_POSTPONEMENT"}

# ── (RC6) THE VOID TERMS OF ONE CONTRACT, FROM ITS OWN CAPTURED RULES ─────
#: where the census reads them (migration 312; the premap sweep writes the
#: venue's own description at no extra venue request)
VOID_TERMS_SOURCE = "market_plane_rules"
#: what one contract HELD is guaranteed in a cancelled / unrescheduled game
#: under each payout rule the registry parser can state: (YES, NO). A last
#: fair market price is ONE market's number in [0, 1] -- its guarantee is 0
#: on either side, and two markets' prices are never summed to $1
#: (settlement_pair_policy B_SEPARATE); a scalar 0.50 is 0.50 on both sides.
#: Anything else (stake back, an unread rule) is not a fixed payout.
PAYOUT_LOWER_BOUND = {
    SRR.LAST_FAIR_PRICE: (Decimal("0"), Decimal("0")),
    SRR.SCALAR_0_50: (Decimal("0.5"), Decimal("0.5")),
}
#: why a contract's void / postponement terms are NOT established (each
#: counted by name in the scan, never a silent default)
R_VT_RULES_NOT_CAPTURED = "VOID_TERMS_RULES_NOT_CAPTURED"
R_VT_RULES_NOT_PUBLISHED = "VOID_TERMS_RULES_NOT_PUBLISHED_BY_THE_VENUE"
R_VT_FINGERPRINT_DIFFERS = "VOID_TERMS_RULES_TEXT_DIFFERS_FROM_ITS_FINGERPRINT"
R_VT_CONFLICT = "VOID_TERMS_RULES_IN_CONFLICT"
R_VT_MANUAL_REVIEW = "VOID_TERMS_MANUAL_REVIEW_IS_NOT_A_PAYOUT"
R_VT_VOID_NOT_STATED = "VOID_TERMS_CANCELLATION_PAYOUT_NOT_STATED"
R_VT_POSTPONEMENT_NOT_STATED = "VOID_TERMS_POSTPONEMENT_PAYOUT_NOT_STATED"
R_VT_WINDOW_NOT_STATED = "VOID_TERMS_POSTPONEMENT_WINDOW_NOT_STATED"
R_VT_RULE_NOT_FIXED = "VOID_TERMS_PAYOUT_RULE_IS_NOT_A_FIXED_OR_BOUNDED_PAYOUT"
#: a structure whose ordinary-completion floor is positive but whose
#: cancelled / postponed floor -- each leg at the lower bound of ITS OWN
#: market's stated payout -- is lower: the venue's terms, read and
#: established, are what refuse it (classified as the settlement policy's
#: priced-floor refusal is: SOFTWARE / SETTLEMENT)
R_VOID_STATE_FLOOR = "VOID_STATE_FLOOR_ON_ESTABLISHED_TERMS_BELOW_THE_STRUCTURE"

FAMILY_OF_PREFIX = {"tsc": A.TOTAL, "asc": A.SPREAD}
SKIP_FAMILY = {"aec": "MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED",
               "atc": "MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED",
               "astatc": "PROP_FAMILY_NOT_MODELLED"}
#: (RC6) the venue's own slug forms, read off production (research-sql
#: 37873179784 F2): the football full-game total carries `total-` before
#: its line (tsc-cfb-airf-nill-2026-10-10-total-19pt5, 2,640 active rows
#: read SLUG_GRAMMAR_NOT_READ before), and football quarters are `1q`..`4q`
#: (tsc-...-1q-10pt5, asc-...-3q-neg-1pt5). A team total (`tt-`, `tt1h-`,
#: `tt2h-`), a tennis / esports / first-five form (`tg-`, `st-`, `gs-`,
#: `ss-`, `tot-`, `f5-`, `m2tr-`) is a different subject: never matched.
_SLUG = re.compile(
    r"^(?P<pfx>tsc|asc)-(?P<lg>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:-(?P<seg>fh|sh|1h|2h|q[1-4]|[1-4]q|p[1-3])|-(?P<total>total))?-"
    r"(?:(?P<sign>neg|pos)-)?(?P<line>\d+(?:pt\d+)?)$")

AUTHORITY = dict(A.AUTHORITY)
VENUES_NOT_RECORDED = {
    # (RC6) true of THIS census, which reads paper_book_observations only;
    # since migration 314 Kalshi books ARE recorded (kalshi_books_current)
    # and the claim-first cross-venue scan (adr-claims-*) reads them
    A.KALSHI: ("NO_KALSHI_BOOK_SOURCE_IN_THIS_CENSUS: the recorded-books "
               "census reads Polymarket US books (paper_book_observations) "
               "only; Kalshi books (kalshi_books_current) are read by the "
               "claim-first cross-venue scan recorded beside it "
               "(adr-claims-*)"),
    A.POLYMARKET: ("NO_POLYMARKET_INTERNATIONAL_BOOK_SOURCE: no book is "
                   "recorded and no fee schedule is known"),
}


def _d(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        if isinstance(x, dict):
            x = x.get("value")
        return Decimal(str(x))
    except Exception:                                           # noqa: BLE001
        return None


def _j(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:                                           # noqa: BLE001
        return None


def parse_slug(slug: str) -> dict | None:
    """The family grammar of a Polymarket US market slug, or None."""
    m = _SLUG.match(str(slug or ""))
    if not m:
        return None
    g = m.groupdict()
    raw = g["line"].replace("pt", ".")
    line = Decimal(raw)
    fam = FAMILY_OF_PREFIX[g["pfx"]]
    if fam == A.SPREAD and not g["sign"]:
        return None
    if fam == A.TOTAL and g["sign"]:
        return None
    if g["total"] and fam != A.TOTAL:
        return None
    # the threshold the YES side must clear on the integer underlying
    # (total points; or margin a - b, which is above +L for 'neg-L' and
    # above -L for 'pos-L')
    thr = line if fam == A.TOTAL else (line if g["sign"] == "neg" else -line)
    seg = g["seg"] or ""
    if re.fullmatch(r"[1-4]q", seg):
        seg = "q" + seg[0]          # the venue's 1q is the period Q1
    period = {"fh": "FIRST_HALF", "1h": "FIRST_HALF", "sh": "SECOND_HALF",
              "2h": "SECOND_HALF"}.get(seg, (seg or "FULL").upper())
    event = "%s-%s-%s-%s" % (g["lg"], g["a"], g["b"], g["date"])
    return {"family": fam, "league": g["lg"], "a": g["a"], "b": g["b"],
            "date": g["date"], "period": period, "line": line,
            "threshold": thr, "event": event,
            "subject": "TOTAL_POINTS" if fam == A.TOTAL else
            "MARGIN_%s_MINUS_%s" % (g["a"].upper(), g["b"].upper())}


def _levels(raw) -> list:
    out = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        px, q = _d(e.get("px")), _d(e.get("qty"))
        if px is None or q is None or not (Decimal(0) < px < Decimal(1)) \
                or q < 1:
            continue
        out.append((px, int(q)))
    return out


def _aware(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def _iso(ts) -> str | None:
    at = _aware(ts)
    return at.isoformat() if at else None


def _lfp_sentence(text) -> str | None:
    """The venue's own sentence that states the last-fair-price payout,
    verbatim (the citation a reader checks the term against)."""
    for s in re.split(r"(?<=\.)\s+", " ".join(str(text or "").split())):
        if re.search(r"fair\s+(?:market\s+)?price", s, re.I):
            return s[:400]
    return None


def contract_void_terms(row: dict) -> dict:
    """THE CANCELLATION / POSTPONEMENT TERMS ONE POLYMARKET US CONTRACT'S OWN
    CAPTURED RULES STATE. Pure.

    `row` carries the market_plane_rules columns read beside the book
    (rules_published, rules_sha256, rules_text, rules_parse_status,
    rules_parser_version, rules_source, rules_observed_at). The captured text
    is re-parsed with the current registry parser and accepted only when it
    still hashes to the recorded fingerprint; the terms are ESTABLISHED only
    when the text states a cancellation payout, a postponement payout and the
    postponement window, each a payout this census can bound
    (PAYOUT_LOWER_BOUND), with no manual-review clause and no conflict.
    Returns {"established", "why", "void_rule", "postponement_payout",
    "window_h", "void_lo" (YES, NO), "postponed_lo" (YES, NO), "rule_id",
    "citation"} -- never a default."""
    cite = {"source": row.get("rules_source") or VOID_TERMS_SOURCE,
            "rules_sha256": row.get("rules_sha256"),
            "parser_version": row.get("rules_parser_version"),
            "captured_at": _iso(row.get("rules_observed_at"))}
    out = {"established": False, "why": None, "void_rule": None,
           "postponement_payout": None, "window_h": None, "void_lo": None,
           "postponed_lo": None, "rule_id": None, "citation": cite}
    if row.get("rules_published") is None and not row.get("rules_sha256"):
        return dict(out, why=R_VT_RULES_NOT_CAPTURED)
    if row.get("rules_published") is False or not row.get("rules_text"):
        return dict(out, why=R_VT_RULES_NOT_PUBLISHED)
    ev = SRR.polymarket_us_rule_evidence(row.get("rules_text"))
    if ev.get("rules_sha256") != row.get("rules_sha256"):
        return dict(out, why=R_VT_FINGERPRINT_DIFFERS)
    if str(row.get("rules_parse_status") or "") == SRR.CONFLICT:
        return dict(out, why=R_VT_CONFLICT)
    cite = dict(cite, parsed_with=SRR.PARSER_VERSION,
                clause=_lfp_sentence(row.get("rules_text")))
    out["citation"] = cite
    if SRR.SC_MANUAL_LFMP in (ev.get("special_conditions") or ()):
        return dict(out, why=R_VT_MANUAL_REVIEW)
    s = ev.get("settlement") or {}
    vr, pp = s.get("void_rule"), s.get("postponement_payout")
    w = s.get("postponement_window_hours")
    out.update(void_rule=vr, postponement_payout=pp, window_h=w)
    if vr is None:
        return dict(out, why=R_VT_VOID_NOT_STATED)
    if pp is None:
        return dict(out, why=R_VT_POSTPONEMENT_NOT_STATED)
    if w is None:
        return dict(out, why=R_VT_WINDOW_NOT_STATED)
    if vr not in PAYOUT_LOWER_BOUND or pp not in PAYOUT_LOWER_BOUND:
        return dict(out, why=R_VT_RULE_NOT_FIXED)
    return dict(out, established=True, void_lo=PAYOUT_LOWER_BOUND[vr],
                postponed_lo=PAYOUT_LOWER_BOUND[pp],
                # compared for equality across legs by the engine: the RULE,
                # never a value (two markets' fair prices are two numbers)
                rule_id="VENUE_RULES:VOID=%s;POSTPONED_BEYOND_%gH=%s" % (
                    vr, float(w), pp))


def _vt_summary(terms: dict) -> dict:
    """The void-terms census of the contracts read (counts by name)."""
    est = [t for t in terms.values() if t["established"]]
    why: dict[str, int] = {}
    rules: dict[str, int] = {}
    wins: dict[str, int] = {}
    for t in terms.values():
        if not t["established"]:
            why[t["why"]] = why.get(t["why"], 0) + 1
            continue
        k = "%s/%s" % (t["void_rule"], t["postponement_payout"])
        rules[k] = rules.get(k, 0) + 1
        wk = "%g" % float(t["window_h"])
        wins[wk] = wins.get(wk, 0) + 1
    return {"source": "%s (each contract's own published rules: text, "
                      "sha256, source, parser version)" % VOID_TERMS_SOURCE,
            "contracts": len(terms), "established": len(est),
            "not_established": why, "rules": rules, "windows_h": wins,
            "valuation": "a last fair market price is ONE market's number "
                         "in [0, 1]: guaranteed 0 per leg, two markets "
                         "never summed to $1 (settlement_pair_policy)",
            "examples": [{"market_id": m, **t["citation"]}
                         for m, t in sorted(terms.items())
                         if t["established"]][:3]}


def build_universe(rows, *, void_terms=None, hypothesis_only=False) -> dict:
    """Contracts, books and outcome spaces from recorded rows. Pure.

    `rows`: dicts with us_market_slug, observed_at, offers, bids, error,
    sport (catalogue sports_type, may be None) and, when read, the contract's
    captured rules (contract_void_terms). Every row that cannot become a
    contract is counted under its reason in `skipped`.

    Terms, in order: `void_terms` / ESTABLISHED_VOID_TERMS declared for a
    family; else (unless `hypothesis_only`) the contract's own captured
    rules; else the HYPOTHESIS, flagged not established."""
    vt = dict(ESTABLISHED_VOID_TERMS if void_terms is None else void_terms)
    per_contract = void_terms is None and not hypothesis_only
    terms: dict[str, dict] = {}
    skipped: dict[str, int] = {}
    parsed = []
    for r in rows:
        slug = str(r.get("us_market_slug") or "")
        pfx = slug.split("-", 1)[0]
        if pfx in SKIP_FAMILY:
            skipped[SKIP_FAMILY[pfx]] = skipped.get(SKIP_FAMILY[pfx], 0) + 1
            continue
        p = parse_slug(slug)
        if p is None:
            skipped["SLUG_GRAMMAR_NOT_READ"] = skipped.get(
                "SLUG_GRAMMAR_NOT_READ", 0) + 1
            continue
        if r.get("error"):
            skipped["BOOK_READ_FAILED"] = skipped.get("BOOK_READ_FAILED", 0) + 1
            continue
        if p["threshold"] % 1 == 0:
            skipped["WHOLE_NUMBER_LINE_PUSH_TERMS_NOT_READ"] = skipped.get(
                "WHOLE_NUMBER_LINE_PUSH_TERMS_NOT_READ", 0) + 1
            continue
        parsed.append((r, p))
    groups: dict[str, list] = {}
    for r, p in parsed:
        key = "%s|%s|%s" % (p["event"], p["family"], p["period"])
        groups.setdefault(key, []).append((r, p))
    contracts, books, spaces = [], [], {}
    for key, items in groups.items():
        space = A.line_buckets(key, sorted({p["threshold"] for _, p in items}))
        spaces[key] = space
        for r, p in items:
            slug = r["us_market_slug"]
            fam = vt.get((A.POLYMARKET_US, p["family"]))
            ct = contract_void_terms(r) if per_contract and not fam else None
            if ct is not None:
                terms[slug] = ct
            # one venue, one event: every leg shares the venue's grading of
            # that event, so the window starts on the event's date (identical
            # for every leg by construction, compared for equality only)
            day = datetime.strptime(p["date"], "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
            window = timedelta(days=2)
            if fam:
                vp = (fam["void_payout"],) * 2
                pp = (fam["postponed_payout"],) * 2
                void_rule = fam.get("rule", "ESTABLISHED")
            elif ct is not None and ct["established"]:
                # the venue's own stated payout, at the lower bound each leg
                # is guaranteed whatever its market's fair price turns out
                vp, pp = ct["void_lo"], ct["postponed_lo"]
                void_rule = ct["rule_id"]
                window = timedelta(hours=float(ct["window_h"]))
            else:
                vp = (HYPOTHESIS["void_payout"],) * 2
                pp = (HYPOTHESIS["postponed_payout"],) * 2
                void_rule = HYPOTHESIS["label"]
            spec = A.SettlementSpec(
                event_key=key, family=p["family"], period=p["period"],
                subject=p["subject"], line=p["threshold"],
                resolution_source="POLYMARKET_US:%s" % p["league"].upper(),
                settle_window=(day.isoformat(), (day + window).isoformat()),
                void_rule=void_rule,
                # a half-point line on an integer underlying cannot push
                tie_rule=A.TIE_IMPOSSIBLE)
            sport = (r.get("sport") or p["league"]).upper()
            yes = A.Contract(A.POLYMARKET_US, slug, A.YES, spec,
                             A.over_under_payoff(space, "OVER", p["threshold"],
                                                 void_payout=vp[0],
                                                 postponed_payout=pp[0]),
                             sport=sport)
            no = A.Contract(A.POLYMARKET_US, slug, A.NO, spec,
                            A.over_under_payoff(space, "UNDER", p["threshold"],
                                                void_payout=vp[1],
                                                postponed_payout=pp[1]),
                            sport=sport)
            contracts += [yes, no]
            at = _aware(r.get("observed_at"))
            offers = _levels(_j(r.get("offers")))
            # On Polymarket US the short side of a market IS listed: buying
            # NO is the venue's own BUY_SHORT order, filled against the
            # long bids at (1 - bid). One instrument, declared as such.
            no_asks = A.synthesize_no_asks(
                no, _levels(_j(r.get("bids"))),
                declared_single_instrument={(A.POLYMARKET_US, slug)})
            books += [A.Book(A.POLYMARKET_US, slug, A.YES, tuple(offers), at),
                      A.Book(A.POLYMARKET_US, slug, A.NO, tuple(no_asks), at)]
    if vt:
        established = True                 # declared for the family
    else:
        established = bool(terms) and all(t["established"]
                                          for t in terms.values())
    return {"contracts": contracts, "books": books, "spaces": spaces,
            "skipped": skipped, "markets": len(parsed),
            "family_terms": vt, "terms": terms,
            "void_terms": None if vt else _vt_summary(terms),
            "void_terms_established": established}


def _closeness(rec) -> float:
    eco = rec.get("economics") or rec.get("conditional_economics") or {}
    try:
        return float(eco.get("worst_case_net_profit"))
    except (TypeError, ValueError):
        return float("-inf")


def _pair_key(rec) -> tuple:
    return tuple(sorted((alt.get("venue"), alt.get("market_id"),
                         alt.get("side"))
                        for leg in _legs(rec) for alt in leg))


def _exceptional_floor(rec) -> dict | None:
    """When the structure is a hedge in ordinary completion (positive floor
    over the regular outcomes) but pays less in a cancelled / postponed game:
    {regular_floor, exceptional_floor, outcomes}. Else None."""
    t = (rec.get("payoff_table") or {}).get("payout_by_outcome") or {}
    exc = {o: v for o, v in t.items() if o in A.REQUIRED_NONSTANDARD}
    reg = [Decimal(str(v)) for o, v in t.items()
           if o not in A.REQUIRED_NONSTANDARD]
    if not exc or not reg:
        return None
    lo_reg = min(reg)
    lo_exc = min(Decimal(str(v)) for v in exc.values())
    if lo_reg <= 0 or lo_exc >= lo_reg:
        return None
    return {"regular_floor": str(lo_reg), "exceptional_floor": str(lo_exc),
            "outcomes": sorted(o for o, v in exc.items()
                               if Decimal(str(v)) == lo_exc)}


def census(rows, now: datetime, *, void_terms=None, **kw) -> dict:
    """ONE CENSUS PASS over recorded rows. Pure. Every considered structure
    is in exactly one of `opportunities` / `refusals`.

    A structure is decided on terms only when EVERY leg's terms are
    established (declared for the family, or read from the contract's own
    captured rules). On established last-fair-price terms its cancelled /
    postponed floor is the sum of each leg's lower bound (0): the engine
    refuses it there and R_VOID_STATE_FLOOR names why; the same structure is
    re-run under the HYPOTHESIS only to report what it would have been
    (`conditional_on`, `conditional_economics`), never as a verdict."""
    mx = dict(max_age_s=kw.get("max_age_s", MAX_AGE_S),
              max_skew_s=kw.get("max_skew_s", MAX_SKEW_S))
    u = build_universe(rows, void_terms=void_terms)
    opps, refs, cen = A.pair_scanner(
        u["contracts"], u["books"], now, outcome_spaces=u["spaces"], **mx)
    vt = u["family_terms"]

    def established(rec) -> bool:
        if vt:
            return all((A.POLYMARKET_US, f) in vt for f in _families(rec))
        mids = [alt.get("market_id") for leg in _legs(rec) for alt in leg]
        return bool(mids) and all((u["terms"].get(m) or {}).get(
            "established") for m in mids)

    twin: dict = {}
    if not vt and any(t["established"] for t in u["terms"].values()):
        h = build_universe(rows, void_terms=void_terms, hypothesis_only=True)
        ho, hr, _hc = A.pair_scanner(h["contracts"], h["books"], now,
                                     outcome_spaces=h["spaces"], **mx)
        twin = {_pair_key(r): r for r in ho + hr}

    def leg_terms(rec):
        return {m: {k: t.get(k) for k in ("established", "why", "void_rule",
                                         "postponement_payout", "window_h",
                                         "rule_id", "citation")}
                for m in {alt.get("market_id") for leg in _legs(rec)
                          for alt in leg}
                for t in [u["terms"].get(m)] if t is not None}

    proven, refused = [], []
    conditional = exceptional = 0
    if twin:
        # a structure with ANY leg whose terms are not established is judged
        # exactly as before, all its legs under the hypothesis (its
        # established leg's lower bound would only add a VOID_RULE_DIFFERS)
        hopps, hrefs = [], []
        for rec in opps + refs:
            if established(rec):
                (hopps if rec["verdict"] == A.GUARANTEED_AFTER_COSTS
                 else hrefs).append(rec)
                continue
            h = twin.get(_pair_key(rec), rec)
            (hopps if h["verdict"] == A.GUARANTEED_AFTER_COSTS
             else hrefs).append(h)
        opps, refs = hopps, hrefs
    for rec in opps:
        if established(rec):
            proven.append(rec)
            continue
        # proven ONLY under the declared hypothesis: a conditional
        # candidate, refused until the venue's terms are established
        conditional += 1
        rec = dict(rec, verdict=A.REFUSED, conditional_on=HYPOTHESIS["label"],
                   reasons=[{"code": VOID_TERMS_NOT_ESTABLISHED,
                             "detail": "proven only if both legs settle 50-50 "
                                       "on a cancellation or postponement; "
                                       "the venue's terms are not read"}]
                   + list(rec.get("reasons") or []))
        refused.append(rec)
    for rec in refs:
        if established(rec) and not vt:
            ex = _exceptional_floor(rec)
            if ex is not None:
                rec = dict(rec, reasons=[{
                    "code": R_VOID_STATE_FLOOR,
                    "detail": ("each leg's own published rules settle a "
                               "cancelled / unrescheduled game at ITS "
                               "market's payout (%s); bounded per leg, never "
                               "summed across markets, the structure is "
                               "guaranteed %s there against %s in ordinary "
                               "completion" % (
                                   ", ".join(sorted({t.get("rule_id") or "?"
                                                     for t in leg_terms(
                                                         rec).values()})),
                                   ex["exceptional_floor"],
                                   ex["regular_floor"])),
                    "outcomes": ex["outcomes"]}]
                    + list(rec.get("reasons") or []))
                tw = twin.get(_pair_key(rec))
                if tw is not None and tw["verdict"] == \
                        A.GUARANTEED_AFTER_COSTS:
                    exceptional += 1
                    rec.update(conditional_on=HYPOTHESIS["label"],
                               conditional_economics=tw.get("economics"),
                               conditional_books=(tw.get("inputs") or {}).get(
                                   "books"))
        elif not established(rec):
            codes = A.reason_codes(rec)
            if VOID_TERMS_NOT_ESTABLISHED not in codes:
                rec["reasons"] = list(rec.get("reasons") or []) + [
                    {"code": VOID_TERMS_NOT_ESTABLISHED,
                     "detail": "venue cancellation / postponement terms not "
                               "read; also refused for the reasons above"}]
        refused.append(rec)
    if not vt:
        for rec in proven + refused:
            lt = leg_terms(rec)
            if lt:
                rec["void_terms"] = lt
    refused.sort(key=_closeness, reverse=True)
    summary = A.census_of(proven + refused)
    summary.update(
        markets_read=u["markets"], skipped=u["skipped"],
        conditional_candidates=conditional,
        exceptional_state_refusals_proven_under_hypothesis=exceptional,
        contracts=len(u["contracts"]), outcome_spaces=len(u["spaces"]),
        same_market_pairs_not_considered=cen.get(
            "same_market_pairs_not_considered", 0),
        hypothesis=HYPOTHESIS["label"],
        void_terms_established=u["void_terms_established"],
        void_terms=u["void_terms"])
    fresh = sum(1 for b in u["books"] if b.side == A.YES and b.observed_at
                and (now - b.observed_at).total_seconds() <= MAX_AGE_S)
    return {"opportunities": proven, "refusals": refused, "census": summary,
            "books_fresh": fresh}


def _families(rec) -> list:
    out = []
    for leg in ((rec.get("inputs") or {}).get("legs") or []):
        for alt in leg if isinstance(leg, list) else [leg]:
            p = parse_slug(alt.get("market_id")) if isinstance(alt, dict) \
                else None
            if p:
                out.append(p["family"])
    return out or [None]


# ═════════════════════════════════════════════════════════════════════
# I/O: read, record, collaborate (each write declared as ADRIANA)
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    return all([await conn.fetchval("SELECT to_regclass($1)", t)
                for t in ("adriana_arb_scans", "adriana_arb_opportunities",
                          "adriana_arb_refusals")])


#: (RC6) each market's captured rules, read beside its book (migration
#: 312); a database without the table reads every rule as not captured
_RULES_COLS = (
    "r.rules_published, r.rules_sha256, r.rules_text, "
    "r.parse_status AS rules_parse_status, "
    "r.parser_version AS rules_parser_version, r.source AS rules_source, "
    "r.observed_at AS rules_observed_at")
_NO_RULES_COLS = (
    "NULL::boolean AS rules_published, NULL::text AS rules_sha256, "
    "NULL::text AS rules_text, NULL::text AS rules_parse_status, "
    "NULL::text AS rules_parser_version, NULL::text AS rules_source, "
    "NULL::timestamptz AS rules_observed_at")


async def read_rows(conn, *, now: float, window_s: float = BOOK_WINDOW_S,
                    limit: int = MAX_MARKETS) -> list:
    """The newest recorded book of each market observed in the window, with
    its catalogue sport and its captured rules. Read only."""
    has_rules = bool(await conn.fetchval(
        "SELECT to_regclass('market_plane_rules') IS NOT NULL"))
    rows = await conn.fetch(
        "WITH latest AS (SELECT DISTINCT ON (us_market_slug) obs_id, "
        "       us_market_slug, observed_at, offers, bids, error "
        "  FROM paper_book_observations "
        " WHERE observed_at >= to_timestamp($1) "
        " ORDER BY us_market_slug, observed_at DESC) "
        "SELECT l.*, (SELECT p.sports_type FROM us_premap p "
        "              WHERE p.market_slug = l.us_market_slug "
        "                AND p.sports_type IS NOT NULL LIMIT 1) AS sport, "
        + (_RULES_COLS if has_rules else _NO_RULES_COLS) +
        "  FROM latest l "
        + ("LEFT JOIN market_plane_rules r "
           "       ON r.contract_id = l.us_market_slug " if has_rules else "")
        + " ORDER BY l.observed_at DESC LIMIT $2",
        now - window_s, limit)
    return [dict(r) for r in rows]


def _id(prefix: str, *parts) -> str:
    return "%s-%s" % (prefix, hashlib.sha256("|".join(
        str(p) for p in parts).encode()).hexdigest()[:20])


def _legs(rec) -> list:
    return [[{k: alt.get(k) for k in ("venue", "market_id", "side")}
             for alt in (leg if isinstance(leg, list) else [leg])]
            for leg in ((rec.get("inputs") or {}).get("legs") or [])]


def _venues(rec) -> list:
    return sorted({alt["venue"] for leg in _legs(rec) for alt in leg
                   if alt.get("venue")})


def _event_key(rec) -> str | None:
    return (rec.get("inputs") or {}).get("event_key")


async def record(conn, result: dict, *, started: float, finished: float,
                 scan_id: str, status: str, why: str | None) -> dict:
    """One transaction, declared as ADRIANA: the scan, its opportunities and
    the ranked refusals. Idempotent on the scan id."""
    PA.assert_may(AGENT, "write.arb_records")
    c = result["census"]
    refs = result["refusals"][:MAX_RECORDED_REFUSALS]
    async with conn.transaction():
        await PA.act_as(conn, AGENT)
        made = await conn.execute(
            "INSERT INTO adriana_arb_scans (scan_id, started_at, finished_at, "
            " status, why, engine_version, venues, markets_read, books_fresh, "
            " structures_considered, opportunities, refusals_total, "
            " refusals_recorded, by_verdict, by_kind, by_code, limits, "
            " authority) VALUES ($1, to_timestamp($2), to_timestamp($3), $4, "
            " $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14::jsonb, "
            " $15::jsonb, $16::jsonb, $17::jsonb, $18::jsonb) "
            "ON CONFLICT (scan_id) DO NOTHING",
            scan_id, started, finished, status, why,
            "%s+%s" % (VERSION, A.VERSION),
            json.dumps(result.get("venues") or venue_support(result),
                       default=str),
            int(c.get("markets_read") or 0), int(result.get("books_fresh") or 0),
            len(result["opportunities"]) + len(result["refusals"]),
            len(result["opportunities"]), len(result["refusals"]), len(refs),
            json.dumps(c.get("by_verdict") or {}),
            json.dumps(c.get("by_structure_kind") or {}),
            json.dumps(dict({
                "by_code": c.get("by_refusal_code") or {},
                "by_primary_code": c.get("by_primary_refusal_code") or {},
                "skipped": c.get("skipped") or {},
                "conditional_candidates": c.get("conditional_candidates", 0),
                "same_market_pairs_not_considered": c.get(
                    "same_market_pairs_not_considered", 0),
                # (RC6) the void / postponement terms of every contract the
                # scan read, by name: what completion's fail_closed states
                "void_terms": c.get("void_terms"),
                "void_terms_established": c.get("void_terms_established")},
                **{k: c[k] for k in SCAN_SUMMARY_KEYS if k in c}),
                default=str),
            json.dumps({"book_window_s": BOOK_WINDOW_S,
                        "max_markets": MAX_MARKETS, "max_age_s": MAX_AGE_S,
                        "max_skew_s": MAX_SKEW_S,
                        "max_recorded_refusals": MAX_RECORDED_REFUSALS,
                        "hypothesis": HYPOTHESIS["label"],
                        "void_terms_source": VOID_TERMS_SOURCE}),
            json.dumps(AUTHORITY))
        if not made.endswith("1"):
            return {"ok": True, "created": False, "scan_id": scan_id}
        opp_ids = []
        for rec in result["opportunities"]:
            eco = rec["economics"]
            oid = _id("adr-opp", scan_id, json.dumps(_legs(rec)))
            plan = A.plan_execution(rec) if len(_legs(rec)) == 2 else None
            await conn.execute(
                "INSERT INTO adriana_arb_opportunities (opportunity_id, "
                " scan_id, structure_kind, event_key, venues, legs, verdict, "
                " max_qty, min_payout_usd, total_cost_usd, net_profit_usd, "
                " edge_per_set_usd, books, economics, leg_plan, "
                " evidence_refs, decided_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,"
                " $7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,$15::jsonb,"
                " $16::jsonb, to_timestamp($17)) ON CONFLICT DO NOTHING",
                oid, scan_id, rec["structure_kind"], _event_key(rec),
                _venues(rec), json.dumps(_legs(rec)), rec["verdict"],
                int(eco["qty"]), Decimal(str(eco["floor_payout_per_set"])),
                Decimal(str(eco["total_cost"])),
                Decimal(str(eco["worst_case_net_profit"])),
                # the engine names the per-set edge `edge_per_contract`
                # (one contract per leg per matched set): the same number
                Decimal(str(eco.get("edge_per_set",
                                    eco.get("edge_per_contract")))),
                json.dumps((rec.get("inputs") or {}).get("books") or []),
                json.dumps(dict(eco, claim_pair=rec.get("claim_pair"))
                           if rec.get("claim_pair") else eco, default=str),
                json.dumps(None if plan is None else {
                    "state": plan.state, "target_qty": plan.target_qty,
                    "leg_a": {"venue": plan.leg_a.venue,
                              "market_id": plan.leg_a.market_id,
                              "side": plan.leg_a.side},
                    "leg_b": {"venue": plan.leg_b.venue,
                              "market_id": plan.leg_b.market_id,
                              "side": plan.leg_b.side},
                    "rule": "thinner, then less reliable leg first",
                    "mode": "SHADOW"}, default=str),
                json.dumps([{"kind": "adriana_arb_scans", "id": scan_id}]),
                finished)
            opp_ids.append(oid)
        for i, rec in enumerate(refs):
            codes = [x for x in A.reason_codes(rec) if x] or ["UNCLASSIFIED"]
            await conn.execute(
                "INSERT INTO adriana_arb_refusals (refusal_id, scan_id, "
                " structure_kind, event_key, venues, legs, codes, "
                " primary_code, detail, decided_at) VALUES ($1,$2,$3,$4,$5,"
                " $6::jsonb,$7,$8,$9::jsonb, to_timestamp($10)) "
                "ON CONFLICT DO NOTHING",
                _id("adr-ref", scan_id, i, json.dumps(_legs(rec))), scan_id,
                rec.get("structure_kind") or "PAIR", _event_key(rec),
                _venues(rec) or [A.POLYMARKET_US], json.dumps(_legs(rec)),
                codes, codes[0], json.dumps({
                    "reasons": rec.get("reasons"),
                    "economics": rec.get("economics"),
                    "conditional_on": rec.get("conditional_on"),
                    "books": (rec.get("inputs") or {}).get("books"),
                    "skew_s": (rec.get("inputs") or {}).get("skew_s"),
                    "payoff_table": rec.get("payoff_table"),
                    # (claim-first scans) the canonical claims, the basis
                    # and the topology read off the routes actually used
                    "claim_pair": rec.get("claim_pair"),
                    # (RC6) each leg's void terms as read, with their
                    # citation; what the structure would have been under
                    # the hypothesis (never a verdict); the pair policy
                    "void_terms": rec.get("void_terms"),
                    "conditional_economics": rec.get(
                        "conditional_economics"),
                    "conditional_books": rec.get("conditional_books"),
                    "settlement_pair_policy": rec.get(
                        "settlement_pair_policy")}, default=str),
                finished)
    return {"ok": True, "created": True, "scan_id": scan_id,
            "opportunity_ids": opp_ids, "refusals_recorded": len(refs)}


def venue_support(result: dict) -> dict:
    """Per venue: SUPPORTED (books recorded and read) or UNAVAILABLE with
    the reason. Never an empty success."""
    out = {A.POLYMARKET_US: {
        "status": "SUPPORTED" if result["census"].get("markets_read")
        else "UNAVAILABLE",
        "why": None if result["census"].get("markets_read") else
        "NO_RECORDED_BOOK_IN_THE_WINDOW",
        "source": "paper_book_observations (recorded, read-only)",
        "fee_schedule": "bettor_fee_schedule (dated, per order)"}}
    for v, why in VENUES_NOT_RECORDED.items():
        out[v] = {"status": "UNAVAILABLE", "why": why, "source": None}
    return out


async def collaborate(conn, *, scan_id: str, opportunity_ids: list,
                      result: dict, now: float) -> dict:
    """Records only: hand-offs per new opportunity, one daily review
    request to Audrey, one open task per structural blocker."""
    from . import agent_memory as M
    out = {"handoffs": 0, "review_requests": 0, "tasks": 0}
    for oid in opportunity_ids:
        ref = [{"kind": "adriana_arb_opportunities", "id": oid}]
        h = await M.record_message(
            conn, from_agent=AGENT, to_agent=R.ARCHER, message_kind="HANDOFF",
            subject_type="adriana_arb_opportunities", subject_id=oid,
            summary="Proven after costs in SHADOW: review the executability "
                    "of both legs (depth, leg order, partial-fill recovery).",
            evidence_refs=ref, created_at=now)
        k = await M.record_message(
            conn, from_agent=AGENT, to_agent=R.KAREN,
            message_kind="REVIEW_REQUEST",
            subject_type="adriana_arb_opportunities", subject_id=oid,
            summary="Challenge this structure: settlement, payoff in every "
                    "outcome, freshness, depth and costs.",
            evidence_refs=ref, created_at=now)
        out["handoffs"] += int(bool(h.get("created")))
        out["review_requests"] += int(bool(k.get("created")))
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    c = result["census"]
    a = await M.record_message(
        conn, from_agent=AGENT, to_agent=R.AUDREY,
        message_kind="REVIEW_REQUEST", subject_type="adriana_census_day",
        subject_id=day,
        summary=("Daily arbitrage census for audit: %d structures, %d proven, "
                 "%d refused, %d conditional on unread settlement terms, %d "
                 "refused at their void-state floor on established terms. "
                 "The claim-first cross-venue scan is recorded beside it." % (
                     len(result["opportunities"]) + len(result["refusals"]),
                     len(result["opportunities"]), len(result["refusals"]),
                     c.get("conditional_candidates", 0),
                     c.get("exceptional_state_refusals_proven_under_"
                           "hypothesis", 0))),
        evidence_refs=[{"kind": "adriana_arb_scans", "id": scan_id}],
        created_at=now)
    out["review_requests"] += int(bool(a.get("created")))
    blockers = []
    # (RC6) a blocker while ANY contract read lacks established terms (each
    # contract's own captured rules), named by reason -- not while a family
    # declaration is merely absent
    if not (ESTABLISHED_VOID_TERMS) and not c.get("void_terms_established"):
        vts = c.get("void_terms") or {}
        blockers.append(("adriana-task-void-terms",
                         "Establish Polymarket US cancellation / postponement "
                         "payouts for totals and spreads from recorded venue "
                         "terms",
                         {"blocker": VOID_TERMS_NOT_ESTABLISHED,
                          "why": "every structure on a contract whose own "
                                 "rules do not state its void terms stays "
                                 "REFUSED until they are read and recorded",
                          "not_established": vts.get("not_established"),
                          "contracts": vts.get("contracts")}))
    blockers.append(("adriana-task-kalshi-books",
                     "Record Kalshi order books (read-only) so cross-venue "
                     "pairs can be evaluated",
                     {"blocker": "NO_KALSHI_BOOK_SOURCE",
                      "why": VENUES_NOT_RECORDED[A.KALSHI]}))
    for tid, title, spec in blockers:
        t = await R.create_task(
            conn, assignee=AGENT, created_by=AGENT,
            kind="ADRIANA_BLOCKER_V1", title=title, spec=spec,
            evidence=[{"kind": "adriana_arb_scans", "id": scan_id}],
            task_id=tid, now=now)
        if t.get("created"):
            # her own blocker: it waits on a dependency outside her desk
            # (venue terms, a book source), it is not work handed to her
            await R.task_event(conn, tid, kind="WAITING_ON_DEPENDENCY",
                               actor=AGENT, detail=spec, status="WAITING",
                               now=now)
        out["tasks"] += int(bool(t.get("created")))
    return out


async def workroom_posts(conn, *, limit: int = 3) -> list:
    """#agent-workroom lines from her records (latest census pass and any
    new opportunity), once each: [(key, text)]."""
    out = []
    r = await conn.fetchrow(
        "SELECT scan_id, finished_at, structures_considered, opportunities, "
        "       refusals_total, by_code FROM adriana_arb_scans "
        " WHERE finished_at > now() - interval '1 hour' "
        " ORDER BY finished_at DESC LIMIT 1")
    if r is not None:
        bc = _j(r["by_code"]) or {}
        top = sorted((bc.get("by_primary_code") or {}).items(),
                     key=lambda kv: -kv[1])[:3]
        out.append(("census:%s" % r["scan_id"], (
            "Adriana · arbitrage census %s · %d structures · %d proven after "
            "costs · %d refused (%s)\nStage: SHADOW census; nothing is "
            "ordered, sized or approved by this message." % (
                r["scan_id"], r["structures_considered"], r["opportunities"],
                r["refusals_total"],
                ", ".join("%s %d" % kv for kv in top) or "no refusal"))))
    for o in await conn.fetch(
            "SELECT opportunity_id, structure_kind, event_key, max_qty, "
            "       net_profit_usd FROM adriana_arb_opportunities "
            " WHERE decided_at > now() - interval '1 hour' "
            " ORDER BY decided_at DESC LIMIT $1", limit):
        out.append(("opportunity:%s" % o["opportunity_id"], (
            "Adriana · %s on %s · %d sets · $%.2f worst-case net after costs "
            "(SHADOW)\nNext: Archer reviews executability, Karen challenges. "
            "Nothing is ordered." % (o["structure_kind"], o["event_key"],
                                    o["max_qty"],
                                    float(o["net_profit_usd"])))))
    return out[:limit]


def assert_no_authority() -> bool:
    return A.assert_no_authority() and not any(AUTHORITY.get(k) for k in (
        "submit", "cancel", "credentials", "capital"))
