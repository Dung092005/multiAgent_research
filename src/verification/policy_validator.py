"""Compare a proposed policy decision to the deterministic engine result."""

from src.schemas.agent_reports import PolicyDecision, VerificationError


def validate_policy(proposed: PolicyDecision, expected: PolicyDecision) -> list[VerificationError]:
    errors: list[VerificationError] = []
    comparisons = {
        "primary_issue": (proposed.primary_issue, expected.primary_issue),
        "case_status": (proposed.case_status, expected.case_status),
        "ranked_causes": (proposed.ranked_causes, expected.ranked_causes),
        "responsible_parties": (proposed.responsible_parties, expected.responsible_parties),
        "resolution_actions": (proposed.resolution_actions, expected.resolution_actions),
    }
    for field, (actual, wanted) in comparisons.items():
        if actual != wanted:
            errors.append(
                VerificationError(
                    code="POLICY_MISMATCH",
                    field=field,
                    message=f"{field} differs from deterministic policy",
                    expected=str(wanted),
                    actual=str(actual),
                    repair_target="policy_agent",
                )
            )
    return errors
