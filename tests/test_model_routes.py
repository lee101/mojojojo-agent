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
