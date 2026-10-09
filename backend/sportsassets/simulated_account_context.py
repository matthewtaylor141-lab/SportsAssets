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
