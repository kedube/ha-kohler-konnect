"""The write-path review of 2026-10-16, the parts that need no Home Assistant.

`konnect/warmup_resume.py` deciding a pause is the warm-up's own only on at-temperature
evidence; the whole degrees the temperature numbers show and the ceilings they offer; each
zone's Flow reading its own zone; and the zone entities surviving a layout that shrank
under them. The write path itself — locks, the last-sent overlay, the pause flag — runs
end to end in `tests/integration/test_review_writes.py`.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.state import GcsState, OutletLimits
from custom_components.kohler_konnect.konnect.valve_hex import decode_word, encode_word
from custom_components.kohler_konnect.konnect.warmup_resume import (
    SETTLE_SECONDS,
    Decision,
    WarmupResume,
)
from custom_components.kohler_konnect.number import (
    OutletDefaultTemperatureNumber,
    OutletMaxTemperatureNumber,
    ZoneFlowNumber,
    ZoneTemperatureNumber,
    _whole_ceiling,
    _whole_degrees,
)
from custom_components.kohler_konnect.switch import ZoneOutletSwitch

from .conftest import make_coordinator, make_valve

# --------------------------------------------------------------------------- #
# No pausing warm-up: a pause resumes only on at-temperature evidence
# --------------------------------------------------------------------------- #
T0 = 1000.0
WARM = [0x07, 0x03]
UNPAUSED = [False, False]
PAUSED = [True, True]


def _warmed(at_temperature: bool = False) -> WarmupResume:
    """A watch that has seen its warm-up run for a while."""
    watch = WarmupResume(T0)
    watch.observe(T0 + 2.0, True, UNPAUSED, WARM)
    watch.observe(T0 + 30.0, True, UNPAUSED, WARM, at_temperature=at_temperature)
    return watch


def test_a_pause_with_no_at_temperature_evidence_is_left_alone_and_said_loudly():
    """The app's and the touchscreen's Stop is 0x40 — the same word as the warm-up's end."""
    outcome = _warmed().observe(T0 + 31.0, False, PAUSED, [0, 0])
    assert outcome.decision is Decision.ABANDON
    assert outcome.warn
    assert "Stop" in outcome.reason


def test_a_caller_that_passes_no_evidence_can_never_resume():
    """Leaving the argument out is "no evidence", the safe reading."""
    watch = WarmupResume(T0)
    watch.observe(T0 + 2.0, True, UNPAUSED, WARM)
    assert watch.observe(T0 + 31.0, False, PAUSED, [0, 0]).decision is Decision.ABANDON


@pytest.mark.parametrize("where", ["during", "transitional", "pause"])
def test_at_temperature_during_the_warmup_or_as_it_ends_is_evidence(where):
    watch = _warmed(at_temperature=where == "during")
    if where == "transitional":
        transitional = watch.observe(
            T0 + 31.0, False, UNPAUSED, WARM, at_temperature=True
        )
        assert transitional.decision is Decision.WAIT
    outcome = watch.observe(
        T0 + 31.1, False, PAUSED, [0, 0], at_temperature=where == "pause"
    )
    assert outcome.decision is Decision.RESUME
    assert not outcome.warn


def test_evidence_from_before_a_restarted_warmup_does_not_carry_over():
    """A second run's pause has to show its own evidence."""
    watch = _warmed(at_temperature=True)
    watch.observe(T0 + 31.0, False, UNPAUSED, WARM)
    watch.observe(T0 + 32.0, True, UNPAUSED, WARM)
    outcome = watch.observe(T0 + 40.0, False, PAUSED, [0, 0])
    assert outcome.decision is Decision.ABANDON


@pytest.mark.parametrize(
    "masks",
    [
        [0x00, 0x03],  # zone 2's half of the warm-up set, zone 1 already cleared
        [0x07, 0x00],  # zone 1's half
        [0x01, 0x02],  # some of each
    ],
)
def test_part_of_the_warmup_set_is_transitional_not_a_takeover(masks):
    """These used to abandon at once, losing the resume for a report 0.1 s from the pause."""
    watch = _warmed(at_temperature=True)
    assert watch.observe(T0 + 31.0, False, UNPAUSED, masks).decision is Decision.WAIT
    assert watch.observe(T0 + 31.1, False, PAUSED, [0, 0]).decision is Decision.RESUME


