"""The product registry and the shape every new product shares (`product_base.py`).

A toy product, registered for these tests only, stands in for the products to come: the
account lists one beside a faucet, and nothing outside `products.py`,
`product_platforms.py` and `konnect/products.py` is told about it. It is built the way a
real one is: a `ProductCoordinator`, a reading, and an untested control.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_konnect import product_platforms, products
from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.kohler_konnect.konnect import (
    InvalidCredentials,
    KohlerError,
    client,
)
from custom_components.kohler_konnect.konnect import products as kinds
from custom_components.kohler_konnect.product_base import (
    ProductCoordinator,
    ProductEntity,
    UntestedControl,
)

from .conftest import FakeKohler, FakeMqttClient, account, registered_device
from .test_shower_entities import enable, setup

TOY_ID = "toy-0001"
TOY_KIND = kinds.ProductKind("toy", frozenset({"TOY"}), "toy")
POWER = "switch.playroom_power"
# Every product with messages gets the account's Report Log on its page.
LOG = "switch.playroom_report_log"


class ToyCoordinator(ProductCoordinator):
    """A toy device: its level is read whole, or set by a `TOY_STS` message."""

    model = "Toy One"

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.reads = 0
        self.loaded: dict[str, Any] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.messages = 0
        #: Raised by the next reads, when set.
        self.failure: Exception | None = None

    async def async_read(self) -> dict[str, Any]:
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return {"level": 3, "on": False}

    def apply_envelope(self, envelope: Any) -> bool:
        self.messages += 1
        self.config_entry.async_create_task(
            self.hass, self.account.async_save_device_state(self)
        )
        if envelope.code != "TOY_STS":
            return False
        self.data["level"] = envelope.attributes[0]["level"]
        return True

    def load(self, stored: Any) -> None:
        self.loaded = dict(stored)

    def stored(self) -> dict[str, Any]:
        return {"messages": self.messages}

    def async_stream_connected(self, *, just_read: bool) -> None:
        self.calls.append(("connected", just_read))
        super().async_stream_connected(just_read=just_read)

    def async_stream_disconnected(self) -> None:
        self.calls.append(("disconnected",))
        super().async_stream_disconnected()


class ToyLevel(ProductEntity, SensorEntity):
    _attr_name = "Level"

    def __init__(self, toy: ToyCoordinator) -> None:
        super().__init__(toy, "level")

    @property
    def native_value(self) -> int:
        return self.coordinator.data["level"]


class ToyPower(UntestedControl, ProductEntity, SwitchEntity):
    _attr_name = "Power"

    def __init__(self, toy: ToyCoordinator) -> None:
        super().__init__(toy, "power")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data["on"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        toy = self.coordinator
        await toy.async_command(
            toy.client.async_request(
                "POST", "/platform/api/v1/commands/toy/power", json_body={"on": True}
            ),
            offline="The toy is offline.",
        )


TOYS = products.Product(
    kind=TOY_KIND,
    create=lambda account_, device, name: ToyCoordinator(account_, device, name),
    default_name="Toy",
    diagnostics=lambda toy: {"messages": toy.messages},
    storage_key=DOMAIN + ".{entry_id}.toys",
    storage_version=1,
    fresh_for=timedelta(seconds=30),
)


@pytest.fixture(autouse=True)
def toy_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add the toy wherever the integration has the registry, as a new product would be."""
    registry, registered_kinds = products.PRODUCTS, kinds.PRODUCT_KINDS
    for name, module in list(sys.modules.items()):
        if not name.startswith("custom_components.kohler_konnect"):
            continue
        if getattr(module, "PRODUCTS", None) is registry:
            monkeypatch.setattr(module, "PRODUCTS", (*registry, TOYS))
        if getattr(module, "PRODUCT_KINDS", None) is registered_kinds:
            monkeypatch.setattr(module, "PRODUCT_KINDS", (*registered_kinds, TOY_KIND))
    monkeypatch.setitem(
        product_platforms._BUILDERS,
        "toy",
        {
            Platform.SENSOR: lambda hass, entry, toy, add: [ToyLevel(toy)],
            Platform.SWITCH: lambda hass, entry, toy, add: [ToyPower(toy)],
        },
    )


