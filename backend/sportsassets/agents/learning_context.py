"""Bounded evidence retrieval for agent conversation, never trading authority.

Selection is deterministic and inspectable. A lesson is an observation with a
scope and date, not an instruction, validated model, or proof of profitability.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re

MAX_CANDIDATES = 48
MAX_LESSONS = 3
MAX_STATEMENT = 1200
STALE_AFTER_S = 14 * 86400

LESSON_QUERY = """
SELECT l.lesson_id,l.account_id,l.agent_id,l.kind,l.strategy,l.series_key,
       l.version,l.statement,l.window_end,l.learned_at,l.provenance,
       l.evidence_category,l.basis,l.improvement_task_id
FROM paper_agent_lessons l
WHERE l.account_id=$1 AND l.agent_id=$2
  AND NOT EXISTS (SELECT 1 FROM paper_agent_lessons n
                  WHERE n.account_id=l.account_id AND n.series_key=l.series_key
                    AND n.version>l.version)
ORDER BY l.learned_at DESC,l.lesson_id
LIMIT $3
"""


def _epoch(value):
    try:
        if isinstance(value, dt.datetime):
            if value.tzinfo is None:
                return None
            value = value.timestamp()
        elif isinstance(value, str):
            value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return _epoch(value)
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _terms(text):
    words=set(re.findall(r'[a-z]{4,}', str(text).lower()))
    groups=(('fees','cost','profit','economics'),('stale','freshness','latency','delay'),
            ('fill','fills','execution','liquidity'),('exit','hold','protection','management'),
            ('audit','ledger','reconcile','reconciliation'))
    for group in groups:
        if words.intersection(group):
            words.update(group)
    return words - {'what','when','which','with','this','that','have','does','from','about'}


def select(rows, *, account_id, agent, question, now):
    """Rank the latest scoped versions; exclude malformed/future/no-evidence rows."""
    if not math.isfinite(now):
        raise ValueError('finite observation time required')
    latest={}
    rejected=[]
    for raw in rows[:MAX_CANDIDATES]:
        r=dict(raw)
        if r.get('account_id')!=account_id or r.get('agent_id')!=agent.upper():
            rejected.append('SCOPE_MISMATCH');continue
        key=r.get('series_key')
        if not key or not isinstance(r.get('version'),int) or r['version']<1:
            rejected.append('INVALID_VERSION');continue
        if key not in latest or r['version']>latest[key]['version']:
            latest[key]=r
    ranked=[]
    for r in latest.values():
        stamp,learned=_epoch(r.get('window_end')),_epoch(r.get('learned_at'))
        if stamp is None or learned is None or stamp>now or learned>now:
            rejected.append('INVALID_OR_FUTURE_TIME');continue
        provenance=r.get('provenance')
        if isinstance(provenance,str):
            try: provenance=json.loads(provenance)
            except ValueError: provenance=None
        count=provenance.get('record_count') if isinstance(provenance,dict) else None
        digest=provenance.get('ids_sha256') if isinstance(provenance,dict) else None
        if (r.get('basis')!='FORWARD_RECORDS_ONLY' or not isinstance(count,int)
                or isinstance(count,bool) or count<1 or not isinstance(digest,str)
                or not re.fullmatch(r'[a-fA-F0-9]{64}',digest)
                or not r.get('lesson_id') or not r.get('statement')
                or not r.get('evidence_category')):
            rejected.append('MISSING_EVIDENCE');continue
        overlap=len(_terms(question)&_terms(r['kind']+' '+r['statement']))
        r.update(age_s=now-stamp,historical=now-stamp>STALE_AFTER_S,
                 relevance_terms=overlap,record_count=count,
                 statement=r['statement'][:MAX_STATEMENT],
                 statement_shortened=len(r['statement'])>MAX_STATEMENT)
        ranked.append(r)
    ranked.sort(key=lambda r:(-r['relevance_terms'],r['historical'],r['age_s'],r['lesson_id']))
    chosen=ranked[:MAX_LESSONS]
    return {'lessons':chosen,'considered':len(rows[:MAX_CANDIDATES]),
            'rejected':rejected,'candidate_limit':MAX_CANDIDATES,
            'selection':'question relevance, recent evidence, deterministic tie-break',
            'authority':'OBSERVATIONS_ONLY_NO_POLICY_CHANGE',
            'limitations':'Bounded retrieval, not exhaustive memory. Historical observations need revalidation. Simulation, counterfactuals and audits do not establish live profitability.'}


async def retrieve(conn, *, account_id, agent, question, now):
    import asyncio
    async with asyncio.timeout(2.0):
        rows=await conn.fetch(LESSON_QUERY,account_id,agent.upper(),MAX_CANDIDATES)
    return select(rows,account_id=account_id,agent=agent,question=question,now=now)
