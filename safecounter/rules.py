"""The rules from data/roles_and_fields.json: read once, never written.

The server derives sensitivity and approvers from these rules and the actual value
(rule R6). Nothing the AI sends can change them.
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

from . import config

ENTITY_TABLE = {"rental": "rentals", "item": "items", "customer": "customers"}
ROLES = ("counter", "technician", "manager", "area_admin")


@dataclass(frozen=True)
class FieldRule:
    field: str            # e.g. "rental.hire_days"
    entity: str           # "rental"
    column: str           # "hire_days"
    type: str
    unit: str
    sensitive: bool
    may_change: frozenset
    approvers: frozenset  # for a normal field: anyone who may change it
    ai_may_propose: bool
    spec: dict            # the raw entry: values, min, max, max_length, ...

    @property
    def table(self) -> str:
        return ENTITY_TABLE[self.entity]


@dataclass(frozen=True)
class Effective:
    """What applies to one field with one specific value."""
    sensitive: bool
    may_change: frozenset
    approvers: frozenset
    meaning: str | None = None   # e.g. "write-off"


class Rules:
    def __init__(self, raw: bytes):
        self.sha256 = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        self.time_zone = doc["time_zone"]
        self.fields: dict[str, FieldRule] = {}
        for f in doc["fields"]:
            entity, column = f["field"].split(".", 1)
            may = frozenset(f["may_change"])
            self.fields[f["field"]] = FieldRule(
                field=f["field"], entity=entity, column=column, type=f["type"], unit=f["unit"],
                sensitive=bool(f["sensitive"]), may_change=may,
                approvers=frozenset(f.get("approvers") or may),
                ai_may_propose=bool(f["ai_may_propose"]), spec=f,
            )

    def get(self, field) -> FieldRule | None:
        return self.fields.get(field) if isinstance(field, str) else None

    def effective(self, rule: FieldRule, value) -> Effective:
        """Apply value rules (such as item.status = retired being a write-off)."""
        for vr in rule.spec.get("value_rules", []):
            if value == vr["when_value"]:
                may = frozenset(vr.get("may_change", rule.may_change))
                return Effective(
                    sensitive=bool(vr.get("sensitive", rule.sensitive)),
                    may_change=may,
                    approvers=frozenset(vr.get("approvers") or may),
                    meaning=vr.get("meaning"),
                )
        return Effective(rule.sensitive, rule.may_change, rule.approvers)


@lru_cache(maxsize=1)
def load() -> Rules:
    return Rules(config.RULES_PATH.read_bytes())
