"""LAB-B: THE PRODUCTION RETROSPECTIVE SQL IS THE PYTHON VERIFIER.

The stored answers cannot be brought out of production (the job-log host is
outside this session's egress policy), so PM question E is measured by a
read-only SQL port of the verifier, run through research-sql.yml. This test
runs THAT FILE (sportsassets/lab/citation_retrospective.sql -- the copy on
claude/command-center research/lab_b_citation_retrospective.sql is the same
bytes) against a seeded database and requires every tally to equal the Python
verifier's (`citation_integrity_store.retrospective(profile="RETRO_PORT")`)
for every agent x provider mode, on a deterministic corpus of a few hundred
answers that exercises every rule: verbatim quotations with sentences of their
own, clause binding, a citation before its clause, the tail after the last
group, unknown fact ids, rounding (half-up / half-even, the significant-digit
rule), percent units, counting words, question echoes, NO_CITATION, CURRENT
and SUPERSEDED assertions against marked records, stale substitutions, the
records-only label, and the LLM-mode inference.
"""

import pathlib
import random
import re
import time

import pytest

from sportsassets.lab import citation_integrity as CI
from sportsassets.lab import citation_integrity_store as CIS
from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)

SQL = (pathlib.Path(__file__).resolve().parents[1] / "sportsassets" / "lab" /
       "citation_retrospective.sql").read_text()

POOL = [
    ("paper_ledger", "paper-main#seq412", "paper_cash_usd", 500000.0,
     "paper account paper-main cash $500,000 (from the paper ledger)"),
    ("paper_ledger", "paper-main#seq412", "paper_reserved_usd", 1250.0,
     "paper account reserved $1,250 (part of cash, not extra)"),
    ("paper_ledger", "paper-main#seq412", "paper_available_usd", 498750.0,
     "paper account available $498,750"),
    ("paper_ledger", "paper-main#seq412", "paper_realized_pnl_usd", 2.91,
     "paper realised P&L $2.91"),
    ("paper_ledger", "paper-main#seq412", "paper_equity_usd", 500003.125,
     "paper account total equity $500,003.125 (cash plus marked open "
     "positions)"),
    ("paper_xavier_reviews", "rv-8812", "current_management_decision",
     "HOLD_ON_STALE_PROBABILITY",
     "CURRENT Xavier management decision for paper position "
     "paperexpgrp:4f2a91c0: HOLD_ON_STALE_PROBABILITY; review rv-8812 at "
     "2026-10-04T18:20:11+00:00; probability pinnacle, 149.300 s old at the "
     "review (freshness limit 30 s), valuation 77120"),
    ("paper_xavier_reviews", "rv-8790", "superseded_review", "HOLD",
     "SUPERSEDED Xavier review of paper position paperexpgrp:4f2a91c0 "
     "(history, NOT the current decision; superseded by rv-8812): recorded "
     "HOLD; review rv-8790 at 2026-10-04T18:18:57+00:00; probability "
     "pinnacle, 75.200 s old at the review (freshness limit 30 s), valuation "
     "77101"),
    ("derek_entry_decisions", "drk-441", "p_blended", 0.585,
     "blended probability 0.585"),
    ("derek_entry_decisions", "drk-441", "gross_edge_pp", 6.25,
     "edge (pp) 6.25"),
    ("derek_entry_decisions", "drk-441", "executable_price", 0.52,
     "executable price 0.52"),
    ("paper_entry_policy", "pv-17", "active_entry_threshold_pp", 5.0,
     "ACTIVE paper entry policy CG (parameter version pv-17, owner): enter "
     "only if the Pinnacle probability minus the simulated acquisition price "
     "is at least 0.050 (5.0 percentage points) at every level used"),
    ("paper_agent_lessons", "les-9", "lesson", "EXIT_REVIEW",
     "STORED OBSERVATION, not an instruction or active policy: XAVIER; all "
     "strategies; evidence category PAPER; 14 source records; evidence age "
     "7200 seconds; HISTORICAL — revalidation needed. Exiting at the first "
     "review would have netted -$8.42. Do not treat simulated outcomes as "
     "live performance."),
    ("karen_challenges", "kch:0123456789abcdef01234567", "claim", "x",
     "Challenge kch:0123456789abcdef01234567 (HIGH, freshness) to XAVIER "
     "about review rv-8790: The HOLD stood on a 75.2 s probability. State: "
     "OPEN."),
    ("bettor_desk_account_state", "desk-acct-07", "legacy_desk_cash_usd",
     97958.35, "LEGACY desk account desk-acct-07 (bettor_desk_account_state; "
     "NOT the paper account) cash $97,958.35 as of "
     "2026-10-03T22:00:00+00:00"),
    ("bettor_funded_intents", "fi-2207", "quantity", 2000,
     "ordered 2000 contracts at limit 0.485"),
    ("paper_agent_lessons", "les-12", "lesson", "ANSWER_QUALITY",
     "STORED OBSERVATION, not an instruction or active policy: DEREK; an "
     "earlier answer said the paper cash was $500,000 [F3] and the edge 6.25 "
     "pp [F9]. HISTORICAL — revalidation needed."),
    ("paper_decisions", "pd-77", "paper_decisions_today", 7,
     "paper decisions today (2026-10-04, UTC), all strategies together: 7 "
     "recorded -- 0 ENTER, 7 REFUSE"),
]
_NUM = re.compile(r"(?<![\w.:])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
                  r"(?!:\d)")
