"""Verifier-only entity reference tool."""

from src.database.repository import OlistRepository
from src.schemas.records import EntityReferenceCheck
from src.tools.base import TracedTools


class EvidenceTools(TracedTools):
    allowed_tools = frozenset({"validate_entity_references", "get_policy_context"})

    def __init__(self, repository: OlistRepository, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._repository = repository

    async def validate_entity_references(
        self, order_id: str, item_ids: list[str], seller_ids: list[str], payment_ids: list[str]
    ) -> EntityReferenceCheck:
        return await self._call(
            "validate_entity_references",
            lambda: self._repository.validate_entity_references(
                order_id, item_ids, seller_ids, payment_ids
            ),
        )

    async def get_policy_context(self, order_id: str):
        return await self._call(
            "get_policy_context", lambda: self._repository.get_policy_context(order_id)
        )
