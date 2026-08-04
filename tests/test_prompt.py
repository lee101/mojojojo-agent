from mjj.prompt import for_model, model_family


def test_model_family_recognizes_provider_qualified_models() -> None:
    assert model_family("gpt-5.3-codex") == "codex"
    assert model_family("openai/gpt-5.3-codex") == "codex"
    assert model_family("gpt-5.6-sol") == "codex"
    assert model_family("grok-4.5") == "grok"
    assert model_family("x-ai/grok-4.5") == "grok"
    assert model_family("openpaths/auto-code") == "neutral"


def test_model_hint_stays_ahead_of_project_rules_and_is_idempotent() -> None:
    instructions = "base\n\n--- project-doc ---\n\nrepo wins"

    profiled = for_model(instructions, "x-ai/grok-4.5")

    assert "favor exact tool calls" in profiled
    assert len(profiled.split()) - len(instructions.split()) <= 15
    assert profiled.endswith("--- project-doc ---\n\nrepo wins")
    assert for_model(profiled, "x-ai/grok-4.5") == profiled


def test_neutral_models_pay_no_prompt_tax() -> None:
    assert for_model("base", "qwen3-coder") == "base"