def test_part_of_the_warmup_set_running_on_past_the_settle_window_is_abandoned():
    watch = _warmed(at_temperature=True)
    watch.observe(T0 + 31.0, False, UNPAUSED, [0x01, 0x00])
    outcome = watch.observe(T0 + 31.1 + SETTLE_SECONDS, False, UNPAUSED, [0x01, 0x00])
    assert outcome.decision is Decision.ABANDON


def test_an_outlet_outside_the_warmup_set_is_still_a_takeover_even_at_temperature():
    """And it stays theirs: a pause they make afterwards is not resumed."""
    watch = _warmed(at_temperature=True)
    outcome = watch.observe(T0 + 31.0, False, UNPAUSED, [0x00, 0x04])
    assert outcome.decision is Decision.ABANDON
    assert "picked them at the wall" in outcome.reason
    later = watch.observe(T0 + 32.0, False, PAUSED, [0, 0], at_temperature=True)
    assert later is outcome


# --------------------------------------------------------------------------- #
# Whole degrees, as the Konnect app shows them
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("tenths", "fahrenheit", "expected"),
    [
        # Rounded once, half-up, from the exact value: 101.48 °F is 101, as the app shows.
        # Rounding to 0.1 first and then half-to-even made these 102 and 104.
        (386, True, 101),
        (403, True, 105),
        (477, True, 118),
        (375, True, 100),  # 99.5 °F exactly: half-up
        (385, False, 39),  # 38.5 °C: half-up, not Python's half-to-even 38
        (150, False, 15),
    ],
)
def test_whole_degrees_round_once_and_half_up(tenths, fahrenheit, expected):
    assert _whole_degrees(tenths, fahrenheit) == expected


def _zone_number(cls, model, unit, zone=1, **word):
    valve = make_valve(model, [31, 11, 1])
    coordinator = make_coordinator([valve])
    coordinator.temperature_unit = unit
    number = cls(coordinator, valve, zone)
    for key, value in word.items():
        setattr(valve.gcs_state.zone_word(zone), key, value)
    return number, valve


def test_the_temperature_number_shows_the_apps_whole_fahrenheit():
    number, _ = _zone_number(
        ZoneTemperatureNumber,
        get_valve_model("K-28210"),
        "Fahrenheit",
        temperature_celsius=38.6,
    )
    assert number.native_value == 101
    assert number.extra_state_attributes["reported_temperature"] == 101


@pytest.mark.parametrize(
    ("tenths", "unit", "expected"),
    [
        (477, "Celsius", 47),  # 48 °C would write 48.0, above a 47.7 limit
        (470, "Celsius", 47),
        (477, "Fahrenheit", 118),  # Kohler's table: 118 °F writes exactly 47.7
        (470, "Fahrenheit", 116),  # 117 °F writes 47.2, above a 47.0 limit
        (450, "Fahrenheit", 113),
    ],
)
def test_a_ceiling_is_never_rounded_above_the_limit(tenths, unit, expected):
    assert _whole_ceiling(tenths, unit) == expected


def test_the_celsius_zone_slider_stops_below_a_fractional_scald_limit():
    number, _ = _zone_number(
        ZoneTemperatureNumber, get_valve_model("K-28210"), "Celsius"
    )
    # The fixture's outlets report a 47.7 °C limit.
    assert number.native_max_value == 47


def _outlet_settings(unit: str):
    valve = make_valve(get_valve_model("K-28210"), [31, 11, 1])
    coordinator = make_coordinator([valve])
    coordinator.temperature_unit = unit
    written: list[dict] = []

    async def _write(**fields):
        written.append(fields)

    valve.async_write_outlet_setting = _write
    maximum = OutletMaxTemperatureNumber(coordinator, valve)
    default = OutletDefaultTemperatureNumber(coordinator, valve)
    for entity in (maximum, default):
        entity.async_write_ha_state = lambda: None
    return maximum, default, written


