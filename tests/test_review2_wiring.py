"""The joins between the 2026-10-16 fixes, each written against the others' contracts.

Three places hand something across: the valve's state read gives the cloud watch the time
it was sent, the reseed's `gcs-settings` read goes through the outlet-write guard, and the
coordinator's sign-in falls back on the token a predecessor stored during a reload.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from custom_components.kohler_konnect import water
from custom_components.kohler_konnect.const import CONF_REFRESH_TOKEN
from custom_components.kohler_konnect.coordinator import KohlerKonnectCoordinator
from custom_components.kohler_konnect.konnect import usage
from custom_components.kohler_konnect.konnect.state import OutletLimits
from custom_components.kohler_konnect.sensor import ControllerZoneTemperatureSensor

from .test_entities import _seed_valve


class _StateClient:
    """`gcs-settings` holds ``settings``; `gcs-state` answers an empty document."""

    def __init__(self, settings=None) -> None:
        self.settings = settings or {}

    async def async_get_gcs_settings(self, device_id):
        return self.settings

    async def async_get_gcs_state(self, device_id):
        return {}


class _Watch:
    """A cloud watch that records what each REST answer was handed over with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def note_rest_payload(self, payload, trigger, **kwargs) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_the_state_read_tells_the_cloud_watch_when_it_was_sent():
    """Without the send time a lagging `Disconnected` overruled a newer valve message."""
    valve = _seed_valve(_StateClient())
    valve.cloud_watch = _Watch()
    valve.apply_rest_reply = lambda payload, serials, writes, **kwargs: set()
    valve._note_running_for_usage = lambda: None
    before = time.monotonic()

    await valve.async_read_live_state()

    (call,) = valve.cloud_watch.calls
    assert before <= call["requested_at"] <= time.monotonic()
    assert call["notify"] is False


@pytest.mark.asyncio
async def test_a_reseed_does_not_undo_an_unconfirmed_outlet_write(monkeypatch):
    """Past the settle window, the cloud can still show the value a write replaced.

    The scald limit was lowered to 43.0 °C from here and no read has shown it yet; a
    reconnect's `gcs-settings` still says 45.0 °C. The cache must keep 43.0, because the
    next outlet write echoes it to every outlet.
    """
    from custom_components.kohler_konnect import coordinator as module

    stale = OutletLimits(0, 16, 200, maximum_temperature_tenths=450)
    monkeypatch.setattr(
        module, "outlet_limits_from_settings", lambda settings: {0: stale}
    )
    valve = _seed_valve(_StateClient())
    valve.name = "Anthem Valve"
    issues: list[tuple] = []
    valve._raise_write_issue = lambda *args: issues.append(args)
    valve.gcs_state.outlet_limits[0] = OutletLimits(
        0, 16, 200, maximum_temperature_tenths=430
    )
    valve._outlet_unconfirmed = {"maximum_temperature_tenths": 430}
    valve._outlet_written_at = time.monotonic() - 120  # long past the settle window

    await valve._async_seed_topology_and_state()

    assert valve.gcs_state.outlet_limits[0].maximum_temperature_tenths == 430
    # Past the window, so the setting is disputed: no other outlet setting may be sent
    # with it until it is settled, and its repair issue says so.
    assert valve._outlet_disputed == {"maximum_temperature_tenths": 450}
    ((fields, _setting, detail),) = issues
    assert fields == ("maximum_temperature_tenths",)
    assert "Max Temperature (45.0 °C)" in detail


def _coordinator(*, closed: bool, token: str | None) -> SimpleNamespace:
    stand_in = SimpleNamespace(
        closed=closed, entry=SimpleNamespace(data={CONF_REFRESH_TOKEN: token})
    )
    stand_in.read = KohlerKonnectCoordinator._stored_refresh_token.__get__(stand_in)
    return stand_in


def test_sign_in_can_fall_back_on_the_stored_token_while_loaded():
    assert _coordinator(closed=False, token="rotated").read() == "rotated"


def test_an_unloaded_coordinator_offers_no_token_to_fall_back_on():
    """Else the old sign-in could redeem the token its successor is holding."""
    assert _coordinator(closed=True, token="rotated").read() is None


# --------------------------------------------------------------------------- #
# Non-finite numbers from Kohler's JSON
# --------------------------------------------------------------------------- #
# Python's JSON parser takes a bare `NaN` or `Infinity` as a float, which passes every
# `isinstance(value, (int, float))` check; Home Assistant then refuses the sensor's value.
NON_FINITE = json.loads("[NaN, Infinity, -Infinity]")


@pytest.mark.parametrize("value", [*NON_FINITE, True])
def test_a_faucet_usage_figure_must_be_a_finite_number(value):
    assert usage._number(value) is None
    assert usage._number(2) == 2.0


def test_a_non_finite_usage_bucket_is_left_out_of_the_totals():
    series = {
        "gcsUsageDataDetailsList": [
            {"intervalKey": "2026-10-14", "volume": 40.0, "onDuration": 300},
            {"intervalKey": "2026-10-15", "volume": NON_FINITE[0], "onDuration": 60},
            {"intervalKey": "2026-10-16", "volume": 25.0, "onDuration": NON_FINITE[1]},
        ]
    }
    # A non-finite figure leaves its bucket out of that figure's totals only.
    assert water._month_totals(None, series)["2026-10"] == {"volume": 65.0}
    durations = water._month_totals(None, series, figure="onDuration")
    assert durations["2026-10"] == {"onDuration": 360.0}


def _zone_temperature(raw: str, *, fahrenheit: bool = True) -> float | None:
    zone = SimpleNamespace(temperature=raw)
    entity = SimpleNamespace(
        _state=SimpleNamespace(zones={1: zone}), _zone=1, _account_fahrenheit=fahrenheit
    )
    return ControllerZoneTemperatureSensor.native_value.fget(entity)


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-inf"])
def test_a_controller_zone_temperature_that_is_not_finite_reads_as_unknown(raw):
    assert _zone_temperature(raw) is None
    assert _zone_temperature("102") == 102


@pytest.mark.parametrize(
    ("raw", "fahrenheit", "reported"),
    [
        # A Fahrenheit account reads °F, full cold included (live).
        ("102", True, 102),
        ("32", True, 32),
        # A Celsius account: whichever unit the controller uses, the reading lands in °F.
        ("102", False, 102),
        ("38.9", False, 102.0),
        ("15", False, 59.0),
    ],
)
def test_a_controller_zone_temperature_is_reported_in_fahrenheit(
    raw, fahrenheit, reported
):
    assert _zone_temperature(raw, fahrenheit=fahrenheit) == reported
