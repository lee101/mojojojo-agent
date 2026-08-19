"""Small, deterministic model aliases for coding-agent workloads.

Aliases express intent, not prices. Gateway-native routes stay gateway-native;
provider-constrained aliases resolve to a model from that lab while preserving
the selected transport and credential boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


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


# Gateway cost/latency preference. "floor" keeps the chosen capability tier and
# asks the gateway for the cheapest healthy provider serving it; "cheap" also
# drops the capability tier to the gateway's cheap-task table.
ROUTING_MODES = ("auto", "floor", "cheap", "config", "fast")
DEFAULT_ROUTING = "floor"

_ROUTING_STRATEGY = {
    "floor": "price",
    "cheap": "price",
    "config": "config",
    "fast": "fastest",
}
# OpenRouter spells the same preference as provider.sort.
_OPENROUTER_SORT = {"floor": "price", "cheap": "price", "fast": "throughput"}
_TASK_TIER = {"cheap": "easy", "fast": "fast"}
# Aliases that are a default rather than a deliberate capability pick, so the
# cost floor may rewrite them.
_CHEAPABLE = ("auto", "auto-code", "auto-fast")


def normalize_routing(routing: str) -> str:
    mode = (routing or "").strip().lower()
    return mode if mode in ROUTING_MODES else DEFAULT_ROUTING


def routing_strategy(routing: str) -> str:
    """Gateway ``routing_strategy`` value, or "" to accept the default."""
    return _ROUTING_STRATEGY.get(normalize_routing(routing), "")


def openrouter_sort(routing: str) -> str:
    return _OPENROUTER_SORT.get(normalize_routing(routing), "")


def task_tier(routing: str, model: str) -> str:
    """Cross-provider tier hint.

    Only sent for auto aliases: the gateway lets ``task_tier`` override an
    explicit model id, and a pinned model must stay pinned.
    """
    if not is_auto_route(model):
        return ""
    return _TASK_TIER.get(normalize_routing(routing), "")


def is_auto_route(model: str) -> bool:
    requested = model.strip().lower()
    if auto_model(requested) is not None:
        return True
    leaf = requested.rsplit("/", 1)[-1]
    return leaf == "auto" or leaf.startswith("auto-")


def routed_model(model: str, routing: str) -> str:
    """Bias a default auto alias toward the cost floor."""
    if normalize_routing(routing) != "cheap":
        return model
    requested = model.strip().lower()
    route = auto_model(requested)
    resolved = route.id if route else requested
    return "auto-cheap" if resolved in _CHEAPABLE else model


def closest_effort(requested: str, supported: tuple[str, ...]) -> str:
    """Keep the caller's intent while selecting a value the model accepts."""
    requested = requested.strip().lower()
    allowed = tuple(value for value in supported if value in EFFORT_ORDER)
    if not allowed or requested in allowed:
        return requested
    try:
        target = EFFORT_ORDER.index(requested)
    except ValueError:
        return requested
    # Prefer more reasoning when two supported values are equally close.
    return min(
        allowed,
        key=lambda value: (
            abs(EFFORT_ORDER.index(value) - target),
            -EFFORT_ORDER.index(value),
        ),
    )


def supported_efforts(model: str) -> tuple[str, ...] | None:
    leaf = model.strip().lower().rsplit("/", 1)[-1]
    if leaf == "gpt-5.6" or leaf.startswith("gpt-5.6-"):
        return ("none", "low", "medium", "high", "xhigh", "max")
    return None
