"""The sidebar panel's usage periods, and what it keeps of Kohler's answers for them."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from custom_components.kohler_konnect.sidebar import history
from custom_components.kohler_konnect.sidebar.history import _UsageCache, period_bounds

MONDAY, SUNDAY = 0, 6


@pytest.mark.parametrize(
    ("period", "anchor", "week_start", "bounds"),
    [
        # A Sunday week across New Year.
        ("week", date(2027, 1, 1), SUNDAY, (date(2026, 12, 27), date(2027, 1, 2))),
        # A Monday week that takes in the end of daylight saving time.
        ("week", date(2026, 11, 1), MONDAY, (date(2026, 10, 26), date(2026, 11, 1))),
        ("week", date(2026, 10, 25), SUNDAY, (date(2026, 10, 25), date(2026, 10, 31))),
        ("month", date(2028, 2, 14), SUNDAY, (date(2028, 2, 1), date(2028, 2, 29))),
        ("month", date(2026, 2, 28), SUNDAY, (date(2026, 2, 1), date(2026, 2, 28))),
        ("month", date(2026, 12, 31), SUNDAY, (date(2026, 12, 1), date(2026, 12, 31))),
        ("year", date(2028, 2, 29), SUNDAY, (date(2028, 1, 1), date(2028, 12, 31))),
    ],
)
def test_a_period_is_the_calendar_week_month_or_year_around_its_anchor(
    period: str, anchor: date, week_start: int, bounds: tuple[date, date]
) -> None:
    assert period_bounds(period, anchor, week_start) == bounds


def test_a_settled_period_is_kept_and_a_recent_one_for_half_an_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(history.time, "monotonic", lambda: now[0])
    cache = _UsageCache()
    cache.put(("settled",), {"rows": 1}, settled=True)
    cache.put(("recent",), {"rows": 2}, settled=False)

    now[0] += 30 * 60 - 1
    assert cache.get(("recent",)) == {"rows": 2}
    now[0] += 2
    assert cache.get(("recent",)) is None
    now[0] += 365 * 24 * 3600
    assert cache.get(("settled",)) == {"rows": 1}


def test_the_least_recently_used_answer_goes_first() -> None:
    cache = _UsageCache()
    for n in range(history._CACHE_SIZE):
        cache.put((str(n),), {"n": n}, settled=True)
    assert cache.get(("0",)) == {"n": 0}

    cache.put(("new",), {"n": -1}, settled=True)
    assert cache.get(("0",)) == {"n": 0}
    assert cache.get(("1",)) is None


def test_a_failed_read_is_not_kept() -> None:
    answers: list[Any] = [{}, RuntimeError("signed out"), {"rows": 1}]
    reads = 0

    async def fetch() -> dict[str, Any]:
        nonlocal reads
        answer = answers[reads]
        reads += 1
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def scenario() -> list[Any]:
        cache = _UsageCache()
        seen: list[Any] = [await cache.read(("k",), "account", fetch, True)]
        with pytest.raises(RuntimeError):
            await cache.read(("k",), "account", fetch, True)
        for _ in range(2):
            seen.append(await cache.read(("k",), "account", fetch, True))
        return seen

    assert asyncio.run(scenario()) == [{}, {"rows": 1}, {"rows": 1}]
    assert reads == 3


def test_an_account_reads_one_period_at_a_time() -> None:
    started: list[str] = []

    async def scenario() -> list[str]:
        cache = _UsageCache()
        gate = asyncio.Event()

        def reader(name: str) -> Any:
            async def fetch() -> dict[str, Any]:
                started.append(name)
                await gate.wait()
                return {"name": name}

            return fetch

        reads = [
            asyncio.create_task(cache.read(("a",), "account", reader("a"), True)),
            asyncio.create_task(cache.read(("a",), "account", reader("again"), True)),
            asyncio.create_task(cache.read(("b",), "account", reader("b"), True)),
            asyncio.create_task(cache.read(("c",), "other", reader("c"), True)),
        ]
        for _ in range(5):
            await asyncio.sleep(0)
        while_waiting = list(started)
        gate.set()
        answers = await asyncio.gather(*reads)
        # The repeat waited for the first read, and took its answer.
        assert answers[1] == {"name": "a"}
        return while_waiting

    assert asyncio.run(scenario()) == ["a", "c"]
    assert started == ["a", "c", "b"]