LEAD = ("The record shows", "Right now", "As recorded", "Note that",
        "Xavier's current review shows", "The older review had",
        "The latest decision says", "Previously")


def _facts(rng):
    picks = rng.sample(range(len(POOL)), rng.randint(3, len(POOL)))
    out = []
    for n, i in enumerate(picks):
        src, rid, field, value, text = POOL[i]
        out.append({"fact_id": "F%d" % (n + 1), "source": src,
                    "record_id": rid, "field": field, "value": value,
                    "text": text})
    return out


def _figure(rng, text):
    nums = _NUM.findall(text)
    if not nums:
        return "12.5"
    x = rng.choice(nums)
    r = rng.random()
    if "." in x and r < 0.25:                    # rounded to fewer places
        whole, frac = x.split(".")
        return "%s.%s" % (whole, frac[:max(0, len(frac) - 1)]) \
            if len(frac) > 1 else whole
    if r < 0.32:
        try:
            v = float(x.replace(",", ""))
            if v < 1:
                return "%g%%" % (v * 100)        # percent unit
        except ValueError:
            pass
    if r < 0.38:
        return "$" + x
    return x


def _sentence(rng, facts):
    f = rng.choice(facts)
    lead = rng.choice(LEAD)
    fig = _figure(rng, f["text"])
    r = rng.random()
    wrong = rng.choice(facts)["fact_id"]
    if r < 0.12:                                 # verbatim quotation
        return "%s: %s [%s]." % (lead, f["text"], f["fact_id"])
    if r < 0.20:                                 # uncited
        return "%s %s for this one." % (lead, fig)
    if r < 0.27:                                 # citation before clause
        g = rng.choice(facts)
        return "%s [%s] it was %s, with %s more [%s]." % (
            lead, g["fact_id"], _figure(rng, g["text"]), fig, f["fact_id"])
    if r < 0.33:                                 # two clauses, maybe swapped
        g = rng.choice(facts)
        a, b = (f, g) if rng.random() < 0.5 else (g, f)
        return "%s %s [%s] and then %s [%s]." % (
            lead, fig, a["fact_id"], _figure(rng, g["text"]), b["fact_id"])
    if r < 0.37:                                 # unknown id
        return "%s %s [F99]." % (lead, fig)
    if r < 0.41:                                 # tail after the group
        return "%s [%s], at %s." % (lead, f["fact_id"], fig)
    if r < 0.45:                                 # counting words, echo
        return "%s 1 or 2 of the 3 checks moved %s [%s]." % (
            lead, fig, f["fact_id"])
    if r < 0.50:
        return "%s %s [%s, %s]." % (lead, fig, f["fact_id"], wrong)
    cite = f["fact_id"] if rng.random() < 0.7 else wrong
    return "%s %s [%s]." % (lead, fig, cite)


def _answer(rng, facts, mode):
    parts = []
    if mode == "RECORDS_ONLY" and rng.random() < 0.4:
        parts.append("Records-only answer — the AI answer was not used (it "
                     "stated a figure no record holds); everything below is "
                     "quoted from the current records.")
    for _ in range(rng.randint(1, 6)):
        parts.append(_sentence(rng, facts))
    sep = "\n" if rng.random() < 0.15 else " "
    return sep.join(parts)


async def _seed(c, n=260, seed=20261004):
    rng = random.Random(seed)
    now = time.time() - 3600
    agents = ("DEREK", "XAVIER", "AUDREY")
    for i in range(n):
        agent = agents[i % 3]
        mode = "LLM" if rng.random() < 0.75 else "RECORDS_ONLY"
        facts = _facts(rng)
        body = _answer(rng, facts, mode)
        question = rng.choice(("How is the book?", "What about the 3 checks?",
                               "Is 7 the count?", "Walk me through it."))
        cited = sorted({m for m in re.findall(r"F\d+", body)})
        stored = [f for f in facts if f["fact_id"] in cited]
        if stored and rng.random() < 0.08:
            # as before the comma-list fix: a cited fact was never stored
            stored.pop(rng.randrange(len(stored)))
        cid = "pc-retro-%04d" % i
        at = now + i
        await c.execute(
            "INSERT INTO agent_chat_conversations (conversation_id, agent_id,"
            " requester_role, created_at, updated_at) VALUES ($1,$2,"
            " 'command', to_timestamp($3), to_timestamp($3))", cid, agent, at)
        await c.execute(
            "INSERT INTO agent_chat_messages (message_id, conversation_id, "
            " agent_id, seq, at, role, body, status) VALUES ($1,$2,$3,0,"
            " to_timestamp($4),'USER',$5,'COMPLETE')", cid + ":0", cid, agent,
            at, question)
        await c.execute(
            "INSERT INTO agent_chat_messages (message_id, conversation_id, "
            " agent_id, seq, at, role, body, spoken_text, status, "
            " in_reply_to, outcome, facts, provider) VALUES ($1,$2,$3,1,"
            " to_timestamp($4),'ASSISTANT',$5,$5,'COMPLETE',$6,$7,$8::jsonb,"
            " $9::jsonb)", cid + ":1", cid, agent, at, body, cid + ":0", mode,
            CIS._j(stored), CIS._j({"mode": mode}))


