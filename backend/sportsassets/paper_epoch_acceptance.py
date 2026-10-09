"""Fail-closed independent engineering proof for PAPER account activation.

No caller-supplied verified=True flag. Verify the actual packet and manifest
with Sigstore, bind the source verdict, and recompute every category's rates.
Files are snapshotted once so validation cannot race changes to the input dir.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import subprocess
import tempfile
import time
from decimal import Decimal

from .bettor_paper_day_one import EpochRefused

REPO = 'matthewtaylor141-lab/SportsAssets'
WORKFLOW = REPO + '/.github/workflows/pm-acceptance.yml'
CATEGORIES = frozenset({'Core trading engine', 'GitHub CI and regression tests',
 'Red-team safeguards', 'Deployment infrastructure', 'Market-plane stability',
 'Data freshness and latency', 'Sports and market coverage', 'EV and pricing methodology',
 'Xavier and agent coordination', 'Archer and reconciliation', 'Adriana cross-venue arbitrage',
 'Risk management and capital controls', 'Kalshi integration', 'Command Center desktop and mobile'})


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EpochRefused('ENGINEERING_RATE_UNMEASURED')
    return Decimal(str(value))


def validate_contents(files, *, release_sha, now):
    def load(name):
        try:
            value = json.loads(files[name])
            if not isinstance(value, dict):
                raise TypeError("object required")
            return value
        except (KeyError, ValueError, TypeError) as exc:
            raise EpochRefused('ACCEPTANCE_SOURCE_MALFORMED:' + name) from exc
    packet = load('evidence_packet.json')
    acceptance = load('acceptance.json')
    score = load('scorecard_14.json')
    gates = load('gates.json')
    ref = packet.get('acceptance', {}).get('independent_pm_state')
    if ref != {'source': 'acceptance.json', 'path': 'independent_pm_state', 'value': 'GREEN'} or acceptance.get('independent_pm_state') != 'GREEN':
        raise EpochRefused('SIGNED_CONTENTS_NOT_CONSISTENT_GREEN')
    if acceptance.get('critical_failures') != [] or acceptance.get('unproven') != {}:
        raise EpochRefused('ENGINEERING_REDS_OR_UNPROVEN')
    built = packet.get('built_at')
    if isinstance(built, bool) or not isinstance(built, (int, float)) or not math.isfinite(built) or not 0 <= now-built <= 900:
        raise EpochRefused('ACCEPTANCE_WINDOW_NOT_CURRENT')
    if score.get('release_sha') != release_sha or score.get('target') != .95:
        raise EpochRefused('ACCEPTANCE_RELEASE_OR_THRESHOLD_MISMATCH')
    rows = score.get('categories', [])
    if len(rows) != 14 or {r.get('category') for r in rows} != CATEGORIES:
        raise EpochRefused('ENGINEERING_CATEGORIES_INCOMPLETE')
    for row in rows:
        if row.get('passes') is not True or row.get('unreadable_units') != [] or _number(row.get('readiness')) < Decimal('.95'):
            raise EpochRefused('ENGINEERING_CATEGORY_NOT_ACCEPTED:' + row['category'])
        if not row.get('components') or any(u.get('class') == 'FAIL' for u in row.get('units', [])):
            raise EpochRefused('ENGINEERING_CATEGORY_HAS_REDS')
        for component in row['components']:
            n, d = _number(component.get('numerator')), _number(component.get('denominator'))
            if d <= 0 or not 0 <= n <= d or n / d < Decimal('.95'):
                raise EpochRefused('ENGINEERING_RATE_BELOW_TARGET')
    for name in ('backend_tests', 'capital_critical', 'commit_guard', 'engine_diagnostic'):
        run = gates.get('runs', {}).get(name, {})
        if run.get('head_sha') != release_sha or run.get('status') != 'completed' or run.get('conclusion') != 'success':
            raise EpochRefused('EXACT_SHA_GATE_NOT_GREEN:' + name)
    return {'release_sha': release_sha, 'packet_digest': hashlib.sha256(files['evidence_packet.json']).hexdigest(),
            'verified_at': now, 'valid_until': built + 900, 'signer_workflow': WORKFLOW}


def verify_acceptance(evidence_dir, *, release_sha, bundle, trusted_root, gh='gh', now=None):
    if not re.fullmatch('[0-9a-f]{40}', release_sha):
        raise EpochRefused('NOT_A_FULL_RELEASE_SHA')
    root = pathlib.Path(evidence_dir).resolve()
    manifest = (root/'SHA256SUMS').read_bytes()
    files = {}
    for line in manifest.decode().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise EpochRefused('MALFORMED_HASH_MANIFEST')
        digest, name = match.groups()
        path = root/name
        if pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise EpochRefused('UNSAFE_MANIFEST_PATH')
        normalized = str(pathlib.Path(name))
        if normalized in files:
            raise EpochRefused('DUPLICATE_MANIFEST_FILE')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise EpochRefused('HASH_MISMATCH:' + normalized)
        files[normalized] = content
    files['SHA256SUMS'] = manifest
    with tempfile.TemporaryDirectory(prefix='bettor-epoch-proof-') as tmp:
        for name in ('evidence_packet.json', 'SHA256SUMS'):
            if name not in files:
                raise EpochRefused('SIGNED_SUBJECT_MISSING')
            path = pathlib.Path(tmp)/name
            path.write_bytes(files[name])
            try:
                result = subprocess.run([gh, 'attestation', 'verify', str(path), '--bundle', str(bundle),
                    '--repo', REPO, '--signer-workflow', WORKFLOW, '--deny-self-hosted-runners',
                    '--custom-trusted-root', str(trusted_root)], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise EpochRefused('SIGNATURE_VERIFICATION_UNAVAILABLE') from exc
            if result.returncode != 0:
                raise EpochRefused('SIGNATURE_VERIFICATION_FAILED:' + name)
    return validate_contents(files, release_sha=release_sha, now=time.time() if now is None else now)
