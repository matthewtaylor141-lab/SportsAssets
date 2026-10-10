"""Prospective protocols using the EXISTING paper-learning evaluator/authority."""
from __future__ import annotations
import json
import math
from . import capability_work as W


def catalog():
    from . import paper_learning as P
    return {'classes':P.CHANGE_CLASSES,'parameter_bounds':P.PARAM_BOUNDS,
            'max_parameter_step_pp':P.PARAM_MAX_STEP_PP,
            'parameter_policy':P.PARAM_POLICY,
            'parameter_grid_pp':P.PARAM_GRID_PP}


def protocol(body,now,classes):
    cls=classes.get(body.get('change_class'))
    if not cls:raise ValueError('UNSUPPORTED_EXPERIMENT_CLASS')
    train=body.get('training');evaluate=body.get('evaluation')
    if not isinstance(train,list) or not isinstance(evaluate,list) or len(train)!=2 or len(evaluate)!=2:raise ValueError('TWO_PERIODS_REQUIRED')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in train+evaluate):raise ValueError('FINITE_TIMES_REQUIRED')
    if not train[0]<train[1]<=now<=evaluate[0]<evaluate[1]:raise ValueError('FORWARD_ONLY_SPLIT_REQUIRED')
    if evaluate[1]-evaluate[0]<86400 or evaluate[1]>now+90*86400:raise ValueError('EVALUATION_1_TO_90_DAYS_REQUIRED')
    n=body.get('min_outcomes',cls['min_evaluation_outcomes'])
    if type(n) is not int or not cls['min_evaluation_outcomes']<=n<=10000:raise ValueError('SAMPLE_FLOOR_CANNOT_BE_LOWERED')
    if not isinstance(body.get('rationale'),str) or not 10<=len(body['rationale'])<=2000:raise ValueError('HYPOTHESIS_REQUIRED')
    return cls,train,evaluate,n


async def register(conn,body,*,actor,now,account=None):
    from . import paper_learning as P, registry as R
    key=body.get('request_id')
    if not isinstance(key,str) or not 8<=len(key)<=100:raise ValueError('REQUEST_ID_REQUIRED')
    lesson_ids=body.get('lesson_ids',[])
    if not isinstance(lesson_ids,list) or len(lesson_ids)>10 or any(not isinstance(x,str) or len(x)>150 for x in lesson_ids):raise ValueError('INVALID_LESSONS')
    acct=await W.scope(conn,account)
    tid='capexp:'+W.stable(acct,key)
    digest=W.stable(body)
    async with conn.transaction():
        await W.control(conn,True,account=acct)
        existing=W.row(await conn.fetchrow('SELECT * FROM agent_tasks WHERE task_id=$1',tid))
        if existing:
            if existing['spec'].get('request_digest')!=digest:raise ValueError('REQUEST_IDEMPOTENCY_MISMATCH')
            return existing['outcome']
        cls,train,evaluate,n=protocol(body,now,P.CHANGE_CLASSES)
        valid=await conn.fetchval('SELECT count(*) FROM paper_agent_lessons WHERE account_id=$1 AND agent_id=$2 AND lesson_id=ANY($3::text[])',acct,cls['agent'],lesson_ids)
        if valid!=len(set(lesson_ids)):raise ValueError('LESSON_SCOPE_MISMATCH')
        out=await P.create_proposal(conn,account_id=acct,agent_id=cls['agent'],strategy=body.get('strategy'),change_class=body['change_class'],proposed_change=body.get('change') or {},rationale=body['rationale'],proposed_by=actor,proposed_at=now,training=tuple(train),evaluation=tuple(evaluate),source_lesson_ids=lesson_ids,min_evaluation_outcomes=n)
        if not out.get('ok'):raise ValueError(out.get('refusal','PROPOSAL_REFUSED'))
        task=await R.create_task(conn,assignee='AUDREY',created_by=actor,kind='CAPABILITY_EXPERIMENT_V1',title=body['rationale'][:150],spec={'account_id':acct,'request_digest':digest,'proposal_id':out['proposal_id']},task_id=tid,now=now)
        if not task.get('ok'):raise RuntimeError('EXPERIMENT_TASK_WRITE_FAILED')
        await conn.execute("UPDATE agent_tasks SET status='EVALUATING',outcome=$2::jsonb WHERE task_id=$1",tid,json.dumps(out))
        return out


async def evaluate_due(conn,now,*,account=None):
    from . import paper_learning as P
    acct=await W.scope(conn,account)
    r=await conn.fetchrow("SELECT p.proposal_id FROM paper_improvement_proposals p JOIN agent_tasks t ON t.spec->>'proposal_id'=p.proposal_id WHERE t.kind='CAPABILITY_EXPERIMENT_V1' AND t.status='EVALUATING' AND t.spec->>'account_id'=$1 AND p.account_id=$1 AND p.status IN ('AWAITING_FORWARD_DATA','INSUFFICIENT_FORWARD_DATA','EVALUATED') AND p.evaluation_end<=to_timestamp($2) AND coalesce((t.spec->>'last_evaluation_attempt')::double precision,0)<$2-300 ORDER BY coalesce((t.spec->>'last_evaluation_attempt')::double precision,0),p.proposed_at LIMIT 1 FOR UPDATE OF p,t SKIP LOCKED",acct,now)
    if not r:return {'evaluated':False,'why':'NO_DUE_PROTOCOL'}
    out=await P.evaluate_proposal(conn,r['proposal_id'],now=now)
    await conn.execute("UPDATE agent_tasks SET spec=jsonb_set(spec,'{last_evaluation_attempt}',to_jsonb($2::double precision)) WHERE kind='CAPABILITY_EXPERIMENT_V1' AND spec->>'proposal_id'=$1",r['proposal_id'],now)
    if out.get('evaluated') or out.get('already_evaluated'):
        ts=await conn.fetch('SELECT task_id FROM agent_tasks WHERE kind=$1 AND spec->>\'proposal_id\'=$2','CAPABILITY_EXPERIMENT_V1',r['proposal_id'])
        for t in ts:
            await conn.execute("UPDATE agent_tasks SET status='CLOSED_NO_CHANGE',outcome=$2::jsonb,updated_at=to_timestamp($3) WHERE task_id=$1",t['task_id'],json.dumps(out,default=str),now)
            await W.event(conn,t['task_id'],'EVALUATION_RECORDED','SYSTEM',{'proposal_id':r['proposal_id'],'status':out.get('status'),'activation':'EXISTING_SEPARATE_CONTROL_ONLY'},now)
    return out
