"""Tests for the kohler_konnect.dispense action."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.const import DOMAIN

from .conftest import BAR, DEVICE_ID, FakeKohler, ha_device_id


async def _dispense(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(DOMAIN, "dispense", data, blocking=True)


def _quantities(kohler: FakeKohler) -> list[tuple[str, float]]:
    return [(body["deviceId"], body["quantity"]) for cmd, body in kohler.commands]


GLASS = {"experienceId": "g", "title": "A Glass of Water", "dispenseAmount": 0.236588}
CUPS = [
    {"experienceId": "a", "title": "One Cup", "dispenseAmount": 0.236588},
    {"experienceId": "b", "title": "One Cup", "dispenseAmount": 0.25},
]


async def _two_faucets(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """The kitchen faucet and a bar faucet, on the one account."""
    kohler.devices.append(dict(BAR))
    assert await hass.config_entries.async_setup(config_entry.entry_id)


@pytest.mark.parametrize(
    ("data", "liters"),
    [
        ({"amount": 300}, 0.3),  # metric default unit is mL
        ({"amount": 1.5, "unit": "l"}, 1.5),
        ({"amount": 2, "unit": "cup"}, 0.4732),
        ({"amount": 16, "unit": "fl_oz"}, 0.4732),
        ({"amount": 1, "unit": "qt"}, 0.9464),
        ({"amount": 0.5, "unit": "gal"}, 1.8927),
    ],
)
async def test_dispense_units(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler, data, liters
) -> None:
    await _dispense(hass, **data)
    assert _quantities(kohler) == [(DEVICE_ID, liters)]


async def test_us_default_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """fl oz when the Konnect account uses US units."""
    kohler.water_units = "Standard"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _dispense(hass, amount=8)  # fl oz
    assert _quantities(kohler) == [(DEVICE_ID, 0.2366)]


@pytest.mark.parametrize(
    ("data", "limits"),
    [
        ({"amount": 5}, ("10", "11360", "mL")),
        ({"amount": 3.1, "unit": "gal"}, ("0.002642", "3", "gal")),
        ({"amount": 49, "unit": "cup"}, ("0.04227", "48.01", "cups")),
        ({"amount": 11.4, "unit": "l"}, ("0.01", "11.36", "L")),
    ],
)
async def test_dispense_out_of_range(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler, data, limits
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, **data)
    assert err.value.translation_key == "amount_out_of_range"
    # Rounded inward and without exponents: every amount stated is accepted.
    placeholders = err.value.translation_placeholders
    assert (placeholders["min"], placeholders["max"], placeholders["unit"]) == limits
    assert kohler.commands == []


@pytest.mark.parametrize(
    ("data", "liters"),
    [
        ({"amount": 3, "unit": "gal"}, 11.3562),  # the Konnect app's largest
        ({"amount": 48, "unit": "cup"}, 11.3562),
        ({"amount": 5, "unit": "l"}, 5.0),
    ],
)
async def test_dispense_up_to_three_gallons(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler, data, liters
) -> None:
    await _dispense(hass, **data)
    assert _quantities(kohler) == [(DEVICE_ID, liters)]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"amount": 1, "amount_ml": 1},
        {"amount": -1},
        {"amount": 1, "unit": "pint"},
        {"amount": 1, "preset": "Tea"},
        {"preset": "  "},
    ],
)
async def test_dispense_invalid_input(
    hass: HomeAssistant, setup_entry: MockConfigEntry, data
) -> None:
    with pytest.raises(vol.Invalid):
        await _dispense(hass, **data)


async def test_dispense_goes_with_the_last_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    await hass.config_entries.async_unload(setup_entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "dispense")


async def test_dispense_targets_one_of_several_faucets(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await _two_faucets(hass, config_entry, kohler)

    # Without a target, refuse rather than run water at every faucet.
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, amount=100)
    assert err.value.translation_key == "faucet_required"

    await _dispense(hass, amount=100, device_id=ha_device_id(hass, "sen-bar"))
    assert _quantities(kohler) == [("sen-bar", 0.1)]

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, amount=100, device_id="not-a-device")
    assert err.value.translation_key == "faucet_not_selected"


@pytest.mark.parametrize(
    ("preset", "liters"),
    [
        ("A Glass of Water", 0.2366),
        ("a glass of water", 0.2366),  # case doesn't matter
        ("  A Glass of Water ", 0.2366),
        ("One Cup (2)", 0.25),  # as the Preset select lists repeats
    ],
)
async def test_dispense_preset(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    preset: str,
    liters: float,
) -> None:
    kohler.presets = [GLASS, *CUPS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    await _dispense(hass, preset=preset)
    assert _quantities(kohler) == [(DEVICE_ID, liters)]
    dispensing = hass.states.get("binary_sensor.kitchen_dispensing")
    assert dispensing.attributes["preset"] in ("A Glass of Water", "One Cup")


async def test_dispense_unknown_preset(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [GLASS, *CUPS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="Two Cups")
    assert err.value.translation_key == "preset_not_found"
    assert err.value.translation_placeholders == {
        "name": "Kitchen",
        "preset": "Two Cups",
        "presets": "A Glass of Water, One Cup, One Cup (2)",
    }
    assert kohler.commands == []


async def test_dispense_preset_without_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="One Cup")
    assert err.value.translation_placeholders["presets"] == "—"


async def test_dispense_preset_out_of_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [{"experienceId": "p", "title": "Pasta pot", "dispenseAmount": 12}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="Pasta pot")
    assert err.value.translation_key == "preset_out_of_range"
    assert kohler.commands == []


async def test_dispense_preset_checks_every_faucet_first(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    # Only the kitchen faucet has the preset.
    kohler.presets = [GLASS]
    await _two_faucets(hass, config_entry, kohler)

    devices = [ha_device_id(hass, DEVICE_ID), ha_device_id(hass, "sen-bar")]
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="A Glass of Water", device_id=devices)
    assert err.value.translation_placeholders["name"] == "Bar"
    # Not even at the faucet that has it.
    assert kohler.commands == []
