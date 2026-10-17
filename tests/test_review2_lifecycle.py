"""The pieces of the second 2026-10-16 lifecycle review that need no Home Assistant.

What a `gcs-state` or `hub-state` reply keeps when a push or a write overtook it, seed
reads left running after a sign-in failure, the capture button's updates, the order of the
two usage reads at a month's end, and the dates a usage read asks for. The rest of that
review runs end to end in `integration/test_review2_lifecycle.py`.

Each test here failed on the code before its fix, unless its docstring says it guards
the fix itself.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from .conftest import make_controller, make_coordinator, make_valve
from .test_entities import _seed_valve
from .test_review_lifecycle import _collect, _writes

COORDINATOR = "custom_components.kohler_konnect.coordinator"


# --------------------------------------------------------------------------- #
# What a REST reply keeps
# --------------------------------------------------------------------------- #
def _gcs_reply(*open_outlets: int, mode: str = "warmUpDisabled") -> dict[str, Any]:
    zone = {f"out{n}": "1" if n in open_outlets else "0" for n in (1, 2, 3)}
    zone.update(temperatureSetpoint="38.5", flowSetpoint="50", pauseFlag="0")
    return {
        "state": {
            "valve1": zone,
            "warmUpState": {"warmUp": mode, "state": "warmUpNotInProgress"},
            "currentSystemState": "normalOperation",
        }
    }


def _replying_valve(valve_model):
    """A valve with a real `GcsState` and an overlay set as a write would leave it."""
    from custom_components.kohler_konnect.konnect.state import GcsState

    valve = _seed_valve(None)
    valve.name = "Anthem Valve"
    valve.gcs_state = GcsState(valve_model)
    valve.gcs_state.apply_rest_state(_gcs_reply(1))
    word = valve.gcs_state.valve1
    valve._sent_words = (word, None)
    valve._sent_over = (word, None)
    valve._sent_at = time.monotonic()
    valve._sent_hold_seconds = 5.0
    return valve, word


@pytest.mark.parametrize(
    ("since_write", "kept"), [(1.0, True), (60.0, False)], ids=["held", "lapsed"]
)
def test_the_words_a_write_stands_in_for_are_kept_only_while_it_does(
    valve_model, since_write, kept
):
    """The reply may predate the write, so it must not end the write's overlay — but only
    while the overlay holds. `_sent_words` is cleared only when the next write asks for a
    baseline, so it outlives its hold, and trusting it alone would stop every later reseed
    from correcting the words. The lapsed case guards the fix."""
    valve, word = _replying_valve(valve_model)
    valve._sent_at = time.monotonic() - since_write
    fields = valve.apply_rest_reply(_gcs_reply(2), dict(valve._push_serials), 0)
    assert (valve.gcs_state.valve1 is word) is kept
    assert ("valve1" in fields) is kept
    assert valve.gcs_state.outlets == (
        [True, False, False] if kept else [False, True, False]
    )


def test_a_reply_keeps_only_what_a_push_carried(valve_model):
    """`GCS_WARM_STS` carries the mode alone: the words in the reply still apply."""
    valve, _ = _replying_valve(valve_model)
    valve._sent_words = None
    serials = dict(valve._push_serials)
    valve.gcs_state.warmup_mode = "warmUpAllOutletsWithNoStartDelay"
    valve._push_serials["warmup_mode"] += 1  # the announcement, during the read
    fields = valve.apply_rest_reply(_gcs_reply(), serials, 0)
    assert fields == {"warmup_mode"}
    assert valve.gcs_state.outlets == [False, False, False]
    assert valve.gcs_state.warmup_mode == "warmUpAllOutletsWithNoStartDelay"


def test_a_reply_keeps_what_an_accessory_message_reported_and_its_fault_flag(
    valve_model,
):
    """`MUSIC_STS` reports the amplifier and its `errorstate`; the zones still apply."""
    from custom_components.kohler_konnect.coordinator import (
        _HUB_PUSH_FIELDS,
        _keeping,
    )
    from custom_components.kohler_konnect.konnect.const import MSG_HUB_MUSIC
    from custom_components.kohler_konnect.konnect.state import HubState

    state = HubState(valve_model)
    state.music_on = True
    state.error_components["amplifier"] = True
    reply = {
        "state": {
            "shower": [{"zone": "1", "status": "OFF", "outlets": [0, 0, 0]}],
            "musicStateModel": {"status": "OFF"},
        },
        "errorComponent": {"amplifier": "0", "valve1": "1"},
    }
    fields, components = _HUB_PUSH_FIELDS[MSG_HUB_MUSIC]
    with _keeping(state, fields, components):
        state.apply_rest_state(reply)
    assert state.music_on is True
    assert state.error_components == {"amplifier": True, "valve1": True}
    assert state.zones[1].status == "OFF"


# --------------------------------------------------------------------------- #
# Seed reads are all let finish before a sign-in failure is raised
# --------------------------------------------------------------------------- #
class _SlowSiblings:
    """A client whose configuration read is refused at once and whose other reads are slow.

    The ordered settings/state pair answers quickly, so the seed reaches its independent
    reads while the slow ones are still under way.
    """

    def __init__(self) -> None:
        self.finished: list[str] = []

    async def _slow(self, name: str) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        self.finished.append(name)
        return {}

    async def async_get_gcs_configuration(self, device_id: str) -> dict[str, Any]:
        from custom_components.kohler_konnect.konnect import AuthError

        raise AuthError("AADB2C90080: The grant expired.")

    async def async_get_gcs_settings(self, device_id: str) -> dict[str, Any]:
        return {}

    async def async_get_gcs_state(self, device_id: str) -> dict[str, Any]:
        return {}

    async def async_get_gcs_presets(self, device_id: str) -> dict[str, Any]:
        return await self._slow("presets")

    async def async_get_gcs_about(self, device_id: str) -> dict[str, Any]:
        return await self._slow("about")

    async def async_get_usage(
        self, device_id: str, *, from_date: str, to_date: str, interval: str = "MONTH"
    ) -> dict[str, Any]:
        return await self._slow(interval)


@pytest.mark.asyncio
async def test_a_sign_in_failure_in_one_seed_read_leaves_none_of_the_others_running():
    """`gather` raised the first failure at once and left the rest running.

    They went on reading — and writing what they read into the valve — against a setup
    that had already failed.
    """
    from custom_components.kohler_konnect.konnect import AuthError

    client = _SlowSiblings()
    valve = _seed_valve(client)

    with pytest.raises(AuthError):
        await valve.async_seed()

    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    assert pending == []
    assert sorted(client.finished) == ["DAY", "MONTH", "about", "presets"]


# --------------------------------------------------------------------------- #
# The capture button describes the whole account
# --------------------------------------------------------------------------- #
def test_the_capture_button_renders_for_every_devices_messages(valve_model):
    """Its attributes are the account's one capture, which every device's messages feed."""
    other = make_valve(valve_model, [31, 11, 1], device_id="gcs-other0002")
    coordinator = make_coordinator(
        [make_valve(valve_model, [31, 11, 1]), other], [make_controller(valve_model)]
    )
    coordinator.raw_log = None
    (button,) = [
        entity
        for entity in _collect("button", coordinator)
        if entity.unique_id == "gcs-test0001_new_mqtt_capture"
    ]
    written = _writes(button)
    for changed in (frozenset({"gcs-other0002"}), frozenset({"hub-test0001"})):
        coordinator.devices_changed = changed
        button._handle_coordinator_update()
    assert len(written) == 2


# --------------------------------------------------------------------------- #
# A month's end: the monthly series is read before the daily one is replaced
# --------------------------------------------------------------------------- #
def _series(litres: dict[str, float]) -> dict[str, Any]:
    return {
        "gcsUsageDataDetailsList": [
            {"intervalKey": key, "volume": value} for key, value in litres.items()
        ]
    }


@pytest.mark.asyncio
async def test_a_month_rollover_never_lowers_last_month(monkeypatch):
    """The new daily window starts part-way into last month, which counts the larger of
    its monthly and daily figures — so swapped in before the monthly read, the daily
    series lowered last month, and the year with it, until that read landed."""
    from custom_components.kohler_konnect import coordinator as module
    from custom_components.kohler_konnect.water import _month_totals

    monkeypatch.setattr(
        dt_util, "now", lambda time_zone=None: datetime(2026, 11, 10, 8, tzinfo=UTC)
    )
    replies = {
        # Read once October was over: the whole month.
        "MONTH": _series({"2026-10": 60.0, "2026-11": 5.0}),
        # Thirty-five days back from 10 November: October from the 6th only.
        "DAY": _series({"2026-10-15": 30.0, "2026-11-05": 5.0}),
    }
    seen: list[float] = []

    async def _get_usage(_device_id, *, from_date, to_date, interval="MONTH"):
        # What `Water Used This Year` would count for October, as each read goes out.
        seen.append(
            _month_totals(holder.usage, holder.usage_daily)["2026-10"]["volume"]
        )
        await asyncio.sleep(0)
        return replies[interval]

    holder = SimpleNamespace(
        gcs_device=SimpleNamespace(device_id="gcs-1"),
        client=SimpleNamespace(async_get_usage=_get_usage),
        # Read early in October: only part of it.
        usage=_series({"2026-10": 10.0}),
        # Read after the last October shower: the 1st to the 28th.
        usage_daily=_series({"2026-10-01": 20.0, "2026-10-15": 30.0}),
        _usage_seeded_month="2026-10",
    )
    holder.async_refresh_monthly_usage = lambda: (
        module.Valve.async_refresh_monthly_usage(holder)
    )

    await module.Valve.async_refresh_daily_usage(holder)

    assert min(seen) >= 50.0
    totals = _month_totals(holder.usage, holder.usage_daily)
    assert totals["2026-10"]["volume"] == 60.0
    assert holder._usage_seeded_month == "2026-11"


# --------------------------------------------------------------------------- #
# The dates a usage read asks for
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("zone", "utc_now", "today"),
    [
        # Half past midnight in Berlin, still the evening before in UTC: the local date.
        ("Europe/Berlin", "2026-10-15T22:30:00", "2026-10-16"),
        # Eight in the evening in Chicago, already tomorrow in UTC: the UTC date, as
        # before — where an evening's water sits on accounts with UTC buckets.
        ("America/Chicago", "2026-10-16T01:00:00", "2026-10-16"),
    ],
    ids=["east-of-utc", "west-of-utc"],
)
@pytest.mark.asyncio
async def test_a_usage_read_runs_to_the_later_of_the_local_and_utc_dates(
    monkeypatch, zone, utc_now, today
):
    """East of UTC the read ended a day early from local midnight until UTC's, leaving a
    shower taken then out of `Water Used Today`. The west-of-UTC case guards the fix."""
    from custom_components.kohler_konnect import coordinator as module

    local = ZoneInfo(zone)
    fixed = datetime.fromisoformat(utc_now).replace(tzinfo=UTC)

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz in (None, UTC) else fixed.astimezone(tz)

    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", local)
    monkeypatch.setattr(f"{COORDINATOR}.datetime", _Pinned)
    monkeypatch.setattr(
        dt_util, "now", lambda time_zone=None: fixed.astimezone(time_zone or local)
    )
    asked: list[tuple[str, str, str]] = []

    async def _get_usage(_device_id, *, from_date, to_date, interval="MONTH"):
        asked.append((interval, from_date, to_date))
        return _series({f"{today}": 1.0})

    holder = SimpleNamespace(
        gcs_device=SimpleNamespace(device_id="gcs-1"),
        client=SimpleNamespace(async_get_usage=_get_usage),
        usage={},
        usage_daily={},
        _usage_seeded_month=None,
    )
    holder.async_refresh_monthly_usage = lambda: (
        module.Valve.async_refresh_monthly_usage(holder)
    )

    await module.Valve.async_refresh_daily_usage(holder)

    assert {interval: to_date for interval, _, to_date in asked} == {
        "MONTH": today,
        "DAY": today,
    }


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16): what a report carried, field by field
# --------------------------------------------------------------------------- #
def test_a_status_report_carries_only_what_it_had(valve_model):
    """A zone word that did not decode, or a session field the report left out, is not
    something it reported — nor a reason to keep a read's value out."""
    from custom_components.kohler_konnect.konnect.mqtt import Envelope
    from custom_components.kohler_konnect.konnect.state import GcsState

    state = GcsState(valve_model)

    def carried(attribute):
        state.apply_envelope(
            Envelope(
                sku="GCS",
                device_id="gcs-1",
                code="GCS_SOLO_STS",
                attributes=[attribute],
                received_at=time.time(),
            )
        )
        return state.carried

    assert carried(
        {"primaryValve1": "zz", "currentSystemState": "normalOperation"}
    ) == {"system_state"}
    assert carried({"primaryValve1": "0184C80100000001"}) == {"valve1"}


def test_a_read_just_after_a_report_keeps_only_what_the_report_carried(valve_model):
    """A report whose zone 1 word was garbled kept zone 1 shown running over a read that
    had it stopped; any status report kept every session field out."""
    valve, _ = _replying_valve(valve_model)
    valve._sent_words = None
    now = time.monotonic()
    valve._reported_at = {"valve2": now, "system_state": now}
    fields = valve.apply_rest_reply(
        _gcs_reply(2), dict(valve._push_serials), 0, requested_at=now
    )
    assert fields == {"valve2", "system_state"}
    assert valve.gcs_state.outlets == [False, True, False]