def test_a_celsius_default_above_a_fractional_scald_limit_is_refused():
    """48 °C displayed for a 47.7 °C limit, and compared in whole degrees, let 48 through."""
    _, default, written = _outlet_settings("Celsius")
    assert default.extra_state_attributes["scald_limit"] == 47
    with pytest.raises(HomeAssistantError, match="Max Temperature of 47"):
        asyncio.run(default.async_set_native_value(48))
    assert written == []
    asyncio.run(default.async_set_native_value(47))
    assert written == [{"default_temperature_tenths": 470}]


def test_a_max_temperature_below_the_default_is_refused_before_anything_is_written():
    """The fixture's default is 38.8 °C — 102 °F."""
    maximum, _, written = _outlet_settings("Fahrenheit")
    with pytest.raises(HomeAssistantError, match="below this valve's Default"):
        asyncio.run(maximum.async_set_native_value(101))
    assert written == []
    asyncio.run(maximum.async_set_native_value(102))
    assert written == [{"maximum_temperature_tenths": 388}]


# --------------------------------------------------------------------------- #
# Each zone's Flow reads its own zone
# --------------------------------------------------------------------------- #
def test_zone_twos_flow_follows_zone_two_not_zone_one():
    """`GcsState.flow_is_live` reads zone 1 only; zone 2's entity used it anyway."""
    model = get_valve_model("K-28212")
    valve = make_valve(model, [31, 11, 1, 31, 11, 1])
    coordinator = make_coordinator([valve])
    state = valve.gcs_state
    state.valve1.outlet_mask, state.valve1.flow_percent = 0, 24.5  # idle junk byte
    state.valve2.outlet_mask, state.valve2.flow_percent = 0b001, 60.0
    first = ZoneFlowNumber(coordinator, valve, 1)
    second = ZoneFlowNumber(coordinator, valve, 2)

    assert second.native_value == 60
    assert second.extra_state_attributes["flow_is_live"] is True
    assert second.extra_state_attributes["reported_flow_percent"] == 60.0
    # Zone 1 is idle, so it shows what Home Assistant holds, not its own idle byte.
    assert first.native_value == 100.0
    assert first.extra_state_attributes["flow_is_live"] is False
    assert first.extra_state_attributes["reported_flow_percent"] == 24.5


# --------------------------------------------------------------------------- #
# A layout that shrinks under the entities
# --------------------------------------------------------------------------- #
def _shrunk_valve(before: str, after: str):
    """A valve whose entities were built for ``before`` and which now reports ``after``.

    A real `GcsState`, because `zone_flow_limits` and `zone_outlets` are where the old
    layout's indices used to raise.
    """
    old, new = get_valve_model(before), get_valve_model(after)
    valve = make_valve(old, [31, 11, 1, 31, 11, 1][: old.total_outlets])
    state = GcsState(old, "Fahrenheit")
    state.valve1 = decode_word(encode_word(0x01, 38.8, 100.0, 0b111))
    state.valve2 = decode_word(encode_word(0x11, 38.8, 100.0, 0b111))
    for zone in old.zones:
        for outlet in range(1, old.outlets_in_zone(zone) + 1):
            outlet_id = old.outlet_id(zone, outlet)
            state.outlet_limits[outlet_id] = OutletLimits(
                outlet_id, 16, 200, 1800, 200, 31, 477, 150, 388, 1
            )
    valve.gcs_state = state
    coordinator = make_coordinator([valve])
    entities = {
        "temperature 2": ZoneTemperatureNumber(coordinator, valve, 2),
        "flow 2": ZoneFlowNumber(coordinator, valve, 2),
        "outlet 1.3": ZoneOutletSwitch(coordinator, valve, 1, 3),
        "outlet 2.1": ZoneOutletSwitch(coordinator, valve, 2, 1),
    }
    # What `Valve._apply_topology` does to a running valve.
    valve.model = new
    state.model = new
    if not new.uses_valve2:
        state.valve2 = None
    return entities


