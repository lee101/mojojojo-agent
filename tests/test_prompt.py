from mjj.prompt import for_model, model_family, split_cache_layers


def test_model_family_recognizes_provider_qualified_models() -> None:
    assert model_family("gpt-5.3-codex") == "codex"
    assert model_family("openai/gpt-5.3-codex") == "codex"
    assert model_family("gpt-5.6-sol") == "codex"
    assert model_family("grok-4.5") == "grok"
    assert model_family("x-ai/grok-4.5") == "grok"
    assert model_family("deepseek-v4-flash") == "deepseek"
    assert model_family("openpaths/deepseek-v4-pro") == "deepseek"
    assert model_family("openpaths/auto-code") == "neutral"


def test_model_hint_trails_project_rules_for_shared_prefixes() -> None:
    instructions = "base\n\n--- project-doc ---\n\nrepo wins"

    profiled = for_model(instructions, "x-ai/grok-4.5")

    assert "favor exact tool calls" in profiled
    assert profiled.startswith(instructions)
    assert profiled.count("--- model-hint ---") == 1
    assert profiled.endswith("until verified.")
    assert for_model(profiled, "x-ai/grok-4.5") == profiled
    stable, volatile = split_cache_layers(profiled)
    assert stable == instructions
    assert "favor exact tool calls" in volatile
    assert len(volatile.split()) <= 20


def test_deepseek_hint_keeps_agent_moving() -> None:
    profiled = for_model("base", "deepseek-v4-flash")
    assert profiled.startswith("base")
    assert "keep calling tools until the task is done" in profiled
    stable, volatile = split_cache_layers(profiled)
    assert stable == "base"
    assert "keep calling tools" in volatile


def test_neutral_models_pay_no_prompt_tax() -> None:
    assert for_model("base", "qwen3-coder") == "base"
