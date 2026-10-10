"""Read-only durable PAPER account selection; no ledger or order imports."""

LEGACY_ACCOUNT = 'paper_acct_main'


async def selected_account(conn):
    if not await conn.fetchval("SELECT to_regclass('paper_epoch_control') IS NOT NULL"):
        return LEGACY_ACCOUNT
    account = await conn.fetchval('SELECT account_id FROM paper_epoch_control WHERE singleton')
    if not isinstance(account, str) or not account.startswith('paper'):
        raise ValueError('PAPER_EPOCH_SELECTOR_UNREADABLE')
    if account != LEGACY_ACCOUNT and not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs WHERE account_id=$1)', account):
        raise ValueError('PAPER_EPOCH_SELECTOR_NOT_REGISTERED')
    return account


async def account_lineage(conn, account):
    """Nearest account first; only registered epoch edges carry history.

    Cycles, malformed edges and excessive depth refuse rather than discard
    losses. An unregistered legacy account has no inherited history.
    """
    if not await conn.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL"):
        return [account]
    out = []
    for _ in range(64):
        if not isinstance(account, str) or not account or account in out:
            raise ValueError('PAPER_EPOCH_LINEAGE_INVALID')
        out = out + [account]
        parent = await conn.fetchval('SELECT previous_account_id FROM paper_account_epochs WHERE account_id=$1', account)
        if parent is None:
            return out
        account = parent
    raise ValueError('PAPER_EPOCH_LINEAGE_TOO_DEEP')


async def risk_history_accounts(conn, account):
    """Every registered epoch of the same root, including rolled-back ones.

    Logical rollback cannot erase a child's losses from the risk population.
    Unrelated legacy accounts stay isolated; malformed/deep ancestry refuses.
    """
    lineage = await account_lineage(conn, account)
    if not await conn.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL"):
        return lineage
    rows = await conn.fetch('SELECT account_id, previous_account_id FROM paper_account_epochs')
    family = [lineage[-1]]
    for _ in range(64):
        children = [r['account_id'] for r in rows
                    if r['previous_account_id'] in family and r['account_id'] not in family]
        if not children:
            return [account] + [aid for aid in family if aid != account]
        family = family + children
    raise ValueError('PAPER_EPOCH_RISK_HISTORY_TOO_DEEP')
