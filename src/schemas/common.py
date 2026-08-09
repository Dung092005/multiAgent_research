"""Shared strict Pydantic base model."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
