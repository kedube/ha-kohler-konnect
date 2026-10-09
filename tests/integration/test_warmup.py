"""Warm-up inside a real Home Assistant: the dropdown, auto-restore, and No pausing warm-up.

The unit tests in `tests/test_warmup.py` hold the timing and every decision branch. These
check the wiring end to end — a message on the (fake) MQTT stream, through the real
coordinator and `WarmupManager`, to a real HTTP write — with the delays shortened so a
minute's wait costs milliseconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_konnect.const import (
    CONF_LAST_WARMUP_MODE,
    CONF_VALVES,
    CONF_WARMUP_AUTO_RESTORE,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect.const import (
    WARMUP_ALL_OUTLETS_NOW,
    WARMUP_DISABLED,
    WARMUP_SELECTED_OUTLETS_NOW,
)
from custom_components.kohler_konnect.konnect.valve_hex import encode_word

from .conftest import (
    TENANT_ID,
    VALVE,
    VALVE_ID,
    FakeKohler,
    FakeMqttClient,
    account,
    unload,
    wait_for,
)

ALL = WARMUP_ALL_OUTLETS_NOW
SELECTED = WARMUP_SELECTED_OUTLETS_NOW
OFF = WARMUP_DISABLED
WARMUP_SELECT = "select.anthem_valve_warmup"

# The reference install's zone 1 warm-up set, then the two ways a warm-up ends.
WARMING_WORD = encode_word(0x01, 40.0, 100.0, 0x07)
PAUSED_WORD = encode_word(0x01, 40.0, 100.0, 0x00, paused=True)
STOPPED_WORD = encode_word(0x01, 40.0, 100.0, 0x00)
RUNNING_WORD = encode_word(0x01, 40.0, 100.0, 0x01)


class WarmKohler(FakeKohler):
    """The shared fake, with a valve whose `gcs-state` carries a warm-up mode it keeps.

    A `warmup` POST lands on the valve, and the next `gcs-state` read reports it — the
    read-back `async_set_warmup` relies on — unless `lagging_reads` makes that many reads
    still answer with the old mode, as the cloud did for ~3 s on 2026-08-20.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices.append(dict(VALVE))
        self.warmup_mode: str | None = ALL
        self.lagging_reads = 0
        self._previous_mode: str | None = None

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if method.lower() == "get" and url.path.endswith(f"/gcs-state/{VALVE_ID}"):
            mode = self.warmup_mode
            if self.lagging_reads:
                self.lagging_reads -= 1
                mode = self._previous_mode
            state: dict[str, Any] = {"totalVolume": "1"}
            if mode is not None:
                state["warmUpState"] = {"warmUp": mode, "state": "warmUpNotInProgress"}
            return self._respond(
                method, url, (200, {"state": state, "connectionState": "Connected"})
            )
        response = await super()._api(method, url, data)
        if method.lower() == "post" and url.path.endswith("/warmup"):
            self._previous_mode, self.warmup_mode = self.warmup_mode, data["warmUp"]
        return response

    def sent(self, command: str) -> list[dict[str, Any]]:
        return [body for name, body in self.commands if name == command]

    @property
    def warmup_writes(self) -> list[str]:
        return [body["warmUp"] for body in self.sent("warmup")]


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> WarmKohler:
    """Overrides the shared fixture: this account has a valve as well as the faucet."""
    return WarmKohler(aioclient_mock)


