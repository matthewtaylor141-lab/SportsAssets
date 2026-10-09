"""Account-backed PAPER epochs. No live authority and no automatic activation.

Activation verifies a signed engineering packet, then switches one durable
selector and creates one funded account in a single transaction. Outstanding
positions/orders/reserves refuse the switch; historical corrections remain on
their original account. Rollback is allowed only while the new account is flat.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from decimal import Decimal

from . import bettor_paper_ledger as L
from . import bettor_paper_session as S

LOCK = 0x50415052  # same lock as the scheduled PAPER pass
OPENING = Decimal('500000.000000')


class EpochRefused(ValueError):
    pass


def epoch_account(epoch_id):
    if not isinstance(epoch_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', epoch_id):
        raise EpochRefused('MALFORMED_EPOCH_ID')
    return 'paper_day_one_' + hashlib.sha256(epoch_id.encode()).hexdigest()[:32]


async def selected_account(conn):
    from .simulated_account_context import selected_account as resolve
    return await resolve(conn)


async def flat_receipt(conn, account):
    await L._lock(conn, account)
    bal = await L.balances(conn, account)
    if not bal.get('ok'):
        raise EpochRefused('PAPER_ACCOUNT_UNREADABLE')
    cs = await L.cash_state(conn, account)
    orders = await conn.fetchval('SELECT count(*) FROM paper_orders WHERE account_id=$1 AND state=ANY($2::text[])', account, list(L.OPEN_STATES))
    positions = await L.positions(conn, account)
    if not cs['running_balance_agrees']:
        raise EpochRefused('PAPER_ACCOUNT_DOES_NOT_RECONCILE')
    if orders or cs['reserved'] != 0 or any(Decimal(str(p['open_qty'])) != 0 for p in positions):
        raise EpochRefused('PAPER_ACCOUNT_HAS_OUTSTANDING_OBLIGATIONS')
    return {'account_id': account, 'cash_usd': str(cs['cash']),
            'reserved_usd': str(cs['reserved']), 'last_sequence': cs['last_seq'],
            'ledger_entries': cs['entries'], 'balances': bal}


async def _activate_verified(conn, *, epoch_id, request_id, proof, deployment_check=None):
    """Internal only; public activate() obtains proof from signature verification."""
    account = epoch_account(epoch_id)
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
        control = await conn.fetchrow('SELECT * FROM paper_epoch_control WHERE singleton FOR UPDATE')
        if deployment_check is not None:
            proof = dict(proof, deployed_services=await deployment_check(proof['release_sha']))
        if 'valid_until' in proof:
            at = await conn.fetchval('SELECT extract(epoch FROM clock_timestamp())::float8')
            if at > proof['valid_until']:
                raise EpochRefused('ACCEPTANCE_EXPIRED_WAITING_FOR_ACCOUNT_LOCK')
        old = control['account_id']
        existing = await conn.fetchrow('SELECT * FROM paper_account_epochs WHERE epoch_id=$1', epoch_id)
        if existing:
            if old != account or existing['acceptance_digest'] != proof['packet_digest'] or existing['release_sha'] != proof['release_sha']:
                raise EpochRefused('EPOCH_ALREADY_EXISTS_WITH_DIFFERENT_CONTEXT')
            return await read(conn, account)
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_epoch_events WHERE request_id=$1)', request_id):
            raise EpochRefused('REQUEST_ID_REUSED')
        history = await flat_receipt(conn, old)
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_accounts WHERE account_id=$1)', account):
            raise EpochRefused('DAY_ONE_ACCOUNT_ALREADY_EXISTS')
        session = await S.active_session(conn, old)
        if not session:
            raise EpochRefused('NO_SOURCE_SESSION_FOR_FROZEN_POLICY')
        # Preserve effective risk policy; no defaults substitute for owner settings.
        at = await conn.fetchval('SELECT extract(epoch FROM clock_timestamp())::float8')
        cfg = dict(session.get('effective_config') or session['config'])
        cfg['account_id'] = account
        if cfg.get('capital_policy'):
            cfg['capital_policy'] = dict(cfg['capital_policy'], account_id=account, inherited_from_account_id=old)
        cfg['epoch_id'] = epoch_id
        cfg['epoch_opened_at'] = at
        cfg['historical_account_id'] = old
        cfg['model_provenance'] = {'history_preserved': True, 'accounting_metrics_account_local': True,
                                   'risk_and_learning_inherit_epoch_lineage': True,
                                   'opening_acceptance_digest': proof['packet_digest']}
        sess = await S.ensure_session(conn, account_id=account, config=cfg, now=at)
        if not sess.get('ok'):
            raise EpochRefused('NEW_SESSION_INITIALIZATION_FAILED')
        opening = await flat_receipt(conn, account)
        cs = await L.cash_state(conn, account)
        if cs['cash'] != OPENING or cs['available'] != OPENING or cs['entries'] != 1:
            raise EpochRefused('DAY_ONE_OPENING_DOES_NOT_RECONCILE')
        from . import bettor_paper_readmodel as RM
        await RM.snapshot_equity(conn, session_id=sess['session_id'], account_id=account, now=at)
        await conn.execute('INSERT INTO paper_account_epochs(epoch_id,account_id,previous_account_id,opened_at,release_sha,acceptance_digest,opening_equity_usd,opening_receipt,historical_receipt) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)',
                           epoch_id, account, old, L._ts(at), proof['release_sha'], proof['packet_digest'], OPENING,
                           json.dumps(opening, default=str), json.dumps(history, default=str))
        await conn.execute('UPDATE paper_epoch_control SET account_id=$1,generation=generation+1 WHERE singleton', account)
        await conn.execute("INSERT INTO paper_epoch_events(request_id,kind,epoch_id,from_account_id,to_account_id,detail) VALUES($1,'ACTIVATE',$2,$3,$4,$5::jsonb)", request_id, epoch_id, old, account, json.dumps(proof))
        return await read(conn, account)


async def activate(conn, *, epoch_id, request_id, evidence_dir, release_sha, bundle):
    from .paper_epoch_acceptance import verify_acceptance, verify_deployed_release
    proof = verify_acceptance(evidence_dir, release_sha=release_sha, bundle=bundle)
    return await _activate_verified(conn, epoch_id=epoch_id, request_id=request_id,
        proof=proof, deployment_check=verify_deployed_release)


async def rollback(conn, *, epoch_id, request_id):
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock($1)', LOCK)
        control = await conn.fetchrow('SELECT * FROM paper_epoch_control WHERE singleton FOR UPDATE')
        epoch = await conn.fetchrow('SELECT * FROM paper_account_epochs WHERE epoch_id=$1', epoch_id)
        if not epoch:
            raise EpochRefused('UNKNOWN_EPOCH')
        event = await conn.fetchrow('SELECT * FROM paper_epoch_events WHERE request_id=$1', request_id)
        if event:
            if event['kind'] != 'ROLLBACK' or event['epoch_id'] != epoch_id:
                raise EpochRefused('REQUEST_ID_REUSED')
            return {'rolled_back': True, 'account_id': event['to_account_id'], 'idempotent': True}
        if control['account_id'] != epoch['account_id']:
            raise EpochRefused('EPOCH_IS_NOT_SELECTED')
        receipt = await flat_receipt(conn, epoch['account_id'])
        await flat_receipt(conn, epoch['previous_account_id'])
        await conn.execute('UPDATE paper_epoch_control SET account_id=$1,generation=generation+1 WHERE singleton', epoch['previous_account_id'])
        await conn.execute("INSERT INTO paper_epoch_events(request_id,kind,epoch_id,from_account_id,to_account_id,detail) VALUES($1,'ROLLBACK',$2,$3,$4,$5::jsonb)", request_id, epoch_id, epoch['account_id'], epoch['previous_account_id'], json.dumps(receipt, default=str))
        return {'rolled_back': True, 'account_id': epoch['previous_account_id'], 'idempotent': False}


async def read(conn, account=None, *, bal=None):
    from . import bettor_paper_epoch as EP
    return await EP.read_account_epoch(conn, account, bal=bal)
