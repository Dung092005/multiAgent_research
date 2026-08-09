"""Delivery agent tools."""

from src.database.repository import OlistRepository
from src.schemas.records import DeliveryTimeline, ItemRecord, ReviewRecord
from src.tools.base import TracedTools


class DeliveryTools(TracedTools):
    allowed_tools = frozenset(
        {"get_order_delivery_timeline", "get_order_items", "get_order_reviews"}
    )

    def __init__(self, repository: OlistRepository, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._repository = repository

    async def get_order_delivery_timeline(self, order_id: str) -> DeliveryTimeline:
        return await self._call(
            "get_order_delivery_timeline",
            lambda: self._repository.get_order_delivery_timeline(order_id),
        )

    async def get_order_items(self, order_id: str) -> list[ItemRecord]:
        return await self._call(
            "get_order_items", lambda: self._repository.get_order_items(order_id)
        )

    async def get_order_reviews(self, order_id: str) -> list[ReviewRecord]:
        return await self._call(
            "get_order_reviews", lambda: self._repository.get_order_reviews(order_id)
        )
