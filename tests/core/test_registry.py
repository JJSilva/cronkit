"""The tool catalogue."""

import pytest

from cronkit.core import registry
from cronkit.core.errors import ToolNotFoundError


@pytest.fixture
def clean_registry(monkeypatch):
    """Isolate the registry so tests cannot see (or corrupt) the real one."""
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.setattr(registry, "_loaded", True)
    return registry


def test_registering_makes_a_tool_discoverable(clean_registry, fake_tool_cls):
    clean_registry.register(fake_tool_cls)

    assert clean_registry.tool_names() == ["fake"]
    assert clean_registry.get("fake") is fake_tool_cls


def test_registering_returns_the_class_so_it_works_as_a_decorator(clean_registry, fake_tool_cls):
    assert clean_registry.register(fake_tool_cls) is fake_tool_cls


def test_registering_the_same_class_twice_is_harmless(clean_registry, fake_tool_cls):
    clean_registry.register(fake_tool_cls)
    clean_registry.register(fake_tool_cls)

    assert clean_registry.tool_names() == ["fake"]


def test_two_tools_claiming_one_name_fail_loudly(clean_registry, fake_tool_cls):
    """Silently shadowing a tool would mean the wrong job runs on a schedule."""

    class Impostor(fake_tool_cls):
        pass

    clean_registry.register(fake_tool_cls)
    with pytest.raises(ValueError, match="fake"):
        clean_registry.register(Impostor)


def test_a_nameless_tool_is_rejected(clean_registry, fake_tool_cls):
    class Nameless(fake_tool_cls):
        name = ""

    with pytest.raises(ValueError, match="non-empty"):
        clean_registry.register(Nameless)


def test_an_unknown_name_lists_what_is_available(clean_registry, fake_tool_cls):
    clean_registry.register(fake_tool_cls)
    with pytest.raises(ToolNotFoundError, match="fake"):
        clean_registry.get("nope")


def test_select_preserves_the_callers_order(clean_registry, fake_tool_cls):
    class Other(fake_tool_cls):
        name = "aaa-other"

    clean_registry.register(fake_tool_cls)
    clean_registry.register(Other)

    assert clean_registry.select(["fake", "aaa-other"]) == [fake_tool_cls, Other]


# --- the real registry -----------------------------------------------------


def test_the_calendar_tool_is_registered_out_of_the_box():
    """Importing cronkit.tools must be enough; no entry point has to remember."""
    assert "trainingpeaks-calendar" in registry.tool_names()
