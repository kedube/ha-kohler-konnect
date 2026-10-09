"""Diagnostics: the faucet's part of the report, with identity taken out."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import DEVICE_ID, MOBILE_ID, TENANT_ID, USERNAME, FakeKohler


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    dumped = json.dumps(diagnostics)

    for secret in (USERNAME, DEVICE_ID, TENANT_ID, MOBILE_ID, "SN-SECRET-1"):
        assert secret not in dumped
    assert "refresh-" not in dumped
    assert diagnostics["devices"]["faucet_count"] == 1
    (faucet,) = diagnostics["faucets"]
    assert faucet["sku"] == "SEN"
    assert faucet["state"]["status"] == "Off"
    about = faucet["configuration"]["configuration"]["about"]
    assert about["firmware"]["version"] == "16.0"


async def test_identifiers_redacted_under_any_name(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    # Fields the key-based list does not know, as Kohler might add them.
    kohler.config["owner"] = {"tenant": TENANT_ID}
    kohler.config["leakDetectionHistory"] = [
        {"id": "leak-1", "source": f"devices/{DEVICE_ID}/leaks"}
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )
    dumped = json.dumps(diagnostics).lower()

    for secret in (DEVICE_ID, TENANT_ID):
        assert secret.lower() not in dumped
    leak = diagnostics["faucets"][0]["configuration"]["leakDetectionHistory"][0]
    assert leak == {"id": "leak-1", "source": "devices/**REDACTED**/leaks"}


async def test_preset_names_stay_out(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """The owner's own words, like the showers' favorite names; the count is enough."""
    kohler.presets = [
        {"experienceId": "p1", "title": "Pasta Pot", "dispenseAmount": 4.0}
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )
    assert "Pasta Pot" not in json.dumps(diagnostics)
    assert diagnostics["faucets"][0]["presets"]["count"] == 1
