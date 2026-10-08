from __future__ import annotations

EXPECTED={
  "PMX":"POLYMARKET_EXCHANGE_RSA_M2M",
  "PMUS":"POLYMARKET_US_ED25519",
  "KALSHI":"KALSHI_RSA_API_KEY",
}

def credential_slot_gate(slots:dict[str,str])->dict:
    """slots = logical slot -> detected credential class. Never pass secrets."""
    blockers=[]
    for slot,expected in EXPECTED.items():
        actual=slots.get(slot)
        if actual is None:
            blockers.append(f"MISSING_CREDENTIAL_CLASS:{slot}")
        elif actual!=expected:
            blockers.append(f"CREDENTIAL_CLASS_MISMATCH:{slot}:{actual}")
    return {"green":not blockers,"blockers":tuple(blockers)}
