"""Source-controlled model selection (slug stays in git; secrets stay in .env)."""

from pathlib import Path

import yaml

from src.errors import ConfigurationError

OPENROUTER_MODEL_ID = "openai/gpt-5-nano"
OPENROUTER_MODEL_PARAMETER_COUNT_B: float | None = None  # proprietary; size not published
OPENROUTER_PROVIDER = "OpenRouter"
OPENROUTER_MODEL_FAMILY = "GPT-5 Nano"
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 1024


def validate_model_configuration(registry_path: Path | None = None) -> None:
    """Fail fast when source constants and the audited registry disagree."""
    path = registry_path or Path(__file__).resolve().parents[2] / "config" / "model_registry.yaml"
    try:
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read model registry: {path}") from exc

    if registry.get("model_id") != OPENROUTER_MODEL_ID:
        raise ConfigurationError("Model registry slug does not match source configuration")
