"""Final schema validation entry point."""

from src.schemas.final_output import FinalCaseOutput


def validate_output_payload(payload: str) -> FinalCaseOutput:
    return FinalCaseOutput.model_validate_json(payload)
