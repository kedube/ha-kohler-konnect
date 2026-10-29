"""The Ice Bath journal in Home Assistant: its sessions, and nothing personal.

Against a fake of its two cloud reads (`docs/protocol/ice_bath.md`).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import DOMAIN
from custom_components.kohler_konnect.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import TENANT_ID, FakeKohler, registered_device
from .test_shower_entities import setup

JOURNAL_ID = "ibh-1761667200xyz"
PRIVATE = "Felt anxious before work"
# Midnight in Los Angeles on the first of each month, in epoch seconds.
JULY_15, AUGUST, SEPTEMBER, OCTOBER, NOVEMBER = (
    1784098800,
    1785567600,
    1788246000,
    1790838000,
    1793516400,
)
RECORDS = [
    {
        "id": f"{JOURNAL_ID}-21-10-2026",
        # Wednesday 21 October, a routine day missed.
        "record": {"routineMissed": True, "completionTime": "1792591200"},
    },
    {
        "id": f"{JOURNAL_ID}-26-10-2026",
        "record": {
            # Monday 26 October 2026, 7:00 in Los Angeles.
            "completionTime": "1793023200",
            "routineMissed": False,
            "durationMin": 3,
            "durationSec": 0,
            "temperature": 50,
            "note": PRIVATE,
            "followUp": {"energized": True, "thoughts": PRIVATE},
            "sleepQuality": {"quality": "SomewhatBetter", "thoughts": PRIVATE},
        },
    },
    {
        "id": f"{JOURNAL_ID}-27-10-2026",
        "record": {
            "completionTime": "1793109600",
            "routineMissed": False,
            "durationMin": 11,
            "durationSec": 20,
            "temperature": 39,
            "question1": "yes",
        },
    },
    {
        "id": f"{JOURNAL_ID}-28-10-2026",
        # Today, marked missed ahead: a session may still come.
        "record": {"routineMissed": True, "completionTime": "1793196000"},
    },
]


class IceBathKohler(FakeKohler):
    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.devices = [
            {
                "deviceId": JOURNAL_ID,
                "sku": "IBH",
                "logicalName": "Sam",
                # Wednesday 15 July 2026, noon in Los Angeles.
                "createdTime": 1784142000,
            }
        ]
        #: Each records read: its window, as epoch seconds.
        self.windows: list[tuple[int, int]] = []
        #: Each record written: the body.
        self.written: list[dict[str, Any]] = []
        #: Each routine update: the body.
        self.routines: list[dict[str, Any]] = []
        self.routine_fails = False

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        path = url.path
        if method.lower() == "patch" and path.endswith(
            f"/ibh-configuration/{JOURNAL_ID}/update"
        ):
            self.routines.append(data)
            return self._respond(method, url, (200, {"deviceId": JOURNAL_ID}))
        if method.lower() == "patch" and path.endswith("/ibh-record"):
            self.written.append(data)
            return self._respond(method, url, (200, {"status": "Success"}))
        if path.endswith(f"/ibh-record/{JOURNAL_ID}"):
            start, end = int(url.query["FromDate"]), int(url.query["ToDate"])
            self.windows.append((start, end))
            records = [
                entry
                for entry in RECORDS
                if start <= int(entry["record"]["completionTime"]) < end
            ]
            return self._respond(method, url, (200, {"records": records}))
        if path.endswith(f"/ibh-configuration/{JOURNAL_ID}"):
            if self.routine_fails:
                return self._respond(method, url, (500, {"error": "Internal"}))
            routine = {
                "isDaysOfWeek": True,
                "days": "0101010",
                "isReminderEnabled": True,
                "reminderTime": "07:30",
                "isFollowUpEnabled": False,
            }
            body = {"deviceId": JOURNAL_ID, "configuration": {"ibhConfig": routine}}
            return self._respond(method, url, (200, body))
        return await super()._api(method, url, data)


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> IceBathKohler:
    return IceBathKohler(aioclient_mock)


@pytest.fixture
async def wednesday(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-28T12:00:00-07:00")


async def test_a_journal_shows_its_last_session_and_its_week(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)

    assert (
        hass.states.get("sensor.sam_last_session").state == "2026-10-27T14:00:00+00:00"
    )
    duration = hass.states.get("sensor.sam_last_session_duration")
    assert (duration.state, duration.attributes["ten_minutes_or_more"]) == ("600", True)
    # Whole °F on the wire whatever the account's unit; this Home Assistant is metric.
    temperature = hass.states.get("sensor.sam_last_session_temperature")
    assert float(temperature.state) == pytest.approx(3.9, abs=0.05)
    assert temperature.attributes["unit_of_measurement"] == "°C"
    week = hass.states.get("sensor.sam_sessions_this_week")
    # The missed day isn't a session.
    assert (week.state, week.attributes["target"]) == ("2", 3)
    routine = hass.states.get("sensor.sam_routine")
    assert routine.state == "Mon, Wed, Fri"
    assert routine.attributes["days"] == ["mon", "wed", "fri"]
    assert routine.attributes["reminder_time"] == "07:30"
    # No messages to record, so no Report Log.
    assert hass.states.get("switch.sam_report_log") is None
    # Last month and this one, a calendar month at a time, as the app reads them.
    assert kohler.windows == [(SEPTEMBER, OCTOBER), (OCTOBER, NOVEMBER)]


async def test_nothing_personal_reaches_a_state_or_the_diagnostics(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)

    states = json.dumps(
        [state.as_dict() for state in hass.states.async_all()], default=str
    )
    assert PRIVATE not in states
    report = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(report)
    for secret in (PRIVATE, JOURNAL_ID, "SomewhatBetter"):
        assert secret not in text, secret
    assert report["ice_baths"][0]["sessions_read"] == 2


async def test_a_session_is_due_today_and_the_missed_day_counts(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)

    # Wednesday is a routine day, and nothing is recorded for it yet.
    assert hass.states.get("binary_sensor.sam_session_due").state == "on"
    # Last Wednesday; today, also marked, doesn't count until it is over.
    assert hass.states.get("sensor.sam_missed_this_month").state == "1"
    events = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.sam_sessions",
            "start_date_time": "2026-10-20T00:00:00-07:00",
            "end_date_time": "2026-10-29T00:00:00-07:00",
        },
        blocking=True,
        return_response=True,
    )
    summaries = [
        event["summary"] for event in events["calendar.sam_sessions"]["events"]
    ]
    # Home Assistant here is metric; the journal keeps °F.
    assert summaries == ["3:00 · 10 °C", "10+ min · 4 °C"]
    text = json.dumps(events, default=str)
    assert PRIVATE not in text


async def test_a_session_is_logged_unless_its_day_has_one(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)
    device = registered_device(hass, JOURNAL_ID)

    # Wednesday's only record is a missed day, which a session replaces.
    await hass.services.async_call(
        DOMAIN,
        "log_ice_bath_session",
        {
            "device_id": device.id,
            "duration": {"minutes": 3, "seconds": 30},
            "temperature": 4,
        },
        blocking=True,
    )
    (body,) = kohler.written
    # The app's New Record, field for field: the note empty, no answers.
    assert body == {
        "id": f"{JOURNAL_ID}-28-10-2026",
        "recordId": f"{JOURNAL_ID}-28-10-2026",
        "deviceId": JOURNAL_ID,
        "sku": "IBH",
        "operation": "Insert",
        "tenantId": TENANT_ID,
        "record": {
            "durationMin": 3,
            "durationSec": 30,
            # 4 °C, as whole °F.
            "temperature": 39,
            "note": "",
            "isManual": True,
            # Now: noon on Wednesday in Los Angeles.
            "completionTime": 1793214000,
        },
    }
    # Before writing, the guard read the day with a day either side.
    assert (1793084400, 1793343600) in kohler.windows

    with pytest.raises(HomeAssistantError) as refused:
        await hass.services.async_call(
            DOMAIN,
            "log_ice_bath_session",
            {
                "device_id": device.id,
                "duration": {"minutes": 3},
                "temperature": 4,
                "completed": "2026-10-27T08:00:00-07:00",
            },
            blocking=True,
        )
    assert refused.value.translation_key == "ice_bath_session_exists"
    assert len(kohler.written) == 1


@pytest.mark.parametrize(
    ("temperature", "fahrenheit"),
    [(1, None), (2, 36), (24, 75), (24.5, None), (25, None)],
)
async def test_a_temperature_is_logged_only_when_the_journal_can_hold_it(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
    temperature: float,
    fahrenheit: int | None,
) -> None:
    await setup(hass, config_entry)
    device = registered_device(hass, JOURNAL_ID)
    call = {
        "device_id": device.id,
        "duration": {"minutes": 2},
        "temperature": temperature,
        "completed": "2026-10-25T08:00:00-07:00",
    }

    if fahrenheit is None:
        # The °F picker's 36 to 75, in this metric Home Assistant's unit.
        with pytest.raises(ServiceValidationError) as refused:
            await hass.services.async_call(
                DOMAIN, "log_ice_bath_session", call, blocking=True
            )
        assert refused.value.translation_key == "ice_bath_temperature"
        assert refused.value.translation_placeholders["low"] == "2"
        assert kohler.written == []
    else:
        await hass.services.async_call(
            DOMAIN, "log_ice_bath_session", call, blocking=True
        )
        assert kohler.written[0]["record"]["temperature"] == fahrenheit


async def test_no_session_is_logged_before_the_journal_began(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)
    device = registered_device(hass, JOURNAL_ID)

    with pytest.raises(ServiceValidationError) as refused:
        await hass.services.async_call(
            DOMAIN,
            "log_ice_bath_session",
            {
                "device_id": device.id,
                "duration": {"minutes": 2},
                "temperature": 4,
                "completed": "2026-07-14T08:00:00-07:00",
            },
            blocking=True,
        )
    assert refused.value.translation_key == "ice_bath_before_journal"
    assert kohler.written == []


async def test_the_calendar_pages_back_a_month_at_a_time_from_the_journal_s_start(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)
    kohler.windows.clear()

    await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.sam_sessions",
            "start_date_time": "2026-06-01T00:00:00-07:00",
            "end_date_time": "2026-09-01T00:00:00-07:00",
        },
        blocking=True,
        return_response=True,
    )
    # Nothing before the day the journal began, in July.
    assert kohler.windows == [(JULY_15, AUGUST), (AUGUST, SEPTEMBER)]


async def test_sessions_stand_without_a_routine(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    kohler.routine_fails = True
    await setup(hass, config_entry)

    assert (
        hass.states.get("sensor.sam_last_session").state == "2026-10-27T14:00:00+00:00"
    )
    assert hass.states.get("sensor.sam_routine").state == "unknown"
    assert hass.states.get("binary_sensor.sam_session_due").state == "unknown"


async def test_the_action_goes_with_the_last_entry(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)
    assert hass.services.has_service(DOMAIN, "log_ice_bath_session")

    await hass.config_entries.async_unload(config_entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "log_ice_bath_session")


async def test_the_routine_changes_as_the_app_changes_it(
    hass: HomeAssistant,
    wednesday: None,
    config_entry: MockConfigEntry,
    kohler: IceBathKohler,
) -> None:
    await setup(hass, config_entry)
    device = registered_device(hass, JOURNAL_ID)
    reminders = hass.states.get("switch.sam_routine_reminders")
    assert (reminders.state, reminders.attributes["untested"]) == ("on", True)
    assert hass.states.get("switch.sam_follow_up_questions").state == "off"

    # Settings' toggle: every setting the routine has, one of them changed.
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.sam_routine_reminders"},
        blocking=True,
    )
    assert kohler.routines == [
        {
            "isDaysOfWeek": True,
            "days": "0101010",
            "isReminderEnabled": False,
            "isFollowUpEnabled": False,
            "reminderTime": "07:30",
        }
    ]
    assert hass.states.get("switch.sam_routine_reminders").state == "off"

    # The Routine screen: weekdays Sunday first, or a count; the reminder time 24-hour.
    async def routine(**data: Any) -> dict[str, Any]:
        await hass.services.async_call(
            DOMAIN,
            "set_ice_bath_routine",
            {"device_id": device.id, **data},
            blocking=True,
        )
        return kohler.routines[-1]

    body = await routine(days=["tue", "thu"], reminder_time="06:45:00")
    assert (body["isDaysOfWeek"], body["days"], body["reminderTime"]) == (
        True,
        "0010100",
        "06:45",
    )
    assert hass.states.get("sensor.sam_routine").state == "Tue, Thu"
    body = await routine(sessions_per_week=4)
    assert (body["isDaysOfWeek"], body["noOfDays"]) == (False, 4)
    assert hass.states.get("sensor.sam_routine").state == "4/week"

    # Its days or its count, never both; and something to change.
    for data in ({"days": ["mon"], "sessions_per_week": 2}, {}):
        with pytest.raises(vol.Invalid):
            await routine(**data)
    assert len(kohler.routines) == 3
