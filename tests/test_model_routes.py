from __future__ import annotations

from mjj.auth import Credential
from mjj.model import ModelClient
from mjj.model_routes import AUTO_MODEL_IDS, auto_model, describe_model, resolve_model


def credential(provider: str, default: str = "provider-default") -> Credential:
    return Credential(
        "api_key",
        "test",
        "https://example.test/v1",
        provider=provider,
        default_model=default,
    )


def test_auto_routes_are_provider_aware() -> None:
    assert resolve_model("auto-code", "openpaths", "fallback") == "openpaths/auto-code"
    assert resolve_model("auto-code", "openai", "fallback") == "gpt-5.6-terra"
    assert resolve_model("auto-fast", "openai", "fallback") == "gpt-5.6-luna"
    assert resolve_model("auto-best", "openai", "fallback") == "gpt-5.6-sol"
    assert resolve_model("auto-code", "custom", "fallback") == "fallback"
    assert resolve_model(" AUTO ", "openai", "fallback") == "fallback"


def test_openai_constrained_alias_stays_on_openai_models_through_gateways() -> None:
    assert resolve_model("auto-openai", "openpaths", "fallback") == "gpt-5.6-terra"
    assert (
        resolve_model("auto-openai-fast", "openrouter", "fallback")
        == "openai/gpt-5.6-luna"
    )
    assert resolve_model("auto-openai-best", "openai", "fallback") == "gpt-5.6-sol"


def test_legacy_openpaths_intent_aliases_remain_concise() -> None:
    assert resolve_model("auto-easy-task", "openpaths", "fallback") == "openpaths/auto-cheap"
    assert resolve_model("auto-hard-task", "openpaths", "fallback") == "openpaths/auto-reasoning"


def test_model_client_applies_route_model_without_overriding_explicit_effort() -> None:
    client = ModelClient(model="auto-fast", effort="max")

    assert client.effective_model(credential("openai")) == "gpt-5.6-luna"
    assert client.effective_effort() == "max"
    assert auto_model("auto-fast").recommended_effort == "low"
    assert "latency-sensitive" in describe_model("auto-fast")
    assert "auto-openai" in AUTO_MODEL_IDS


def body(client: ModelClient, provider: str) -> dict:
    return client.chat_request_body([], "sys", [], credential(provider))


def test_floor_routing_asks_openpaths_for_the_cheapest_provider() -> None:
    client = ModelClient(model="auto-code", routing="floor")

    sent = body(client, "openpaths")

    assert sent["routing_strategy"] == "price"
    # floor keeps the coding tier; only the provider under it may change.
    assert "task_tier" not in sent
    assert sent["model"] == "openpaths/auto-code"


def test_cheap_routing_drops_to_the_cheap_task_tier() -> None:
    client = ModelClient(model="auto", routing="cheap")

    sent = body(client, "openpaths")

    assert sent["routing_strategy"] == "price"
    assert sent["task_tier"] == "easy"
    assert client.effective_model(credential("openpaths")) == "openpaths/auto-cheap"


def test_cheap_routing_never_reroutes_a_pinned_model() -> None:
    client = ModelClient(model="deepseek-v4-flash", routing="cheap")

    sent = body(client, "openpaths")

    assert sent["model"] == "deepseek-v4-flash"
    assert "task_tier" not in sent
    assert sent["routing_strategy"] == "price"


def test_routing_modes_map_to_gateway_dialects() -> None:
    assert body(ModelClient(routing="config"), "openpaths")["routing_strategy"] == "config"
    assert body(ModelClient(routing="fast"), "openpaths")["routing_strategy"] == "fastest"
    assert "routing_strategy" not in body(ModelClient(routing="auto"), "openpaths")
    assert body(ModelClient(routing="floor"), "openrouter")["provider"] == {"sort": "price"}
    assert body(ModelClient(routing="fast"), "openrouter")["provider"] == {
        "sort": "throughput"
    }
    assert "provider" not in body(ModelClient(routing="config"), "openrouter")


def test_routing_is_not_sent_to_direct_providers() -> None:
    sent = body(ModelClient(routing="floor"), "deepseek")

    assert "routing_strategy" not in sent
    assert "task_tier" not in sent
    assert "provider" not in sent


def test_unknown_routing_mode_falls_back_to_the_floor() -> None:
    assert ModelClient(routing="bogus").routing == "floor"
