"""Water usage for the products that report it, read and shown as a faucet's is.

A product coordinator with a usage read (`usage_product`) holds a `ProductUsage`, refreshes
it with each of its reads, and offers its payloads as `usage` and `usage_daily`. The water
totals (`water.py`) and the panel's charts (`sidebar/history.py`) read those as they read a
faucet's, so a toilet's or an EvoCycle's water adds up the same way. See
`docs/protocol/platform.md` §6.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .const import FAUCET_USAGE_REFRESH_INTERVAL
from .konnect import KohlerClient
from .product_base import ProductCoordinator, ProductEntity
from .water import (
    DailyDurationUsage,
    DailyWaterUsage,
    MonthlyDurationUsage,
    MonthlyWaterUsage,
    YearlyDurationUsage,
    YearlyWaterUsage,
)

__all__ = ["ProductUsage", "usage_entities"]


class ProductUsage:
    """One device's usage series: the monthly one for the year, the daily one for the rest.

    Read at most every half hour, and at once when the day turns. The monthly series only
    adds months that no longer change, so it is read once a month. Each is a window the
    app's own screens ask for, since nobody has seen these reads take a wider one: this
    year's months, and a month of days, reaching back far enough for the last seven.
    """

    def __init__(self, client: KohlerClient, device_id: str, product: str) -> None:
        self._client = client
        self._device_id = device_id
        #: The usage read, by its path's product: `numi`, `blade` or `evo`.
        self.product = product
        self.usage: dict[str, Any] = {}
        self.usage_daily: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._day: date | None = None
        self._month: str | None = None

    async def async_refresh(self) -> None:
        """Read whatever is due; a failed read keeps what is held until the next one.

        Only a rejected sign-in raises (`KohlerClient.async_get_usage`).
        """
        now = time.monotonic()
        local = dt_util.now().date()
        # Buckets dated in UTC put a US evening's water under tomorrow, so the window ends
        # on the later of the two dates, as the valve's does (`coordinator._usage_window`).
        end = max(local, datetime.now(UTC).date())
        due = (
            self._fetched_at is None
            or now - self._fetched_at >= FAUCET_USAGE_REFRESH_INTERVAL.total_seconds()
            or end != self._day
        )
        if not due:
            return
        self._fetched_at = now
        self._day = end
        month = local.strftime("%Y-%m")
        days_from = min(local.replace(day=1), local - timedelta(days=6))

        async def read(interval: str, start: date) -> dict[str, Any]:
            return await self._client.async_get_usage(
                self._device_id,
                from_date=start.isoformat(),
                to_date=end.isoformat(),
                interval=interval,
                product=self.product,
            )

        if month != self._month:
            monthly, daily = await asyncio.gather(
                read("MONTH", local.replace(month=1, day=1)), read("DAY", days_from)
            )
            if monthly:
                self.usage = monthly
                self._month = month
        else:
            daily = await read("DAY", days_from)
        if daily:
            self.usage_daily = daily


class _ProductWater(ProductEntity):
    """A water total read from one product's usage series. See `water.py`."""

    def __init__(self, coordinator: ProductCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key

    def _usage_series(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return self.coordinator.usage, self.coordinator.usage_daily

    def _water_units(self) -> str | None:
        return self.coordinator.account.water_units


class _Today(_ProductWater, DailyWaterUsage):
    _attr_icon = "mdi:water-check"
    _days = 1


class _Week(_ProductWater, DailyWaterUsage):
    _attr_icon = "mdi:calendar-week"
    _days = 7


class _Month(_ProductWater, MonthlyWaterUsage):
    pass


class _Year(_ProductWater, YearlyWaterUsage):
    pass


class _TodayDuration(_ProductWater, DailyDurationUsage):
    _attr_icon = "mdi:timer-outline"
    _days = 1


class _WeekDuration(_ProductWater, DailyDurationUsage):
    _attr_icon = "mdi:calendar-week"
    _days = 7


class _MonthDuration(_ProductWater, MonthlyDurationUsage):
    pass


class _YearDuration(_ProductWater, YearlyDurationUsage):
    pass


def usage_entities(coordinator: ProductCoordinator) -> list[Entity]:
    """The eight totals a faucet has, named as the faucet's are."""
    return [
        _Today(coordinator, "water_today"),
        _Week(coordinator, "water_this_week"),
        _Month(coordinator, "water_this_month"),
        _Year(coordinator, "water_this_year"),
        _TodayDuration(coordinator, "duration_today"),
        _WeekDuration(coordinator, "duration_this_week"),
        _MonthDuration(coordinator, "duration_this_month"),
        _YearDuration(coordinator, "duration_this_year"),
    ]