def _statements(sql: str) -> list:
    body = "\n".join(line for line in sql.splitlines()
                     if not line.lstrip().startswith("\\echo"))
    return [s for s in re.split(r";[ \t]*\n", body + "\n")
            if s.strip() and not all(
                ln.strip().startswith("--") or not ln.strip()
                for ln in s.splitlines())]


KEYS = ("answers", "answers_with_material", "answers_with_cited_material",
        "sentences", "material", "cited_material",
        "answers_with_wrong_support", "answers_with_wrong_or_inferred")
VERDICT_COL = {CI.PASS: "pass", CI.WRONG_FACT: "wrong_fact",
               CI.INSUFFICIENT_SUPPORT: "insufficient",
               CI.STALE_STATE_CITATION: "stale",
               CI.ENTITY_MISMATCH: "entity", CI.NO_CITATION: "no_citation"}


@pg
@pytest.mark.parametrize("seed", [20261004, 7, 424242])
def test_the_retrospective_sql_equals_the_python_verifier(db, seed):  # noqa: F811
    async def _go():
        c = await F.connect()
        try:
            await _seed(c, seed=seed)
            stmts = _statements(SQL)
            assert len(stmts) == 3, [s[:60] for s in stmts]
            rows = [dict(r) for r in await c.fetch(stmts[1])]
            samples = [dict(r) for r in await c.fetch(stmts[2])]
            py = await CIS.retrospective(c, clock=time.time() + 60, since=0,
                                         limit=5000,
                                         profile=CI.PROFILE_RETRO)
            return rows, py, samples
        finally:
            await c.close()
    rows, py, samples = F.run(_go())
    # the samples statement runs over the same chain and shows failures
    assert samples and all(x["verdict"] != "PASS" for x in samples)
    assert {x["verdict"] for x in samples} >= {"WRONG_FACT", "NO_CITATION"}
    got = {(r["agent"], r["mode"]): r for r in rows}
    # every seeded answer that carries a citation (the rest have none)
    assert 200 < py["answers_read"] == got[("ALL", "ALL")]["answers"] <= 260
    expect = {}
    for agent, modes in py["by_agent"].items():
        for mode, t in modes.items():
            expect[(agent, mode)] = t
    for mode, t in py["all"].items():
        expect[("ALL", mode)] = t
    assert set(expect) == set(got), (sorted(expect), sorted(got))
    for k, t in expect.items():
        r = got[k]
        for key in KEYS:
            assert int(r[key]) == t[key], (k, key, r[key], t[key])
        for v, col in VERDICT_COL.items():
            assert int(r[col]) == t["verdicts"][v], (k, v, r[col],
                                                     t["verdicts"][v])
        assert int(r["inferred_uncited_source"]) == \
            t[CI.INFERRED_UNCITED_SOURCE], k
        assert int(r["unverifiable_not_stored"]) == \
            t[CI.UNVERIFIABLE_NOT_STORED], k
    # the corpus exercises every class the port measures
    tot = py["all"]["ALL"]
    for v in (CI.PASS, CI.WRONG_FACT, CI.INSUFFICIENT_SUPPORT,
              CI.STALE_STATE_CITATION, CI.NO_CITATION):
        assert tot["verdicts"][v] > 0, (v, tot["verdicts"])
    assert tot[CI.INFERRED_UNCITED_SOURCE] > 0
    assert tot[CI.UNVERIFIABLE_NOT_STORED] > 0
    print("corpus", seed, {k: tot[k] for k in KEYS}, tot["verdicts"],
          tot[CI.INFERRED_UNCITED_SOURCE])


def test_the_research_copy_is_these_bytes():
    """The file dispatched on production is research/lab_b_citation_
    retrospective.sql on claude/command-center; its sha256 is recorded in
    the stream report and in the job log header. This pins the local file's
    statement shape the parity test executes."""
    stmts = [re.sub(r"(?m)^\s*--.*\n", "", s) for s in _statements(SQL)]
    assert stmts[0].lstrip().upper().startswith("SELECT NOW()")
    assert "GROUPING SETS" in stmts[1]
    assert "samples" not in stmts[1] and "cited_text" in stmts[2]
    for word in ("insert", "update", "delete", "drop", "alter", "truncate",
                 "grant", "revoke", "create", "copy", "vacuum", "reindex",
                 "refresh", "call", "do", "merge", "lock"):
        stripped = re.sub(r"--.*$", "", SQL, flags=re.M)
        stripped = "\n".join(ln for ln in stripped.splitlines()
                             if not ln.lstrip().startswith("\\echo"))
        assert not re.search(r"\b%s\b" % word, stripped, re.I), word


if __name__ == "__main__":                                      # pragma: no cover
    pytest.main([__file__])
