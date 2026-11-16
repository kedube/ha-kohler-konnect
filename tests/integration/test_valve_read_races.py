"""A read that a push overtakes never undoes the push.

Kohler's documents trail the devices' own messages by seconds, so a message that lands
while a read is in flight, or just before it went out, is the newer of the two: preset
slots, the controller's favorites, the touch panels' settings, the warm-up mode and the
zone words each keep what the message said.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.coordinator import KohlerKonnectCoordinator
from custom_components.kohler_konnect.konnect import Envelope

from .conftest import USERNAME, VALVE_ID, FakeMqttClient, account, unload
from .test_coordinator import MAIN, SHOWER_DATA, start
from .test_coordinator import ShowerKohler as AccountKohler
from .test_review3_valve import GetHeldKohler, stream_gap
from .test_review_writes import valve_of
from .test_shower_entities import SHOWER_ON, ShowerKohler, call, setup, solo, word
from .test_warmup import (  # noqa: F401 - `short_waits` is autouse
    ALL,
    SELECTED,
    WARMUP_SELECT,
    WarmKohler,
    _announce,
    _set_up,
    short_waits,
)


async def turns(n: int = 10) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# Preset slots
# --------------------------------------------------------------------------- #
class RacingKohler(ShowerKohler):
    """Once armed, the next `gcs-preset` read answers from before a `GCS_PRESET_STS`
    the valve pushes while that read is in flight."""

    armed = False

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        if self.armed and method.lower() == "get" and "/gcs-preset/" in url.path:
            self.armed = False
            stale = copy.deepcopy(self.gcs_presets)
            # The app changes the default shower: preset 1 now opens the showerhead.
            FakeMqttClient.instances[-1].deliver(
                {
                    "sku": "GCS",
                    "deviceid": VALVE_ID,
                    "data": {
                        "code": "GCS_PRESET_STS",
                        "attributes": [
                            {
                                "presetId": "1",
                                "name": "Default",
                                "time": "1800",
                                "Valve1": "09849C",
                                "Valve2": "000000",
                            }
                        ],
                    },
                }
            )
            await turns(5)
            return self._respond(
                method, url, (200, {"gcsPresetExperienceDetails": stale})
            )
        return await super()._api(method, url, data)


@pytest.fixture
def racing(aioclient_mock: AiohttpClientMocker) -> RacingKohler:
    return RacingKohler(aioclient_mock)


async def test_a_preset_push_during_a_preset_read_is_kept(
    hass: HomeAssistant, config_entry: MockConfigEntry, racing: RacingKohler
) -> None:
    """Undone, Shower on sent the old default shower's outlets."""
    await setup(hass, config_entry)
    valve = account(hass, config_entry).valves[0]

    racing.armed = True
    await account(hass, config_entry).async_refresh()
    await hass.async_block_till_done()
    assert not racing.armed, "the preset read did not happen"
    assert valve.gcs_state.presets[1].valve_words[0] == "09849C"

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, SHOWER_ON)
    [(zone1, _)] = racing.words()
    assert zone1.outlet_mask == 0b010


