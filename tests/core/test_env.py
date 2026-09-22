"""Environment lookups, with emphasis on the alias chain.

The alias chain is what lets a tool rename its variables without breaking a
deployment that still has the old ones set, so it is worth pinning down.
"""

import pytest

from cronkit.core import env
from cronkit.core.errors import ConfigError

NAMES = ["NEW_NAME", "OLD_NAME", "OLDER_NAME"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_first_name_set_wins(clean):
    clean.setenv("NEW_NAME", "new")
    clean.setenv("OLD_NAME", "old")
    assert env.optional(*NAMES) == "new"


def test_a_later_alias_is_used_when_the_first_is_unset(clean):
    clean.setenv("OLDER_NAME", "oldest")
    assert env.optional(*NAMES) == "oldest"


def test_a_blank_value_counts_as_unset(clean):
    """Railway keeps emptied variables around as empty strings."""
    clean.setenv("NEW_NAME", "   ")
    clean.setenv("OLD_NAME", "old")
    assert env.optional(*NAMES) == "old"


def test_values_are_trimmed(clean):
    clean.setenv("NEW_NAME", "  value  ")
    assert env.optional("NEW_NAME") == "value"


def test_the_default_applies_when_nothing_is_set():
    assert env.optional(*NAMES, default="fallback") == "fallback"


def test_require_names_every_accepted_variable(clean):
    """The error has to be actionable: it is the only clue an operator gets."""
    with pytest.raises(ConfigError) as excinfo:
        env.require(*NAMES)
    message = str(excinfo.value)
    for name in NAMES:
        assert name in message


@pytest.mark.parametrize("raw,expected", [("true", True), ("YES", True), ("1", True), ("false", False), ("off", False)])
def test_flags_accept_the_usual_spellings(clean, raw, expected):
    clean.setenv("NEW_NAME", raw)
    assert env.flag("NEW_NAME", default=not expected) is expected


def test_an_unparseable_flag_is_rejected_rather_than_assumed(clean):
    clean.setenv("NEW_NAME", "sometimes")
    with pytest.raises(ConfigError, match="true or false"):
        env.flag("NEW_NAME", default=True)


def test_numbers_and_integers_report_the_offending_name(clean):
    clean.setenv("OLD_NAME", "lots")
    with pytest.raises(ConfigError, match="OLD_NAME"):
        env.number("NEW_NAME", "OLD_NAME", default=1.0)
    with pytest.raises(ConfigError, match="OLD_NAME"):
        env.integer("NEW_NAME", "OLD_NAME", default=1)


def test_csv_lists_are_split_and_trimmed(clean):
    clean.setenv("NEW_NAME", " a, b ,, c ")
    assert env.csv_list("NEW_NAME") == ["a", "b", "c"]


def test_an_unset_csv_list_is_empty():
    assert env.csv_list(*NAMES) == []
