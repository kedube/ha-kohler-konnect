"""One EvoCycle: its state read over REST, kept current by the `data` blocks of its messages.

The app has no polling: it reads when a screen opens and merges each message's `shower`,
`clean` and `product` blocks (`docs/protocol/evocycle.md` §2.2, §4). This reads the state
and the stored defaults at setup, after a reconnect and on the product's slow clock.

Its commands run water and ozone, so they follow the app's interlocks and add two of
their own (§7.2, §7.6): an ozone clean never starts while the shower runs, nothing starts
that the shower's fault stops in the app, and a shower never goes out with a temperature
or flow of 0. Settings changes go one at a time, each from a fresh read (`write_lock`).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, FAUCET_USAGE_REFRESH_INTERVAL, FIRMWARE_CHECK_INTERVAL
from ..konnect import Device, Envelope, KohlerError
from ..konnect.evocycle import (
    CLEAN_FIELDS,
    CLEANS,
    DEFAULT_FIELDS,
    MODES,
    EvoCycleDevice,
    activity,
    apply_data,
    clean,
    clean_reminders,
    default_settings,
    fault,
    fault_stops,
    goal_request,
    interface,
    interface_settings,
    savings_months,
    shower,
    shower_defaults,
    week_split,
)
from ..konnect.faucet import FirmwareInfo, parse_firmware
from ..product_base import PendingWrites, ProductCoordinator
from ..product_usage import ProductUsage

if TYPE_CHECKING:
    from ..coordinator import KohlerKonnectCoordinator

__all__ = ["EvoCycleCoordinator"]

_LOGGER = logging.getLogger(__name__)

EVOCYCLE_OFFLINE = (
    "The EvoCycle is offline. Check that it has power and Wi-Fi, then try again."
)
# The goal can change in the app with nothing announced; the app reads it as its screen
# opens, which this stands in for.
_RECORD_REFRESH = timedelta(hours=1)
# The interface settings a `uisetting` write sets, by the names they are read back under.
_INTERFACE_FIELDS = ("language", "temperatureUnits", "flowUnits", "hapticFeedback")


class EvoCycleCoordinator(ProductCoordinator):
    """One EvoCycle shower."""

    model = "EvoCycle"

    def __init__(
        self, account: KohlerKonnectCoordinator, device: Device, name: str
    ) -> None:
        super().__init__(account, device, name)
        self.device = EvoCycleDevice(self.client, self.device_id, self.sku)
        self.firmware: FirmwareInfo | None = None
        self._firmware_read_at: float | None = None
        #: The last replies, as read, for diagnostics.
        self.raw: dict[str, Any] = {}
        self.usage_reader = ProductUsage(self.client, self.device_id, "evo")
        #: The account's record of this shower, which holds its water-savings goal (§3.8).
        self.record: dict[str, Any] = dict(device.raw)
        self._record_read_at = time.monotonic()
        self._pending_shower = PendingWrites()
        self._pending_clean = PendingWrites()
        self._pending_interface = PendingWrites()
        #: The Water Savings screen's figures (§6.3): this week's cycled and fresh litres,
        #: and this year's saved litres and cycle-mode share by `yyyy-MM`.
        self.savings: dict[str, Any] = {}
        self._savings_read_at: float | None = None

    async def async_read(self) -> dict[str, Any]:
        reply = await self.device.async_get_state()
        try:
            configuration = await self.device.async_get_configuration()
        except KohlerError as err:
            # The stored defaults aren't live: keep the last ones.
            _LOGGER.debug("Could not read %s's settings: %s", self.device_name, err)
            configuration = self.raw.get("configuration") or {}
        await self._async_check_firmware()
        await self._async_check_record()
        await self._async_check_savings()
        self.raw = {"state": reply, "configuration": configuration}
        live = reply.get("state")
        return {
            "connection": reply.get("connectionState"),
            "state": dict(live) if isinstance(live, dict) else {},
            "saved_litres": reply.get("recycledWaterUsed"),
            "configuration": configuration,
        }

    async def _async_check_firmware(self) -> None:
        now = time.monotonic()
        read_at = self._firmware_read_at
        if (
            read_at is not None
            and now - read_at < FIRMWARE_CHECK_INTERVAL.total_seconds()
        ):
            return
        self._firmware_read_at = now
        if (
            firmware := parse_firmware(
                await self.client.async_get_firmware(self.device_id, "evo")
            )
        ) is not None:
            self.firmware = firmware

    async def _async_check_record(self) -> None:
        """Read the account's record of the shower again, hourly, for its goal."""
        now = time.monotonic()
        if now - self._record_read_at < _RECORD_REFRESH.total_seconds():
            return
        self._record_read_at = now
        try:
            customer = await self.client.async_get_customer()
        except KohlerError as err:
            _LOGGER.debug("Could not read %s's goal: %s", self.device_name, err)
            return
        if (found := customer.record(self.device_id)) is not None:
            self.record = dict(found[1])

    async def _async_check_savings(self) -> None:
        """Read the Water Savings screen's two reads, at the usage reads' pace. Either
        failing keeps what was read before: neither is live."""
        now = time.monotonic()
        read_at = self._savings_read_at
        if (
            read_at is not None
            and now - read_at < FAUCET_USAGE_REFRESH_INTERVAL.total_seconds()
        ):
            return
        self._savings_read_at = now
        today = dt_util.now().date()
        try:
            cycled, fresh = week_split(await self.device.async_get_week(today))
            months = savings_months(await self.device.async_get_savings(today.year))
        except KohlerError as err:
            _LOGGER.debug("Could not read %s's savings: %s", self.device_name, err)
            return
        self.savings = {"cycled": cycled, "fresh": fresh, "months": months}

    def apply_envelope(self, envelope: Envelope) -> bool:
        """Merge a message's `data` blocks; it has no code (§4.1). `INF` ones are dropped."""
        if envelope.raw.get("type") == "INF":
            return True
        data = envelope.raw.get("data")
        if not isinstance(data, dict) or not self.data:
            return False
        self.data = {
            **self.data,
            "state": apply_data(self.state, data, int(time.time())),
        }
        return True

    # --- what the entities read ---------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        return (self.data or {}).get("state") or {}

    @property
    def online(self) -> bool | None:
        connection = (self.data or {}).get("connection")
        if not isinstance(connection, str):
            return None
        return connection.strip().casefold() != "disconnected"

    @property
    def activity(self) -> str | None:
        return activity(self.state)

    @property
    def fault(self) -> int | None:
        return fault(self.state)

    @property
    def _configuration(self) -> dict[str, Any]:
        return (self.data or {}).get("configuration") or {}

    @property
    def shower_configuration(self) -> dict[str, Any]:
        """The stored defaults, with any just written that Kohler doesn't show yet."""
        return self._pending_shower.over(shower_defaults(self._configuration))

    @property
    def clean_configuration(self) -> dict[str, Any]:
        """The clean reminders, with any just written that Kohler doesn't show yet."""
        return self._pending_clean.over(clean_reminders(self._configuration))

    @property
    def temperature_ceiling(self) -> int:
        """The highest default temperature, in tenths of °C (§3.5)."""
        return _ceiling(self.shower_configuration)

    # --- commands --------------------------------------------------------------------

    def _refuse(self, key: str, **placeholders: str) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={"name": self.device_name, **placeholders},
        )

    def _refuse_if_faulted(self, what: str) -> None:
        """Refuse what the shower's fault stops in the app (`fault_stops`)."""
        if fault_stops(code := self.fault, what):
            raise self._refuse("evocycle_fault", code=str(code))

    async def _async_send(self, command: str, body: dict[str, Any]) -> None:
        await self.async_command(
            self.device.async_command(command, body), EVOCYCLE_OFFLINE
        )

    async def _async_running(self, kinds: dict[str, int]) -> str | None:
        """Which of ``kinds`` runs, for a stop. Read again when the state says none
        does: the stop is what matters if a message was missed."""
        if self.activity not in kinds:
            await self.async_refresh()
        running = self.activity
        return running if running in kinds else None

    async def async_set_shower(self, mode: str | None) -> None:
        """Start the shower in one mode, or stop it (``None``), as the app's cards do.

        A start carries that mode's temperature and flow from the state, else the stored
        defaults; with neither it is refused rather than sent as 0, as the app would.
        Choosing the mode that runs already does nothing.
        """
        if mode is None:
            if (running := await self._async_running(MODES)) is None:
                return
            block = shower(self.state, running)
            await self._async_send(
                "shower",
                {
                    "showerOn": 0,
                    "showerMode": MODES[running],
                    "temperature": _whole(block.get("temperature")) or 0,
                    "flow": _whole(block.get("flow")) or 0,
                },
            )
            return
        running = self.activity
        if mode == running:
            return
        if running in CLEANS or running == "cancelling":
            raise self._refuse("evocycle_cleaning")
        self._refuse_if_faulted(mode)
        block = shower(self.state, mode)
        defaults = self.shower_configuration
        temperature = _whole(block.get("temperature")) or _whole(
            defaults.get("defaultTemperature")
        )
        flow = _whole(block.get("flow")) or _whole(defaults.get("defaultFlow"))
        if not temperature or not flow:
            raise self._refuse("settings_unreadable")
        await self._async_send(
            "shower",
            {
                "showerOn": 1,
                "showerMode": MODES[mode],
                "temperature": temperature,
                "flow": flow,
            },
        )

    async def async_clean(self, kind: str | None) -> None:
        """Start a Rapid or Deep ozone clean, or stop the one running (``None``).

        ⚠️ Ozone runs through the shower: a clean never starts while it runs, or during
        another clean or a fault.
        """
        if kind is None:
            if (running := await self._async_running(CLEANS)) is None:
                return
            await self._async_send(
                "clean", {"cleanOn": 0, "cleanMode": CLEANS[running]}
            )
            return
        if self.activity is not None:
            raise self._refuse("evocycle_running")
        self._refuse_if_faulted(kind)
        await self._async_send("clean", {"cleanOn": 1, "cleanMode": CLEANS[kind]})

    async def _async_fresh(self) -> dict[str, Any]:
        """The stored settings, read now, after the state says nothing runs: the app
        reads the state afresh before every save."""
        with self.account.command_errors(EVOCYCLE_OFFLINE):
            reply = await self.device.async_get_state()
            if activity(_mapping(reply.get("state"))) is not None:
                raise self._refuse("settings_while_running")
            return await self.device.async_get_configuration()

    async def _async_save(
        self, command: str, body: dict[str, Any], pending: PendingWrites, read: Any
    ) -> None:
        """Write one settings document, and show what it set until Kohler does."""
        with self.account.command_errors(EVOCYCLE_OFFLINE):
            await self.device.async_command(command, body)
        pending.add(read)
        self.async_update_listeners()

    async def async_set_default(self, field: str, value: int) -> None:
        """Change one stored default; all six are written at once, so read them first.

        The default temperature stops at the lower of the two maximum temperatures, and
        lowering the Standard maximum below it lowers it too, as in the app (§3.5).
        """
        async with self.write_lock:
            stored = self._pending_shower.over(
                shower_defaults(await self._async_fresh())
            )
            changes = {field: value}
            if field == "temperature":
                changes[field] = min(value, _ceiling(stored))
            elif field == "standardModeMaxTemperature":
                default = _whole(stored.get("defaultTemperature"))
                if default is not None and default > value:
                    changes["temperature"] = value
            body = default_settings(stored, changes)
            if body is None:
                raise self._refuse("settings_unreadable")
            await self._async_save(
                "showersetting",
                body,
                self._pending_shower,
                {DEFAULT_FIELDS[key]: number for key, number in changes.items()},
            )
        await self.async_after_change()

    async def async_set_reminder(self, field: str, on: bool) -> None:
        """Turn one clean reminder on or off; all three are written at once."""
        async with self.write_lock:
            stored = self._pending_clean.over(
                clean_reminders(await self._async_fresh())
            )
            # A reminder never read counts as on, as the app shows it (§2.3).
            body = {
                name: _flag(stored.get(source)) for name, source in CLEAN_FIELDS.items()
            }
            body[field] = 1 if on else 0
            await self._async_save(
                "cleansetting",
                body,
                self._pending_clean,
                {CLEAN_FIELDS[field]: body[field]},
            )
        await self.async_after_change()

    @property
    def interface(self) -> dict[str, Any]:
        """The digital interface's settings: language, units, haptics (§3.7)."""
        return self._pending_interface.over(interface(self._configuration))

    async def async_set_interface(self, **changes: Any) -> None:
        """Change one interface setting; all of them are written at once, so read first."""
        async with self.write_lock:
            panel = self._pending_interface.over(interface(await self._async_fresh()))
            body = interface_settings(panel, changes)
            if body is None:
                raise self._refuse("settings_unreadable")
            await self._async_save(
                "uisetting",
                body,
                self._pending_interface,
                {key: body[key] for key in _INTERFACE_FIELDS},
            )
        await self.async_after_change()

    @property
    def goal(self) -> dict[str, Any]:
        """The water-savings goal: `goalPercentage` and `isEnableWaterSave` (§6.4)."""
        goal = self.record.get("waterSavingGoal")
        return goal if isinstance(goal, dict) else {}

    async def async_set_goal(
        self, *, percent: int | None = None, enabled: bool | None = None
    ) -> None:
        """Change the water-savings goal, kept on the account's record of this shower.

        That record also names the shower, so it is read fresh first rather than taken
        from setup: a stale one would undo a rename made in the app since (§3.8). As in
        the app, nothing changes while the shower runs, and the target only while the
        goal is on.
        """
        if self.activity is not None:
            raise self._refuse("settings_while_running")
        async with self.write_lock:
            with self.account.command_errors(EVOCYCLE_OFFLINE):
                customer = await self.client.async_get_customer()
            found = customer.record(self.device_id)
            if found is None or not found[1].get("logicalName"):
                raise self._refuse("settings_unreadable")
            home_id, record = found
            goal = record.get("waterSavingGoal")
            goal = goal if isinstance(goal, dict) else {}
            # What provisioning writes for a new shower, where nothing has been set (§3.8).
            stored_percent = _whole(goal.get("goalPercentage"))
            stored_enabled = goal.get("isEnableWaterSave") is True
            if enabled is None and not stored_enabled:
                # A target while the goal is off: the app offers none to change.
                return
            on = enabled if enabled is not None else stored_enabled
            body = goal_request(
                record,
                percent=percent if percent is not None else (stored_percent or 50),
                enabled=on,
            )
            with self.account.command_errors(EVOCYCLE_OFFLINE):
                await self.client.async_update_account_device(home_id, body)
            # An account write: nothing on the shower to read again.
            self.record = {
                **record,
                "waterSavingGoal": body["waterSavingGoal"],
                "showWaterSavingGoalScreen": body["showWaterSavingGoalScreen"],
            }
            self._record_read_at = time.monotonic()
            self.async_update_listeners()

    @property
    def last_clean(self) -> dict[str, int | None]:
        """When each clean last ran, in epoch seconds."""
        return {
            kind: _whole(clean(self.state, kind).get("lastCleanDate"))
            for kind in CLEANS
        }


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _flag(value: Any) -> int:
    return 0 if _whole(value) == 0 else 1


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ceiling(stored: dict[str, Any]) -> int:
    """The highest default temperature the app's slider offers, in tenths of °C: the
    lower of the two maximum temperatures, or 48 °C while neither is known (§3.5)."""
    maxima = (
        _whole(stored.get(key))
        for key in ("defaultStandardMaxTemperature", "defaultCycleMaxTemperature")
    )
    return min((value for value in maxima if value), default=480)
