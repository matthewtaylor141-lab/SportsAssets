"""Operator-only PAPER epoch command. Default read-only, never runs at boot."""
import argparse
import asyncio
import json

from .. import bettor_day_one as E
from ..db import get_pool


async def run(args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if args.action == 'activate':
            result = await E.activate(conn, epoch_id=args.epoch_id, request_id=args.request_id,
                evidence_dir=args.evidence_dir, release_sha=args.release_sha, bundle=args.bundle,
                trusted_root=args.trusted_root, gh=args.gh)
        elif args.action == 'rollback':
            result = await E.rollback(conn, epoch_id=args.epoch_id, request_id=args.request_id)
        else:
            result = await E.read(conn)
    print(json.dumps(result, default=str))
    await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['read', 'activate', 'rollback'], default='read', nargs='?')
    for name in ('epoch-id', 'request-id', 'evidence-dir', 'release-sha', 'bundle', 'trusted-root'):
        parser.add_argument('--' + name)
    parser.add_argument('--gh', default='gh')
    args = parser.parse_args()
    required = ['epoch_id', 'request_id'] if args.action != 'read' else []
    if args.action == 'activate':
        required += ['evidence_dir', 'release_sha', 'bundle', 'trusted_root']
    if any(not getattr(args, name) for name in required):
        parser.error('missing arguments required by action: ' + ', '.join(required))
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