@pytest.fixture(autouse=True)
def short_waits() -> Generator[None]:
    """A minute's restore delay, the read-back retries and the evidence window, in ms.

    The daily-usage re-read a stopped shower schedules is shortened too, so
    `async_block_till_done` is never held up by it.
    """
    with (
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_AUTO_RESTORE_DELAY_SECONDS",
            0.02,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager."
            "WARMUP_CONTEXT_AFTER_SECONDS",
            0.005,
        ),
        patch(
            "custom_components.kohler_konnect.warmup_manager.WARMUP_READBACK_DELAYS",
            (0.0, 0.01, 0.01),
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_REFRESH_DELAY_SECONDS",
            0.01,
        ),
        patch(
            "custom_components.kohler_konnect.coordinator.USAGE_RETRY_DELAY_SECONDS",
            0.01,
        ),
    ):
        yield


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry, **valve_options: Any
) -> Any:
    """Set the account up with these per-valve options, and return the valve."""
    hass.config_entries.async_update_entry(
        entry, options={CONF_VALVES: {VALVE_ID: valve_options}}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = account(hass, entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await hass.async_block_till_done()
    (valve,) = coordinator.valves
    return valve


def _valve_message(code: str, **attributes: Any) -> dict[str, Any]:
    return {
        "sysid": "GCS-TEST",
        "deviceid": VALVE_ID,
        "tenantid": TENANT_ID,
        "sku": "GCS",
        "type": "STS",
        "timestamp": "1791397638",
        "data": {
            "type": "Status",
            "code": code,
            "attributes": [{"code": code, **attributes}],
        },
    }


def _announce(mode: str) -> None:
    """The valve volunteering its warm-up mode."""
    FakeMqttClient.instances[0].deliver(_valve_message("GCS_WARM_STS", warmup=mode))


def _report(word: str, warmup: str = "warmUpNotInProgress") -> None:
    """A valve status report: zone 1's word and whether a warm-up is running."""
    FakeMqttClient.instances[0].deliver(
        _valve_message(
            "GCS_SOLO_STS",
            primaryValve1=word,
            secondaryValve1="00000000",
            warmUpStatus=warmup,
        )
    )


async def _until(condition: Callable[[], bool], what: str) -> None:
    """Wait on real time without `async_block_till_done`, which a watcher would hold up."""
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for {what}")


async def _quiet(seconds: float = 0.2) -> None:
    """Give anything that was going to happen the time to happen."""
    await asyncio.sleep(seconds)


# --------------------------------------------------------------------------- #
# The dropdown
# --------------------------------------------------------------------------- #


async def test_choosing_a_mode_writes_it_and_the_dropdown_shows_the_valves_answer(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    valve = await _set_up(hass, config_entry)
    assert hass.states.get(WARMUP_SELECT).state == "All Outlets"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": WARMUP_SELECT, "option": "Started Outlets"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert kohler.warmup_writes == [SELECTED]
    assert kohler.sent("warmup")[0]["deviceId"] == VALVE_ID
    assert hass.states.get(WARMUP_SELECT).state == "Started Outlets"
    # Confirmed by the read-back, so it is what a later restore reinstates.
    assert valve.last_warmup_mode == SELECTED
    assert (
        config_entry.options[CONF_VALVES][VALVE_ID][CONF_LAST_WARMUP_MODE] == SELECTED
    )


async def test_the_dropdown_refuses_to_change_warmup_while_water_runs(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """The Konnect app blocks this too; here the refusal is said, not silently reverted."""
    await _set_up(hass, config_entry)
    _report(RUNNING_WORD)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="while the shower is running"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": WARMUP_SELECT, "option": "Off"},
            blocking=True,
        )
    assert kohler.warmup_writes == []


async def test_the_mode_the_valve_holds_at_startup_is_remembered(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """Read over REST, never announced: the gap behind the seven-hour unrestored disable."""
    valve = await _set_up(hass, config_entry)
    assert valve.last_warmup_mode == ALL


# --------------------------------------------------------------------------- #
# Auto-restore
# --------------------------------------------------------------------------- #


async def test_a_disable_from_outside_is_put_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """The hub's web UI writes `warmUpDisabled`; the valve announces it; it goes back."""
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    kohler.warmup_mode = OFF
    _announce(OFF)
    await hass.async_block_till_done()

    assert kohler.warmup_writes == [ALL]
    assert hass.states.get(WARMUP_SELECT).state == "All Outlets"


async def test_with_auto_restore_off_a_disable_stays(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    await _set_up(hass, config_entry)
    kohler.warmup_mode = OFF
    _announce(OFF)
    await hass.async_block_till_done()

    assert kohler.warmup_writes == []
    assert hass.states.get(WARMUP_SELECT).state == "Off"


@pytest.mark.parametrize("lagging_reads", [0, 3], ids=["read-back", "lagging"])
async def test_off_chosen_on_the_dropdown_stays_off_with_auto_restore_on(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: WarmKohler,
    lagging_reads: int,
) -> None:
    """Without the self-write grace, `Off` could never be selected.

    With a prompt read-back the valve's echo is a restatement; with a lagging one it lands
    as a real change into `warmUpDisabled` — and is still ours.
    """
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    kohler.lagging_reads = lagging_reads
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": WARMUP_SELECT, "option": "Off"},
        blocking=True,
    )
    _announce(OFF)  # the valve's echo, ~3.4 s later on real hardware
    await hass.async_block_till_done()
    await _quiet()
    await hass.async_block_till_done()

    assert kohler.warmup_writes == [OFF]
    assert hass.states.get(WARMUP_SELECT).state == "Off"


async def test_a_restatement_after_a_reboot_is_not_restored(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """Off at startup, a mode remembered from before, and the valve restating Off on boot."""
    kohler.warmup_mode = OFF
    await _set_up(
        hass,
        config_entry,
        **{CONF_WARMUP_AUTO_RESTORE: True, CONF_LAST_WARMUP_MODE: ALL},
    )
    for _ in range(3):
        _announce(OFF)
        await hass.async_block_till_done()
    await _quiet()
    await hass.async_block_till_done()

    assert kohler.warmup_writes == []
    assert hass.states.get(WARMUP_SELECT).state == "Off"


async def test_a_pending_restore_does_not_survive_an_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """It ends in a write to the valve and an entry update — neither safe after teardown."""
    await _set_up(hass, config_entry, **{CONF_WARMUP_AUTO_RESTORE: True})
    with patch(
        "custom_components.kohler_konnect.warmup_manager."
        "WARMUP_AUTO_RESTORE_DELAY_SECONDS",
        0.3,
    ):
        kohler.warmup_mode = OFF
        _announce(OFF)
        (valve,) = account(hass, config_entry).valves
        await _until(lambda: valve.warmup._warmup_restore_task is not None, "restore")
        await unload(hass, config_entry)
        await _quiet(0.5)

    assert kohler.warmup_writes == []


# --------------------------------------------------------------------------- #
# custom_shower, "No pausing warm-up"
# --------------------------------------------------------------------------- #


async def _custom_shower(hass: HomeAssistant, *, keep_on: bool) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN,
        "custom_shower",
        {
            "zone1_temperature": 104,
            "zone1_outlet_1": True,
            "keep_on_after_warmup": keep_on,
        },
        blocking=True,
        return_response=True,
    )


async def test_no_pausing_warmup_resends_the_shower_once_when_the_warmup_pauses(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """Left alone, the warm-up's two-minute pause ends the session; this carries it on."""
    valve = await _set_up(hass, config_entry)
    response = await _custom_shower(hass, keep_on=True)
    assert len(kohler.sent("solowritesystem")) == 1

    _report(WARMING_WORD, "warmUpInProgress")
    await _quiet(0.05)
    assert len(kohler.sent("solowritesystem")) == 1  # never during the warm-up
    _report(PAUSED_WORD)
    await _until(lambda: len(kohler.sent("solowritesystem")) == 2, "the resend")

    first, resend = kohler.sent("solowritesystem")
    # The caller's own words, exactly.
    assert resend["gcsValveControlModel"] == first["gcsValveControlModel"]
    assert resend["gcsValveControlModel"]["primaryValve1"] == response["zone1_hex"]
    await _until(lambda: valve._custom_shower_task is None, "the watcher to finish")

    # Once: a later warm-up and pause are not this command's to resume.
    _report(WARMING_WORD, "warmUpInProgress")
    _report(PAUSED_WORD)
    await _quiet()
    assert len(kohler.sent("solowritesystem")) == 2


async def test_no_pausing_warmup_leaves_a_warmup_that_ends_in_a_stop_alone(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """Somebody stopped the shower during its warm-up; it must not come back on."""
    valve = await _set_up(hass, config_entry)
    await _custom_shower(hass, keep_on=True)
    _report(WARMING_WORD, "warmUpInProgress")
    await _quiet(0.05)
    _report(STOPPED_WORD)
    await _until(lambda: valve._custom_shower_task is None, "the watcher to finish")
    _report(PAUSED_WORD)
    await _quiet()

    assert len(kohler.sent("solowritesystem")) == 1


async def test_no_pausing_warmup_leaves_outlets_picked_at_the_wall_alone(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """Outlets other than the warm-up's own, unpaused: a person took over."""
    valve = await _set_up(hass, config_entry)
    await _custom_shower(hass, keep_on=True)
    _report(WARMING_WORD, "warmUpInProgress")
    await _quiet(0.05)
    _report(encode_word(0x01, 40.0, 100.0, 0x02))
    await _until(lambda: valve._custom_shower_task is None, "the watcher to finish")
    _report(PAUSED_WORD)
    await _quiet()

    assert len(kohler.sent("solowritesystem")) == 1


async def test_without_the_option_a_custom_shower_is_written_once_and_left(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: WarmKohler
) -> None:
    """`custom_shower` fires its write once, so by itself it can never disrupt a warm-up."""
    valve = await _set_up(hass, config_entry)
    await _custom_shower(hass, keep_on=False)
    assert valve._custom_shower_task is None
    _report(WARMING_WORD, "warmUpInProgress")
    _report(PAUSED_WORD)
    await _quiet()

    assert len(kohler.sent("solowritesystem")) == 1
