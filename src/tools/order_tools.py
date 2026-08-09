"""Order and seller agent tools."""

from src.database.repository import OlistRepository
from src.schemas.records import ItemRecord, OrderRecord, SellerRecord
from src.tools.base import TracedTools


class OrderTools(TracedTools):
    allowed_tools = frozenset({"get_order", "get_order_items", "get_order_sellers"})

    def __init__(self, repository: OlistRepository, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._repository = repository

    async def get_order(self, order_id: str) -> OrderRecord:
        return await self._call("get_order", lambda: self._repository.get_order(order_id))

    async def get_order_items(self, order_id: str) -> list[ItemRecord]:
        return await self._call(
            "get_order_items", lambda: self._repository.get_order_items(order_id)
        )

    async def get_order_sellers(self, order_id: str) -> list[SellerRecord]:
        return await self._call(
            "get_order_sellers", lambda: self._repository.get_order_sellers(order_id)
        )
