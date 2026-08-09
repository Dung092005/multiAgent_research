"""Input case schema."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from src.schemas.common import StrictModel


class CustomerRequest(StrictModel):
    language: str = Field(min_length=2, max_length=10)
    message: str = Field(min_length=1, max_length=4000)
    claimed_order_id: str = Field(min_length=1, max_length=64)


class CaseInput(StrictModel):
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    opened_at: datetime
    customer_request: CustomerRequest
    policy_version: Literal["EC_POLICY_V1"]

    @field_validator("opened_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("opened_at must include a timezone offset")
        return value
