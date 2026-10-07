"""Canonical entity registry with collision-safe alias resolution."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

VERSION = "ENTITY_REGISTRY_V1"


def fold(v) -> str:
    t = unicodedata.normalize("NFKD", str(v or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", t))


@dataclass
class Entity:
    entity_id: str
    kind: str
    canonical_name: str
    competition: str | None = None
    aliases: set[str] = field(default_factory=set)


class Registry:
    def __init__(self):
        self.entities: dict[str, Entity] = {}
        self.aliases: dict[tuple[str, str | None, str], set[str]] = {}

    def add(self, entity: Entity):
        if entity.entity_id in self.entities and self.entities[entity.entity_id] != entity:
            raise ValueError("entity id collision")
        self.entities[entity.entity_id] = entity
        for alias in {entity.canonical_name, *entity.aliases}:
            k = (entity.kind, entity.competition, fold(alias))
            self.aliases.setdefault(k, set()).add(entity.entity_id)

    def resolve(self, text, *, kind: str, competition: str | None = None) -> dict:
        key = (kind, competition, fold(text))
        ids = self.aliases.get(key, set())
        if len(ids) == 1:
            eid = next(iter(ids)); e = self.entities[eid]
            return {"status": "EXACT", "entity_id": eid,
                    "canonical_name": e.canonical_name, "version": VERSION}
        if len(ids) > 1:
            return {"status": "AMBIGUOUS", "entity_ids": sorted(ids),
                    "version": VERSION}
        if competition is not None:
            anycomp = set()
            target = fold(text)
            for (k_kind, _comp, alias), vals in self.aliases.items():
                if k_kind == kind and alias == target:
                    anycomp |= vals
            if len(anycomp) == 1:
                return {"status": "COMPETITION_MISMATCH", "entity_ids": sorted(anycomp),
                        "version": VERSION}
        return {"status": "UNRESOLVED", "version": VERSION}
