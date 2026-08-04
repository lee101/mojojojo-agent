"""Small, deterministic model aliases for coding-agent workloads.

Aliases express intent, not prices. Gateway-native routes stay gateway-native;
provider-constrained aliases resolve to a model from that lab while preserving
the selected transport and credential boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutoModel:
    id: str
    purpose: str
    openai: str
    openpaths: str
    openrouter: str
    recommended_effort: str

    def resolve(self, provider: str, default: str) -> str:
        if provider == "openpaths":
            return self.openpaths
        if provider == "openrouter":
            return self.openrouter
        if provider == "openai":
            return self.openai
        return default


AUTO_MODELS = (
    AutoModel(
        "auto-code",
        "balanced coding default",
        "gpt-5.6-terra",
        "openpaths/auto-code",
        "openrouter/auto",
        "medium",
    ),
    AutoModel(
        "auto-fast",
        "latency-sensitive edits and lookups",
        "gpt-5.6-luna",
        "openpaths/auto-fast",
        "openrouter/auto",
        "low",
    ),
    AutoModel(
        "auto-cheap",
        "cost-sensitive routine work",
        "gpt-5.6-luna",
        "openpaths/auto-cheap",
        "openrouter/auto",
        "low",
    ),
    AutoModel(
        "auto-best",
        "hard debugging, architecture, and review",
        "gpt-5.6-sol",
        "openpaths/auto-reasoning",
        "openrouter/auto",
        "high",
    ),
    AutoModel(
        "auto-openai",
        "OpenAI-only balanced coding",
        "gpt-5.6-terra",
        "gpt-5.6-terra",
        "openai/gpt-5.6-terra",
        "medium",
    ),
    AutoModel(
        "auto-openai-fast",
        "OpenAI-only low-cost coding",
        "gpt-5.6-luna",
        "gpt-5.6-luna",
        "openai/gpt-5.6-luna",
        "low",
    ),
    AutoModel(
        "auto-openai-best",
        "OpenAI-only capability-first coding",
        "gpt-5.6-sol",
        "gpt-5.6-sol",
        "openai/gpt-5.6-sol",
        "high",
    ),
)

_BY_ID = {route.id: route for route in AUTO_MODELS}
_ALIASES = {
    "auto-medium": "auto-code",
    "auto-medium-task": "auto-code",
    "auto-easy": "auto-cheap",
    "auto-easy-task": "auto-cheap",
    "auto-hard": "auto-best",
    "auto-hard-task": "auto-best",
    "auto-reasoning": "auto-best",
}
AUTO_MODEL_IDS = tuple(route.id for route in AUTO_MODELS)


def auto_model(model: str) -> AutoModel | None:
    requested = model.strip().lower()
    return _BY_ID.get(_ALIASES.get(requested, requested))


def resolve_model(model: str, provider: str, default: str) -> str:
    requested = model.strip()
    if requested.lower() == "auto":
        return default
    route = auto_model(requested)
    return route.resolve(provider.lower(), default) if route else requested


def describe_model(model: str) -> str:
    route = auto_model(model)
    return route.purpose if route else ""
