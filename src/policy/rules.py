"""Policy configuration loader and supported constants."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from src.errors import ConfigurationError


@dataclass(frozen=True)
class RuleDefinition:
    primary_issue: str
    root_cause: str
    party_type: str | None
    party_id: str | None
    refund_basis: str
    action: str


@lru_cache(maxsize=1)
def load_rules() -> tuple[RuleDefinition, ...]:
    path = Path(__file__).resolve().parents[2] / "config" / "policy_v1.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read policy configuration: {path}") from exc
    if payload.get("version") != "EC_POLICY_V1":
        raise ConfigurationError("Unsupported policy configuration version")
    return tuple(RuleDefinition(**row) for row in payload.get("rules", []))


ROOT_CAUSE_CODES = frozenset(rule.root_cause for rule in load_rules())
PRIMARY_ISSUES = tuple(rule.primary_issue for rule in load_rules())
