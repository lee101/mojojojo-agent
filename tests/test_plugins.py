from __future__ import annotations

from dataclasses import dataclass

from mjj import plugins
from mjj.ledger import Budget, Ledger
from mjj.tools import build_registry
from mjj.tools.base import ToolContext, ToolResult


class EchoTool:
    name = "echo"
    description = "Echo text from a test plugin."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_approval = True

    def run(self, args, _ctx):
        return ToolResult(args.get("text", ""))


@dataclass
class FakePoint:
    name: str
    exported: object
    value: str = "example.plugin:build"
    loads: int = 0

    def load(self):
        self.loads += 1
        if isinstance(self.exported, Exception):
            raise self.exported
        return self.exported


def test_plugins_are_opt_in_namespaced_approval_gated_and_ledger_bounded(
    tmp_path, monkeypatch
):
    point = FakePoint("example", lambda: [EchoTool()])
    monkeypatch.setattr(plugins, "_entry_points", lambda: [point])

    unused = build_registry(only=["plugins"])
    assert unused.tools == {}
    assert point.loads == 0

    registry = build_registry(only=["plugins"], plugins=("example",))
    assert set(registry.tools) == {"example__echo"}
    assert point.loads == 1
    assert registry.schemas()[0]["name"] == "example__echo"

    denied = registry.dispatch(
        "example__echo",
        '{"text":"no"}',
        ToolContext(tmp_path, Ledger(), approve=lambda _name, _args: False),
    )
    assert not denied.ok and denied.meta["denied"] is True

    ledger = Ledger(Budget(default=12))
    result = registry.dispatch(
        "example__echo",
        '{"text":"' + ("x" * 2_000) + '"}',
        ToolContext(tmp_path, ledger, approve=lambda _name, _args: True),
    )
    assert result.ok
    assert len(result.output) <= 12 * 4
    assert result.meta["plugin"] == "example"
    assert ledger.drops
    assert list((tmp_path / ".mjj" / "tool-results").glob("*example__echo*"))


def test_plugin_objects_close_and_invalid_tools_degrade_to_warnings(monkeypatch):
    class BadSchema:
        name = "unsafe"
        description = "bad"
        parameters = {"type": "array"}

        def run(self, _args, _ctx):
            return ToolResult("bad")

    class Bundle:
        def __init__(self):
            self.tools = [EchoTool(), BadSchema()]
            self.closed = False

        def close(self):
            self.closed = True

    bundle = Bundle()
    monkeypatch.setattr(
        plugins,
        "_entry_points",
        lambda: [FakePoint("bundle", lambda: bundle)],
    )

    registry = build_registry(only=["plugins"], plugins=("bundle", "missing"))
    assert set(registry.tools) == {"bundle__echo"}
    assert any("object schema" in warning for warning in registry.warnings)
    assert any("not found" in warning for warning in registry.warnings)
    registry.close()
    assert bundle.closed is True


def test_plugin_with_no_valid_tools_is_still_closed(monkeypatch):
    class Bundle:
        tools = [object()]
        closed = False

        def close(self):
            self.closed = True

    bundle = Bundle()
    monkeypatch.setattr(
        plugins,
        "_entry_points",
        lambda: [FakePoint("bundle", lambda: bundle)],
    )

    registry = build_registry(only=["plugins"], plugins=("bundle",))
    assert registry.tools == {}
    registry.close()
    assert bundle.closed is True


def test_plugin_iterables_and_total_schema_cost_are_bounded(monkeypatch):
    yielded = 0

    def endless_tools():
        nonlocal yielded
        while True:
            yielded += 1
            yield EchoTool()

    monkeypatch.setattr(
        plugins,
        "_entry_points",
        lambda: [FakePoint("many", endless_tools())],
    )
    registry = build_registry(only=["plugins"], plugins=("many",))
    assert registry.tools == {}
    assert yielded == plugins.MAX_TOOLS_PER_PLUGIN + 1
    assert any("more than" in warning for warning in registry.warnings)

    monkeypatch.setattr(plugins, "MAX_TOTAL_SCHEMA_BYTES", 1)
    monkeypatch.setattr(
        plugins,
        "_entry_points",
        lambda: [FakePoint("small", lambda: [EchoTool()])],
    )
    registry = build_registry(only=["plugins"], plugins=("small",))
    assert registry.tools == {}
    assert any("schema budget" in warning for warning in registry.warnings)


def test_plugin_qualified_name_collisions_do_not_replace_tools(monkeypatch):
    first = EchoTool()
    first.name = "beta__echo"
    second = EchoTool()
    monkeypatch.setattr(
        plugins,
        "_entry_points",
        lambda: [
            FakePoint("alpha", lambda: [first]),
            FakePoint("alpha__beta", lambda: [second]),
        ],
    )

    registry = build_registry(
        only=["plugins"], plugins=("alpha", "alpha__beta")
    )

    assert set(registry.tools) == {"alpha__beta__echo"}
    assert registry.tools["alpha__beta__echo"].inner is first
    assert any("collision" in warning for warning in registry.warnings)


def test_broken_plugin_load_is_a_short_registry_warning(monkeypatch):
    point = FakePoint("broken", RuntimeError(("failure\n" * 1_000)))
    monkeypatch.setattr(plugins, "_entry_points", lambda: [point])

    registry = build_registry(only=["plugins"], plugins=("broken",))

    assert registry.tools == {}
    assert len(registry.warnings) == 1
    assert len(registry.warnings[0]) <= 320
    assert "load failed" in registry.warnings[0]


def test_plugin_inventory_does_not_import_entry_point(monkeypatch):
    point = FakePoint("example", AssertionError("must not import"))
    monkeypatch.setattr(plugins, "_entry_points", lambda: [point])

    inventory = plugins.plugin_inventory()

    assert inventory[0].public(enabled=True) == {
        "name": "example",
        "value": "example.plugin:build",
        "distribution": None,
        "enabled": True,
    }
    assert point.loads == 0
