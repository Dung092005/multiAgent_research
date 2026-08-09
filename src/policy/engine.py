"""Authoritative EC_POLICY_V1 rule evaluation."""

from decimal import Decimal

from src.errors import PolicyNoMatchError
from src.finance.calculator import money
from src.policy.rules import RuleDefinition, load_rules
from src.schemas.agent_reports import PolicyDecision, RankedCause, ResponsibleParty
from src.schemas.records import PolicyContext


class PolicyEngine:
    def __init__(self) -> None:
        self._rules = {rule.primary_issue: rule for rule in load_rules()}

    def classify(self, context: PolicyContext) -> str:
        financials = context.financials
        if context.order.order_status == "canceled" and financials.payment_total_brl > 0:
            return "canceled_order_paid"
        if context.order.order_status == "unavailable" and financials.payment_total_brl > 0:
            return "unavailable_order_paid"
        if context.delivered_after_estimate and context.late_items:
            return "late_delivery_seller"
        if context.delivered_after_estimate and not context.late_items:
            return "late_delivery_logistics"
        if financials.payment_row_count >= 2 and financials.payment_matches_order_total:
            return "valid_split_payment"
        if context.delivered_within_estimate and financials.payment_matches_order_total:
            return "unsupported_late_claim"
        raise PolicyNoMatchError(f"POLICY_NO_MATCH for order {context.order.order_id}")

    def evaluate(
        self,
        context: PolicyContext,
        *,
        repair_count: int = 0,
        noncritical_missing: bool = False,
        warnings: bool = False,
    ) -> PolicyDecision:
        issue = self.classify(context)
        rule = self._rules[issue]
        parties = self._responsible_parties(rule, context)
        refund = self._refund(rule, context)
        case_status = "action_required" if refund > 0 else "no_action"
        confidence = 0.90 if repair_count or warnings else (0.95 if noncritical_missing else 0.98)
        return PolicyDecision(
            primary_issue=issue,
            case_status=case_status,
            ranked_causes=[RankedCause(cause_code=rule.root_cause, rank=1)],
            responsible_parties=parties,
            recommended_refund_brl=refund,
            resolution_actions=[rule.action],
            confidence=confidence,
        )

    @staticmethod
    def _responsible_parties(
        rule: RuleDefinition, context: PolicyContext
    ) -> list[ResponsibleParty]:
        if rule.party_type is None:
            return []
        if rule.party_id == "late_sellers":
            seller_ids = sorted({item.seller_id for item in context.late_items})
            return [
                ResponsibleParty(party_type="seller", party_id=seller_id)
                for seller_id in seller_ids[:3]
            ]
        return [ResponsibleParty(party_type=rule.party_type, party_id=str(rule.party_id))]

    @staticmethod
    def _refund(rule: RuleDefinition, context: PolicyContext) -> Decimal:
        if rule.refund_basis == "payment_total":
            return money(context.financials.payment_total_brl)
        if rule.refund_basis == "freight_total":
            return money(context.financials.freight_total_brl)
        return Decimal("0.00")
