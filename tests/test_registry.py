"""The device-registry calls that differ between Home Assistant 2026.3 and 2026.10.

CI runs these tests against both versions, but each run installs only one. So every path is
driven here whichever version is installed: the registry is a stand-in offering the old
lookup or the new one, and the version check behind `parent_link` is patched both ways.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.kohler_konnect import registry
from custom_components.kohler_konnect.registry import (
    device_by_identifier,
    parent_link,
)

IDENTIFIER = ("kohler_konnect", "gcs-test0001")
ENTRY_ID = "entry-1"
DEVICE = SimpleNamespace(id="device-1")


def test_the_lookup_uses_the_entry_scoped_call_where_home_assistant_has_it():
    calls = []

    def by_identifier(identifier, config_entry_id):
        calls.append((identifier, config_entry_id))
        return DEVICE

    def deprecated(**kwargs):
        raise AssertionError("async_get_device is deprecated from 2026.10")

    found = device_by_identifier(
        SimpleNamespace(
            async_get_device_by_identifier=by_identifier,
            async_get_device=deprecated,
        ),
        IDENTIFIER,
        ENTRY_ID,
    )
    assert found is DEVICE
    assert calls == [(IDENTIFIER, ENTRY_ID)]


def test_the_lookup_falls_back_to_async_get_device_before_2026_10():
    calls = []

    def get_device(*, identifiers):
        calls.append(identifiers)
        return DEVICE

    found = device_by_identifier(
        SimpleNamespace(async_get_device=get_device), IDENTIFIER, ENTRY_ID
    )
    assert found is DEVICE
    assert calls == [{IDENTIFIER}]


def test_a_parent_is_named_by_registry_id_where_home_assistant_takes_one(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(registry, "VIA_DEVICE_ID", True)
    assert parent_link(IDENTIFIER, "device-1") == {"via_device_id": "device-1"}


def test_an_unknown_registry_id_leaves_the_link_out_rather_than_deprecated(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(registry, "VIA_DEVICE_ID", True)
    assert parent_link(IDENTIFIER, None) == {}


def test_a_parent_is_named_by_identifier_before_2026_10(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(registry, "VIA_DEVICE_ID", False)
    assert parent_link(IDENTIFIER, "device-1") == {"via_device": IDENTIFIER}
    assert parent_link(IDENTIFIER, None) == {"via_device": IDENTIFIER}