async def test_a_held_message_that_fails_does_not_fail_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    racing: RacingKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contained as a live message is: it costs itself, not the entry."""
    bad = Envelope(
        sku="GCS",
        device_id=VALVE_ID,
        code="BROKEN",
        attributes=[],
        received_at=time.time(),
    )
    init = KohlerKonnectCoordinator.__init__
    handle = KohlerKonnectCoordinator._handle_envelope

    def holding(self: KohlerKonnectCoordinator, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        assert self._early_envelopes is not None
        self._early_envelopes.append(bad)

    def handling(self: KohlerKonnectCoordinator, envelope: Envelope) -> Any:
        if envelope is bad:
            raise RuntimeError("handler bug")
        return handle(self, envelope)

    with (
        patch.object(KohlerKonnectCoordinator, "__init__", holding),
        patch.object(KohlerKonnectCoordinator, "_handle_envelope", handling),
    ):
        await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert "Kohler message BROKEN could not be handled" in caplog.text


# --------------------------------------------------------------------------- #
# The controller's favorites, and the touch panels
# --------------------------------------------------------------------------- #
@pytest.fixture
def shower(aioclient_mock: AiohttpClientMocker) -> AccountKohler:
    return AccountKohler(aioclient_mock)


@pytest.fixture
async def shower_entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({USERNAME})",
        unique_id=USERNAME,
        data=dict(SHOWER_DATA),
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


async def test_a_favorites_snapshot_during_a_favorites_read_is_kept(
    hass: HomeAssistant, shower_entry: MockConfigEntry, shower: AccountKohler
) -> None:
    shower.add_controller()
    coordinator = await start(hass, shower_entry)
    (controller,) = coordinator.controllers

    shower.gate = asyncio.Event()
    shower.gate_marker = f"/hub-experience/{MAIN}/favorites"
    seed = asyncio.ensure_future(coordinator._async_seed_controller(controller))
    for _ in range(100):
        if shower.waiting:
            break
        await asyncio.sleep(0.01)
    assert shower.waiting

    # Deleted on the controller while the read was out: the snapshot is the newer list.
    FakeMqttClient.instances[-1].deliver(
        {"sku": "HUB", "deviceid": MAIN, "data": {"code": "FAVORITES_SNAPSHOT"}}
    )
    await hass.async_block_till_done(wait_background_tasks=False)
    assert controller.favorites == []

    shower.gate.set()
    await seed
    assert controller.favorites == []


async def change_panel(hass: HomeAssistant, entity_id: str, value: Any) -> None:
    if entity_id.startswith("number."):
        service, data = "set_value", {"value": value}
    else:
        service, data = "select_option", {"option": value}
    await hass.services.async_call(
        entity_id.split(".")[0],
        service,
        {"entity_id": entity_id, **data},
        blocking=True,
    )


BRIGHTNESS = "number.anthem_valve_display_brightness"
LANGUAGE = "select.anthem_valve_panel_language"


async def test_a_second_panel_change_keeps_the_first(
    hass: HomeAssistant, shower_entry: MockConfigEntry, shower: AccountKohler
) -> None:
    """`gcsadvancestate` still shows the old brightness when the second write is built
    (this fake never catches up), so building from it alone wrote the old one back."""
    await start(hass, shower_entry)

    await change_panel(hass, BRIGHTNESS, 7)
    await change_panel(hass, LANGUAGE, "es_mx")

    first, second = shower.posted("/writeuiconfig")
    assert first["gcsUIConfigModel"]["backLight"] == "7"
    assert second["gcsUIConfigModel"]["language"] == "es"
    assert second["gcsUIConfigModel"]["backLight"] == "7"
    assert hass.states.get(BRIGHTNESS).state == "7"


async def test_a_panel_change_made_at_the_panel_is_kept_too(
    hass: HomeAssistant, shower_entry: MockConfigEntry, shower: AccountKohler
) -> None:
    """The panel's own report is newer than a read sent before the cloud caught up."""
    coordinator = await start(hass, shower_entry)
    valve = coordinator.valves[0]
    panel = valve.gcs_state.ui_config[0]["ui"]

    FakeMqttClient.instances[-1].deliver(
        {
            "sku": "GCS",
            "deviceid": valve.device_id,
            "data": {
                "code": "READ_GCS_UI_CFG",
                "attributes": [{"UI": panel, "backLight": "3"}],
            },
        }
    )
    await hass.async_block_till_done()
    await change_panel(hass, LANGUAGE, "es_mx")

    (written,) = shower.posted("/writeuiconfig")
    assert written["gcsUIConfigModel"]["backLight"] == "3"


# --------------------------------------------------------------------------- #
# The warm-up mode
# --------------------------------------------------------------------------- #
class EchoKohler(WarmKohler):
    """The valve's echo lands while the `warmup` POST is still in flight."""

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        response = await super()._api(method, url, data)
        if method.lower() == "post" and url.path.endswith("/warmup"):
            _announce(data["warmUp"])
            await turns(5)
        return response


@pytest.fixture
def echo(aioclient_mock: AiohttpClientMocker) -> EchoKohler:
    return EchoKohler(aioclient_mock)


@pytest.mark.parametrize("lagging_reads", [2, 3])
async def test_the_valves_warmup_echo_is_not_undone_by_a_lagging_read_back(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    echo: EchoKohler,
    lagging_reads: int,
) -> None:
    valve = await _set_up(hass, config_entry)
    assert valve.gcs_state.warmup_mode == ALL

    before: list[str | None] = []
    after: list[str | None] = []
    original = valve.warmup.async_read_warmup_mode

    async def recording() -> str | None:
        before.append(valve.gcs_state.warmup_mode)
        result = await original()
        after.append(valve.gcs_state.warmup_mode)
        return result

    valve.warmup.async_read_warmup_mode = recording
    echo.lagging_reads = lagging_reads
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": WARMUP_SELECT, "option": "Started Outlets"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # The echo came before the first read-back; no read still showing ALL moves it back.
    assert before[0] == SELECTED
    assert after
    assert all(mode == SELECTED for mode in after), after
    assert valve.gcs_state.warmup_mode == SELECTED


# --------------------------------------------------------------------------- #
# The zone words
# --------------------------------------------------------------------------- #
@pytest.fixture
def held(aioclient_mock: AiohttpClientMocker) -> GetHeldKohler:
    return GetHeldKohler(aioclient_mock)


@pytest.mark.parametrize("stray", [False, True], ids=["alone", "stray message"])
async def test_a_message_the_valve_state_does_not_decode_carries_nothing(
    hass: HomeAssistant, config_entry: MockConfigEntry, held: GetHeldKohler, stray: bool
) -> None:
    """It counted the last valve message's fields again, so a state read kept those old
    words over its newer reply."""
    await setup(hass, config_entry)
    valve = valve_of(hass, config_entry)
    await solo(hass, word(0b001))
    stream_gap(valve)

    # Stopped since; the cloud document says so. A state read goes out.
    held.gcs["valve1"] = held.idle_valve()
    gate = held.hold_state_read()
    read = asyncio.ensure_future(valve.async_read_live_state())
    for _ in range(50):
        await asyncio.sleep(0)
        if held.get_held:
            break
    assert held.get_held

    if stray:
        FakeMqttClient.instances[-1].deliver(
            {"sku": "", "deviceid": VALVE_ID, "data": {"code": "X", "attributes": []}}
        )
    await turns()

    gate.set()
    await read
    assert valve.gcs_state.valve1.outlet_mask == 0