def _render(entity) -> dict:
    """Everything Home Assistant reads when it writes an entity's state."""
    rendered = {"available": entity.available}
    rendered["attributes"] = entity.extra_state_attributes
    if isinstance(entity, ZoneOutletSwitch):
        rendered["value"] = entity.is_on
    else:
        rendered["value"] = entity.native_value
        rendered["range"] = (entity.native_min_value, entity.native_max_value)
    return rendered


def test_zone_two_entities_go_unavailable_rather_than_raise_when_it_disappears():
    entities = _shrunk_valve("K-28212", "K-28210")
    for name in ("temperature 2", "flow 2", "outlet 2.1"):
        rendered = _render(entities[name])
        assert rendered["available"] is False, name
        assert rendered["attributes"] == {}, name
    assert _render(entities["outlet 2.1"])["value"] is None


def test_an_outlet_the_zone_no_longer_has_goes_unavailable_rather_than_raise():
    entities = _shrunk_valve("K-28212", "K-28211")
    rendered = _render(entities["outlet 1.3"])
    assert rendered == {"available": False, "attributes": {}, "value": None}
    # The zone itself is still there, so its first outlet carries on.
    assert _render(entities["outlet 2.1"])["available"] is True


# --------------------------------------------------------------------------------------- #
# A reseed inside an outlet write's catch-up window must not read the old values back
# --------------------------------------------------------------------------------------- #
class _SettingsClient:
    """`gcs-settings` answers with the outlet record the cloud still holds; state is empty.

    ``during_read`` runs while the settings read is in flight, standing in for an outlet
    write that begins then.
    """

    def __init__(self, during_read=None) -> None:
        self.during_read = during_read

    async def async_get_gcs_settings(self, device_id):
        if self.during_read is not None:
            self.during_read()
        await asyncio.sleep(0)
        return {"stale": True}

    async def async_get_gcs_state(self, device_id):
        return {}


def _reseeding_valve(client, monkeypatch, *, written_ago: float | None):
    import time

    from custom_components.kohler_konnect import coordinator as module

    from .test_entities import _seed_valve

    stale = OutletLimits(0, 16, 200, maximum_temperature_tenths=450)
    monkeypatch.setattr(
        module, "outlet_limits_from_settings", lambda settings: {0: stale}
    )
    valve = _seed_valve(client)
    written = OutletLimits(0, 16, 200, maximum_temperature_tenths=430)
    valve.gcs_state.outlet_limits[0] = written
    if written_ago is not None:
        valve._outlet_written_at = time.monotonic() - written_ago
    return valve, written, stale


@pytest.mark.asyncio
async def test_a_reseed_just_after_an_outlet_write_keeps_what_was_written(monkeypatch):
    """The cloud trails a write by ~25 s; a reconnect then must not restore the old limit."""
    valve, written, _ = _reseeding_valve(_SettingsClient(), monkeypatch, written_ago=5)
    await valve._async_seed_topology_and_state()
    assert valve.gcs_state.outlet_limits[0] is written


@pytest.mark.asyncio
async def test_a_reseed_during_which_an_outlet_write_begins_keeps_the_cache(
    monkeypatch,
):
    client = _SettingsClient()
    valve, written, _ = _reseeding_valve(client, monkeypatch, written_ago=None)

    def write_begins() -> None:
        valve._outlet_write_serial += 1

    client.during_read = write_begins
    await valve._async_seed_topology_and_state()
    assert valve.gcs_state.outlet_limits[0] is written


@pytest.mark.asyncio
async def test_a_reseed_long_after_any_outlet_write_takes_the_cloud_record(monkeypatch):
    """Guard: outside the window the read is the freshest thing known, as before."""
    valve, _, stale = _reseeding_valve(_SettingsClient(), monkeypatch, written_ago=60)
    await valve._async_seed_topology_and_state()
    assert valve.gcs_state.outlet_limits[0] is stale