@pytest.fixture
def kohler(kohler: FakeKohler) -> FakeKohler:
    kohler.devices.append({"deviceId": TOY_ID, "sku": "TOY", "logicalName": "Playroom"})
    return kohler


def toy(hass: HomeAssistant, entry: MockConfigEntry) -> ToyCoordinator:
    return account(hass, entry).products["toy"][0]


async def test_a_recorded_product_is_built_and_read_with_the_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await setup(hass, config_entry)
    device = toy(hass, config_entry)

    assert (device.device_id, device.device_name, device.reads) == (
        TOY_ID,
        "Playroom",
        1,
    )
    assert hass.states.get("sensor.playroom_level").state == "3"
    assert registered_device(hass, TOY_ID).model == "Toy One"
    # The stream's first connect came while the first read was fresh.
    assert device.calls == [("connected", True)]
    # Its control is on, flagged as nobody having seen it work.
    assert hass.states.get(POWER).attributes["untested"] is True
    # The faucet beside it is untouched.
    assert account(hass, config_entry).faucets[0].device_name == "Kitchen"
    listing = client.Customer({"customerHome": [{"devices": kohler.devices}]})
    assert "1 toy(s)" in listing.describe()
    assert listing.other_devices == []


async def test_its_messages_reach_it_and_what_it_keeps_is_saved(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    hass_storage: dict[str, Any],
) -> None:
    await setup(hass, config_entry)
    device = toy(hass, config_entry)
    stream = FakeMqttClient.instances[-1]

    # Routed case-insensitively, as faucet messages are, and applied without a read.
    stream.deliver(
        {
            "deviceid": TOY_ID.upper(),
            "sku": "TOY",
            "data": {"code": "TOY_STS", "attributes": [{"level": 7}]},
        }
    )
    await hass.async_block_till_done()
    assert (hass.states.get("sensor.playroom_level").state, device.reads) == ("7", 1)
    saved = hass_storage[f"{DOMAIN}.{config_entry.entry_id}.toys"]["data"]
    assert saved == {TOY_ID: {"messages": 1}}

    # A message it can't apply reads the device again.
    stream.deliver({"deviceid": TOY_ID, "sku": "TOY", "data": {"code": "TOY_OTHER"}})
    await hass.async_block_till_done()
    assert (hass.states.get("sensor.playroom_level").state, device.reads) == ("3", 2)

    stream.drop()
    await hass.async_block_till_done()
    # It may already have reconnected, which it is told too.
    assert ("disconnected",) in device.calls

    # Taken back after a reload.
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert toy(hass, config_entry).loaded == {"messages": 2}


async def test_a_control_waits_for_the_device_to_say_what_it_did(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    enable(hass, config_entry, "switch", f"{TOY_ID}_power", "playroom_power")
    await setup(hass, config_entry)
    device = toy(hass, config_entry)
    assert hass.states.get(POWER).attributes["untested"] is True

    async def later() -> None:
        freezer.tick(timedelta(seconds=9))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": POWER}, blocking=True
    )
    await hass.async_block_till_done()
    assert ("power", {"on": True}) in kohler.commands
    # Kohler's copy can lag a command, so nothing is read straight after one.
    assert device.reads == 1
    # Nothing heard from the device: read it.
    await later()
    assert device.reads == 2

    # The device says what it did, which stands for the read.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": POWER}, blocking=True
    )
    FakeMqttClient.instances[-1].deliver(
        {
            "deviceid": TOY_ID,
            "sku": "TOY",
            "data": {"code": "TOY_STS", "attributes": [{"level": 5}]},
        }
    )
    await hass.async_block_till_done()
    await later()
    assert (hass.states.get("sensor.playroom_level").state, device.reads) == ("5", 2)


