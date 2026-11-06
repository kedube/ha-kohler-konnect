"""Tests for the kohler_konnect.dispense action."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.kohler_konnect.const import DOMAIN

from .conftest import BAR, DEVICE_ID, FakeKohler, faucet, ha_device_id


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
        ({"amount": 12, "unit": "qt"}, 11.3562),
        ({"amount": 5, "unit": "l"}, 5.0),
        ({"amount": 11, "unit": "l"}, 11.0),
        ({"amount": 11000, "unit": "ml"}, 11.0),
        # The other end of each of the app's wheels.
        ({"amount": 0.25, "unit": "gal"}, 0.9464),
        ({"amount": 1, "unit": "cup"}, 0.2366),
        ({"amount": 0.25, "unit": "qt"}, 0.2366),
        ({"amount": 0.25, "unit": "l"}, 0.25),
        ({"amount": 250, "unit": "ml"}, 0.25),
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


LAST = "sensor.kitchen_last_dispensed"


def _asked(hass: HomeAssistant) -> tuple[float, str] | None:
    attributes = hass.states.get(LAST).attributes
    if "asked_amount" not in attributes:
        return None
    return attributes["asked_amount"], attributes["asked_unit"]


@pytest.mark.parametrize(
    ("data", "asked", "state"),
    [
        ({"amount": 3, "unit": "cup"}, (3, "cup"), 709.76),
        ({"amount": 0.75, "unit": "gal"}, (0.75, "gal"), 2839.06),
        ({"amount": 300}, (300, "ml"), 300.0),  # the account's default unit
    ],
)
async def test_last_dispensed_keeps_the_unit_asked_for(
    hass: HomeAssistant, setup_entry: MockConfigEntry, data, asked, state
) -> None:
    """The sensor stays in the account's unit; the attributes say how it was asked for."""
    await _dispense(hass, **data)
    await hass.async_block_till_done()
    assert float(hass.states.get(LAST).state) == state
    assert hass.states.get(LAST).attributes["unit_of_measurement"] == "mL"
    assert _asked(hass) == asked


async def test_last_dispensed_from_a_one_tap_button(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.water_units = "Standard"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.kitchen_dispense_1_quart"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _asked(hass) == (1, "qt")


async def test_last_dispensed_from_a_preset_without_wording_has_only_its_volume(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [GLASS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _dispense(hass, amount=3, unit="cup")
    await _dispense(hass, preset="A Glass of Water")
    await hass.async_block_till_done()
    assert _asked(hass) is None


async def test_last_dispensed_from_a_preset_reads_as_it_was_saved(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [{**GLASS, "displayQuantity": "1 Cups"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _dispense(hass, preset="A Glass of Water")
    await hass.async_block_till_done()
    assert _asked(hass) == (1, "cup")


async def test_last_dispensed_from_the_preset_button_reads_as_it_was_saved(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [{**GLASS, "displayQuantity": "¼ Quarts"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.kitchen_dispense_preset"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _asked(hass) == (0.25, "qt")


async def test_last_dispensed_follows_a_dispense_started_elsewhere(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    coordinator = faucet(hass, setup_entry)
    await _dispense(hass, amount=3, unit="cup")

    # The faucet reporting this dispense, however it rounds it, is still this dispense.
    kohler.state["quantity"] = 0.71
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _asked(hass) == (3, "cup")

    # The closest different amount the app offers.
    await _dispense(hass, amount=1, unit="cup")
    kohler.state["quantity"] = 0.25
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _asked(hass) is None
    assert float(hass.states.get(LAST).state) == 250.0


@pytest.mark.parametrize(
    ("attributes", "asked"),
    [
        ({"asked_amount": 3.0, "asked_unit": "cup"}, (3.0, "cup")),
        ({"asked_amount": 3.0, "asked_unit": "firkin"}, None),
        ({"asked_amount": "3", "asked_unit": "cup"}, None),
        ({}, None),
    ],
)
async def test_last_dispensed_survives_a_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    attributes,
    asked,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(LAST, "24.0", attributes),
                {"native_value": 24.0, "native_unit_of_measurement": "fl. oz."},
            ),
        ),
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(LAST).state) == 709.76
    assert _asked(hass) == asked
