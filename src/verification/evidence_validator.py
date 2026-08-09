"""Evidence parsing, formatting, and deterministic prioritization."""

import re
from dataclasses import dataclass
from typing import Literal

from src.errors import EvidenceValidationError
from src.policy.rules import ROOT_CAUSE_CODES

ID = r"[A-Za-z0-9_-]+"
PATTERNS = {
    "order": re.compile(rf"^order:(?P<order>{ID})$"),
    "item": re.compile(rf"^item:(?P<order>{ID}):(?P<sequence>\d+)$"),
    "payment": re.compile(rf"^payment:(?P<order>{ID}):(?P<sequence>\d+)$"),
    "seller": re.compile(rf"^seller:(?P<seller>{ID})$"),
    "policy": re.compile(r"^policy:(?P<code>[A-Z_]+)$"),
}


@dataclass(frozen=True)
class ParsedEvidence:
    kind: Literal["order", "item", "payment", "seller", "policy"]
    order_id: str | None = None
    sequence: int | None = None
    seller_id: str | None = None
    policy_code: str | None = None


def parse_evidence_id(evidence_id: str) -> ParsedEvidence:
    for kind, pattern in PATTERNS.items():
        match = pattern.fullmatch(evidence_id)
        if not match:
            continue
        groups = match.groupdict()
        if kind == "policy" and groups["code"] not in ROOT_CAUSE_CODES:
            raise EvidenceValidationError(f"Unknown policy evidence: {evidence_id}")
        return ParsedEvidence(
            kind=kind,  # type: ignore[arg-type]
            order_id=groups.get("order"),
            sequence=int(groups["sequence"]) if groups.get("sequence") else None,
            seller_id=groups.get("seller"),
            policy_code=groups.get("code"),
        )
    raise EvidenceValidationError(f"Invalid evidence ID: {evidence_id}")


def prioritize_evidence(
    order_id: str,
    policy_code: str,
    item_ids: list[str],
    payment_ids: list[str],
    seller_ids: list[str],
) -> list[str]:
    candidates = [f"order:{order_id}", f"policy:{policy_code}"]
    candidates.extend(f"item:{item_id}" for item_id in item_ids)
    candidates.extend(f"payment:{payment_id}" for payment_id in payment_ids)
    candidates.extend(f"seller:{seller_id}" for seller_id in seller_ids)
    unique = list(dict.fromkeys(candidates))[:10]
    for evidence_id in unique:
        parse_evidence_id(evidence_id)
    return unique