async def test_a_message_that_comes_during_a_read_outlasts_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await setup(hass, config_entry)
    device = toy(hass, config_entry)
    gate = asyncio.Event()
    read = device.async_read

    async def slow_read() -> dict[str, Any]:
        await gate.wait()
        return await read()

    device.async_read = slow_read  # type: ignore[method-assign]
    refresh = hass.async_create_task(device.async_refresh())
    await asyncio.sleep(0)
    # Newer than whatever the read out will bring back.
    FakeMqttClient.instances[-1].deliver(
        {
            "deviceid": TOY_ID,
            "sku": "TOY",
            "data": {"code": "TOY_STS", "attributes": [{"level": 7}]},
        }
    )
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    await refresh
    await hass.async_block_till_done()
    assert hass.states.get("sensor.playroom_level").state == "7"


async def test_a_failed_read_shows_unavailable_and_a_rejected_sign_in_asks_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await setup(hass, config_entry)
    device = toy(hass, config_entry)

    device.failure = KohlerError("Kohler is down")
    await device.async_refresh()
    assert hass.states.get("sensor.playroom_level").state == "unavailable"

    device.failure = InvalidCredentials("refresh token rejected")
    await device.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_diagnostics_report_it_beside_the_faucet(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await setup(hass, config_entry)

    report = await async_get_config_entry_diagnostics(hass, config_entry)
    assert (report["devices"]["toy_count"], report["devices"]["faucet_count"]) == (1, 1)
    assert report["toys"] == [{"messages": 0}]
    assert "toy" not in report

    device = registered_device(hass, TOY_ID)
    report = await async_get_device_diagnostics(hass, config_entry, device)
    assert (report["requested_for"], report["toy"]) == ("toy_0", {"messages": 0})
    assert "faucet" not in report


async def test_the_panel_knows_an_untested_control_an_owner_turned_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    hass_ws_client: Any,
) -> None:
    er.async_get(hass).async_get_or_create(
        "switch",
        DOMAIN,
        f"{TOY_ID}_power",
        config_entry=config_entry,
        suggested_object_id="playroom_power",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert await async_setup_component(hass, "http", {})
    await setup(hass, config_entry)
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe_devices"})
    async with asyncio.timeout(5):
        assert (await ws.receive_json())["success"]
        event = (await ws.receive_json())["event"]
    descriptor = next(d for d in event["devices"] if d["kind"] == "toy")
    assert descriptor["roles"] == {"level": "sensor.playroom_level", "report_log": LOG}
    assert descriptor["disabled_roles"] == {"power": POWER}
    # A main control, so the device itself is untested, though turned off.
    assert (descriptor["untested"], descriptor["untested_device"]) == (["power"], True)


async def test_the_panel_shows_it_in_the_generic_view(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    hass_ws_client: Any,
) -> None:
    enable(hass, config_entry, "switch", f"{TOY_ID}_power", "playroom_power")
    assert await async_setup_component(hass, "http", {})
    await setup(hass, config_entry)
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": f"{DOMAIN}/subscribe_devices"})
    async with asyncio.timeout(5):
        assert (await ws.receive_json())["success"]
        event = (await ws.receive_json())["event"]
    descriptor = next(d for d in event["devices"] if d["kind"] == "toy")
    # With no role table of its own, each entity's role is its unique-id suffix.
    assert descriptor["roles"] == {
        "level": "sensor.playroom_level",
        "power": POWER,
        "report_log": LOG,
    }
    assert descriptor["untested"] == ["power"]
    assert (descriptor["name"], descriptor["model"]) == ("Playroom", "Toy One")

    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/subscribe_device", "device_id": descriptor["id"]}
    )
    async with asyncio.timeout(5):
        assert (await ws.receive_json())["success"]
        live = (await ws.receive_json())["event"]
    assert (live["device_id"], live["last_update"]) == (descriptor["id"], None)

    await ws.send_json_auto_id(
        {"type": f"{DOMAIN}/device_details", "device_id": descriptor["id"]}
    )
    async with asyncio.timeout(5):
        details = (await ws.receive_json())["result"]
    assert (details["model"], details["firmware"], details["faults"]) == (
        "Toy One",
        [],
        [],
    )
