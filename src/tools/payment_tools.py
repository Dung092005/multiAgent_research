"""Payment agent tools."""

from src.database.repository import OlistRepository
from src.schemas.records import Financials, PaymentRecord
from src.tools.base import TracedTools


class PaymentTools(TracedTools):
    allowed_tools = frozenset({"get_order_payments", "calculate_order_financials"})

    def __init__(self, repository: OlistRepository, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._repository = repository

    async def get_order_payments(self, order_id: str) -> list[PaymentRecord]:
        return await self._call(
            "get_order_payments", lambda: self._repository.get_order_payments(order_id)
        )

    async def calculate_order_financials(self, order_id: str) -> Financials:
        return await self._call(
            "calculate_order_financials",
            lambda: self._repository.calculate_order_financials(order_id),
        )
