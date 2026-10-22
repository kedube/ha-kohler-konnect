"""The faucet's `MQTT connection` and `Last update` diagnostics, as the showers have."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_konnect.const import DOMAIN, FAUCET_SCAN_INTERVAL_IDLE

from .conftest import DEVICE_ID, FakeKohler, FakeMqttClient, account, wait_for

MQTT = "binary_sensor.kitchen_mqtt_connection"
LAST_UPDATE = "sensor.kitchen_last_update"


def _enable(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str) -> str:
    """Pre-register an entity that starts disabled, so it is created enabled."""
    return (
        er.async_get(hass)
        .async_get_or_create(
            domain,
            DOMAIN,
            f"{DEVICE_ID}_{key}",
            config_entry=entry,
            suggested_object_id=f"kitchen_{key}",
        )
        .entity_id
    )


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(hass, lambda: account(hass, entry).stream.connected, "the stream")


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _last_update(hass: HomeAssistant):
    return dt_util.parse_datetime(hass.states.get(LAST_UPDATE).state)


async def test_both_start_disabled_beside_the_cloud_connection(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    for entity_id in (MQTT, LAST_UPDATE):
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
    # Kohler's side of the link, named as the valves' is.
    assert hass.states.get("binary_sensor.kitchen_cloud_connection").state == STATE_ON


async def test_the_mqtt_connection_reports_the_stream_and_this_faucets_share(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    mqtt = _enable(hass, config_entry, "binary_sensor", "mqtt_connection")
    await _set_up(hass, config_entry)
    sensor = hass.states.get(mqtt)
    assert sensor.state == STATE_ON
    assert sensor.attributes["credentials_present"] is True
    assert sensor.attributes["verified"] is False
    assert sensor.attributes["faucet_messages"] == 0

    FakeMqttClient.instances[-1].deliver({"sku": "SEN", "deviceid": DEVICE_ID})
    await hass.async_block_till_done()
    sensor = hass.states.get(mqtt)
    assert sensor.attributes["verified"] is True
    assert sensor.attributes["faucet_messages"] == 1
    assert sensor.attributes["missed_changes"] == 0
    assert sensor.attributes["last_message_at"] is not None

    # Slow reconnects, so the gap lasts long enough to be seen.
    with (
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MIN_SECONDS", 30
        ),
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MAX_SECONDS", 30
        ),
    ):
        FakeMqttClient.instances[-1].drop()
        await hass.async_block_till_done()
        assert hass.states.get(mqtt).state == STATE_OFF
        assert await hass.config_entries.async_unload(config_entry.entry_id)


async def test_last_update_moves_with_news_not_with_every_poll(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    _enable(hass, config_entry, "sensor", "last_update")
    await _set_up(hass, config_entry)
    first = _last_update(hass)
    assert first is not None  # the first read is news

    # A poll that finds nothing new leaves it.
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_IDLE)
    assert _last_update(hass) == first

    # A poll that finds the water on moves it.
    kohler.state["status"] = "On"
    await _advance(hass, freezer, FAUCET_SCAN_INTERVAL_IDLE)
    polled = _last_update(hass)
    assert polled > first

    # So does a message about the faucet, whatever it says.
    freezer.tick(timedelta(seconds=5))
    FakeMqttClient.instances[-1].deliver({"sku": "SEN", "deviceid": DEVICE_ID})
    await _advance(hass, freezer, timedelta(seconds=1))
    assert _last_update(hass) > polled
