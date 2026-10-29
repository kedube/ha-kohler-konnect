"""The Ice Bath journal, offline: which records are sessions, and what a routine asks for."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from custom_components.kohler_konnect.konnect.client import (
    KohlerClient,
    _redact_payload,
)
from custom_components.kohler_konnect.konnect.ice_bath import (
    missed_days,
    record_request,
    routine_target,
    sessions,
    taken,
    week_start,
)

LOS_ANGELES = ZoneInfo("America/Los_Angeles")


def record(**fields: object) -> dict[str, object]:
    return {"id": "ibh-1-01-10-2026", "record": fields}


def test_sessions_are_the_records_the_app_counts_and_read_ten_plus() -> None:
    found = sessions(
        {
            "records": [
                record(
                    completionTime="1790100000",
                    durationMin=11,
                    durationSec=40,
                    temperature=41,
                    routineMissed=False,
                ),
                record(
                    completionTime="1790000000",
                    durationMin=4,
                    durationSec=30,
                    temperature=39,
                    routineMissed=False,
                ),
                record(completionTime="1790200000", routineMissed=True, durationMin=0),
                # The app counts a record only when it is marked not missed.
                record(completionTime="1790300000", durationMin=2),
                # A length below zero is none.
                record(
                    completionTime="1790400000", durationMin=-2, routineMissed=False
                ),
                # A time out of range, or none, makes no session.
                record(completionTime="9" * 14, durationMin=2, routineMissed=False),
                record(durationMin=3, routineMissed=False),
                "not a record",
            ]
        }
    )
    assert [(s.duration, s.ten_plus, s.temperature) for s in found] == [
        (270, False, 39),
        # "10+": ten minutes or more, not eleven, and its seconds don't count.
        (600, True, 41),
        (0, False, None),
    ]
    assert sessions(None) == []


def test_a_routine_asks_for_its_weekdays_or_its_count() -> None:
    def config(**fields: object) -> dict[str, object]:
        return {"configuration": {"ibhConfig": fields}}

    assert routine_target(config(isDaysOfWeek=True, days="0101010")) == 3
    # No day picked is no routine, as the app reads it.
    assert routine_target(config(isDaysOfWeek=True, days="0000000")) is None
    assert routine_target(config(isDaysOfWeek=False, noOfDays=4)) == 4
    assert routine_target(config(isDaysOfWeek=False, noOfDays=9)) is None
    assert routine_target({}) is None


def test_the_week_starts_on_sunday_as_the_app_counts_it() -> None:
    assert week_start(date(2026, 10, 28)) == date(2026, 10, 25)
    assert week_start(date(2026, 10, 25)) == date(2026, 10, 25)


def test_journal_paths_reach_logs_without_the_id() -> None:
    path = "/devices/api/v1/device-management/ibh-record/ibh-1761667200xyz?FromDate=1&ToDate=2"
    assert "1761667200xyz" not in KohlerClient.safe_path(path)


def test_missed_days_are_dated_by_when_they_completed_here() -> None:
    payload = {
        "records": [
            # 19:00 on the 27th in Los Angeles is already the 28th in UTC.
            {
                "id": "ibh-1-28-10-2026",
                "record": {"routineMissed": True, "completionTime": "1793152800"},
            },
            {"id": "ibh-1-25-10-2026", "record": {"routineMissed": False}},
            # With no time, the day it is filed under.
            {"id": "ibh-1-26-10-2026", "record": {"routineMissed": True}},
        ]
    }
    assert missed_days(payload, LOS_ANGELES) == [
        date(2026, 10, 26),
        date(2026, 10, 27),
    ]


def test_a_day_is_taken_by_a_session_filed_under_it_or_completed_on_it() -> None:
    payload = {
        "records": [
            # Filed under the 25th by a phone in another zone; the 26th here.
            {
                "id": "ibh-1-25-10-2026",
                "record": {"routineMissed": False, "completionTime": "1793026800"},
            },
            {"id": "ibh-1-28-10-2026", "record": {"routineMissed": True}},
        ]
    }
    assert taken(payload, "ibh-1-25-10-2026", date(2026, 10, 25), LOS_ANGELES)
    assert taken(payload, "ibh-1-26-10-2026", date(2026, 10, 26), LOS_ANGELES)
    # A missed day holds no session to lose.
    assert not taken(payload, "ibh-1-28-10-2026", date(2026, 10, 28), LOS_ANGELES)
    assert not taken(payload, "ibh-1-27-10-2026", date(2026, 10, 27), LOS_ANGELES)


def test_the_rest_debug_log_keeps_health_notes_out() -> None:
    payload = {
        "records": [
            {
                "id": "ibh-1-27-10-2026",
                "record": {
                    "durationMin": 3,
                    "note": "Felt anxious",
                    "question1": "Q-ONE",
                    "Question2": "Q-TWO",
                    "followUp": {"energized": True, "thoughts": "Calmer"},
                    "sleepQuality": {"quality": "SomewhatBetter"},
                },
            }
        ]
    }
    text = json.dumps(_redact_payload(payload))
    for secret in ("Felt anxious", "Q-ONE", "Q-TWO", "Calmer", "SomewhatBetter"):
        assert secret not in text, secret
    # Every key stays, to show which fields exist.
    assert '"durationMin": 3' in text
    assert '"sleepQuality": "**REDACTED**"' in text


def test_a_logged_session_is_filed_under_its_day_with_ten_plus_as_the_app_keeps_it() -> (
    None
):
    completed = datetime(2026, 10, 28, 19, 0, tzinfo=UTC)
    body = record_request(
        "ibh-1",
        "tenant",
        day=date(2026, 10, 28),
        completed=completed,
        seconds=185,
        fahrenheit=39,
    )
    assert body == {
        "id": "ibh-1-28-10-2026",
        "recordId": "ibh-1-28-10-2026",
        "deviceId": "ibh-1",
        "sku": "IBH",
        "operation": "Insert",
        "tenantId": "tenant",
        "record": {
            "durationMin": 3,
            "durationSec": 5,
            "temperature": 39,
            # Sent empty, as the app sends it when nothing is typed.
            "note": "",
            "isManual": True,
            "completionTime": 1793214000,
        },
    }
    long = record_request(
        "ibh-1",
        "tenant",
        day=date(2026, 10, 28),
        completed=completed,
        seconds=754,
        fahrenheit=39,
    )
    assert (long["record"]["durationMin"], long["record"]["durationSec"]) == (11, 0)
