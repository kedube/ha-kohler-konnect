"""Anthem valve numbers: per-zone temperature and flow, and the outlet temperature settings.

Zone 1 is the primary valve word, zone 2 the secondary; zone 2 entities exist only on
models with a second zone. A zone write re-sends the complete valve command (the valve takes
no partial write) through `Valve.async_apply_valve`, which keeps the open outlets, any pause
and the zone's Flow value, so a change mid-shower applies at once.

Temperatures are shown as the Konnect app shows them: whole degrees, rounded half-up once
from the exact Celsius value. A bound taken from a limit is never rounded past it: see
`_whole_ceiling` and `_whole_floor`.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_FLOW_PERCENT,
    DOMAIN,
    UI_DEFAULT_TEMPERATURE_MIN_F,
    UI_TEMPERATURE_MAX_F,
    UI_TEMPERATURE_MIN_F,
    ZONE_TEMPERATURE_MIN_F,
)
from .coordinator import KohlerKonnectCoordinator, Valve
from .entity import KohlerValveEntity, ZoneWordEntity, zone_label
from .konnect.valve_hex import (
    FLOW_BYTE_MAX,
    FLOW_BYTE_MIN,
    FLOW_PER_PERCENT,
    flow_byte_to_percent,
    unit_to_celsius,
)
from .panel_settings import panel_entities
from .product_platforms import product_entities


def _whole_degrees(tenths: int, fahrenheit: bool) -> int:
    """Tenths of °C as the whole degree the Konnect app shows, rounded half-up.

    Integer arithmetic (°F is ``(18 * tenths + 3200) / 100``) so there is exactly one
    rounding and no float error at a half.
    """
    if fahrenheit:
        return (18 * tenths + 3200 + 50) // 100
    return (tenths + 5) // 10


def _whole_ceiling(tenths: int, temperature_unit: str) -> int:
    """The highest whole degree whose write does not exceed ``tenths`` of °C.

    Rounding a limit can land above it (47.7 °C rounds to 48, which writes 48.0), so this
    steps down until the value, converted as a write converts it, fits. 47.7 °C gives
    47 °C or 118 °F.
    """
    whole = _whole_degrees(tenths, temperature_unit.lower().startswith("f"))
    while round(unit_to_celsius(whole, temperature_unit) * 10) > tenths:
        whole -= 1
    return whole


def zone_temperature_range(
    valve: Valve, zone: int, temperature_unit: str
) -> tuple[float, float]:
    """A zone's setpoint range in the account's unit: the floor and the scald limit.

    Read live from the zone's first outlet: its ``minimumOutletTemperature``
    (`_whole_floor`) and current ``maximumOutletTemperature`` (`_whole_ceiling`). Before
    the valve reports them, 59 °F and the top of the app's Max Temperature. Shared by the
    Temperature number and the sidebar panel, which stages showers inside it.
    """
    fahrenheit = temperature_unit.lower().startswith("f")
    try:
        outlet_id = valve.model.outlet_id(zone, 1)
    except ValueError:
        # The zone is gone; the Temperature number is unavailable then.
        limits = None
    else:
        limits = valve.gcs_state.outlet_limits.get(outlet_id)

    low = None if limits is None else limits.minimum_temperature_tenths
    if low:
        floor = float(_whole_floor(low, temperature_unit))
    elif fahrenheit:
        floor = float(ZONE_TEMPERATURE_MIN_F)
    else:
        floor = float(round(unit_to_celsius(ZONE_TEMPERATURE_MIN_F, "Fahrenheit")))

    high = None if limits is None else limits.maximum_temperature_tenths
    if high:
        ceiling = max(float(_whole_ceiling(high, temperature_unit)), floor)
    elif fahrenheit:
        ceiling = float(UI_TEMPERATURE_MAX_F)
    else:
        ceiling = _celsius_ceiling(UI_TEMPERATURE_MAX_F)
    return floor, ceiling


def _whole_floor(tenths: int, temperature_unit: str) -> int:
    """The lowest whole degree whose write is not below ``tenths`` of °C.

    `_whole_ceiling`'s counterpart for a lower bound: 15.2 °C gives 16 °C, not 15.
    """
    whole = _whole_degrees(tenths, temperature_unit.lower().startswith("f"))
    while round(unit_to_celsius(whole, temperature_unit) * 10) < tenths:
        whole += 1
    return whole


def _celsius_ceiling(fahrenheit: float) -> float:
    """A Fahrenheit top from the app's ranges as the highest whole °C within it.

    118 °F is 47.7 °C, so this gives 47. Also used by `services._temperature_bounds`.
    """
    tenths = round(unit_to_celsius(fahrenheit, "Fahrenheit") * 10)
    return float(_whole_ceiling(tenths, "Celsius"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a temperature and a flow number for each zone the valve has."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Controllers have no live temperature or flow control, only favorites.
    entities: list[NumberEntity] = []
    for valve in coordinator.valves:
        entities.extend(panel_entities(coordinator, valve, "number"))
        for zone in valve.model.zones:
            entities.append(ZoneTemperatureNumber(coordinator, valve, zone))
            entities.append(ZoneFlowNumber(coordinator, valve, zone))
        # Per valve, not per zone: the setting is written to every outlet's record, and
        # the Konnect app offers one setting for the whole valve.
        entities.append(OutletMaxTemperatureNumber(coordinator, valve))
        entities.append(OutletDefaultTemperatureNumber(coordinator, valve))
    # Every other product's, each device with its own coordinator.
    entities += product_entities(
        hass, entry, coordinator, Platform.NUMBER, async_add_entities
    )
    async_add_entities(entities)


class ZoneNumberBase(ZoneWordEntity, NumberEntity):
    """Shared plumbing for the per-zone numbers."""

    # As in the app. The range is at most 58-118 °F, narrow enough to drag.
    _attr_mode = NumberMode.SLIDER

    @property
    def available(self) -> bool:
        """Unavailable once the valve's reported layout no longer has this zone.

        `Valve._apply_topology` can shrink the model before the entry reloads; the entity
        must not raise then, since a raising listener stops the ones after it.
        """
        return super().available and self._zone in self._valve.model.zones


class ZoneTemperatureNumber(ZoneNumberBase):
    """Temperature setpoint for one zone, in whole degrees of the account's unit.

    The range is the app's zone slider (`docs/protocol/gcs_valve.md` §4), read live from
    the zone's first outlet: the floor is its ``minimumOutletTemperature``, the top its
    current ``maximumOutletTemperature`` (scald limit), and one step below the floor is
    **Cold**, which sends 0 °C, "full cold" (§3.1). Before the valve reports its limits,
    59-118 °F.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_step = 1

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone)
        self._attr_name = zone_label(valve, zone, "Temperature")
        self._attr_unique_id = f"{self._device_id}_temperature_zone_{zone}"
        unit = coordinator.temperature_unit
        self._fahrenheit = unit.lower().startswith("f")
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.FAHRENHEIT
            if self._fahrenheit
            else UnitOfTemperature.CELSIUS
        )

    def _display(self, celsius: float) -> float:
        """A Celsius value in the account's unit, as the whole number the app shows."""
        return float(_whole_degrees(round(celsius * 10), self._fahrenheit))

    @property
    def _floor(self) -> float:
        """The lowest real setpoint; see `zone_temperature_range`."""
        return zone_temperature_range(
            self._valve, self._zone, self.coordinator.temperature_unit
        )[0]

    @property
    def native_min_value(self) -> float:
        """The Cold stop: one step below the floor, like the app's ``coldTemperature``."""
        return self._floor - 1

    @property
    def native_max_value(self) -> float:
        """The scald limit; see `zone_temperature_range`."""
        return zone_temperature_range(
            self._valve, self._zone, self.coordinator.temperature_unit
        )[1]

    @property
    def native_value(self) -> float | None:
        """The valve's setpoint, clamped to the slider: below the floor reads as Cold.

        The unclamped value is in the `reported_temperature` attribute.
        """
        word = self._word
        if word is None:
            return None
        value = self._display(word.temperature_celsius)
        if value < self._floor:
            return self.native_min_value
        return min(value, self.native_max_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The valve's real setpoint, whether Cold is engaged, and whether it is clamped."""
        word = self._word
        if word is None:
            return {}
        reported = self._display(word.temperature_celsius)
        return {
            "reported_temperature": reported,
            "cold": reported < self._floor,
            "out_of_range": reported > self.native_max_value,
        }

    async def async_set_native_value(self, value: float) -> None:
        key = "zone1_temperature" if self._zone == 1 else "zone2_temperature"
        if value < self._floor:
            # The Cold stop: 0 °C. `unit_to_celsius` maps 32 °F to 0 by arithmetic, as it
            # is outside the 59-122 °F table.
            value = 32.0 if self._fahrenheit else 0.0
        await self._valve.async_apply_valve(**{key: value})


class ZoneFlowNumber(ZoneNumberBase):
    """Flow setpoint for one zone, as a percentage of the zone's own maximum flow byte.

    Bounded by the min/max flow bytes of the zone's first outlet (`zone_flow_limits`),
    else the protocol range 8-100 %.

    While water runs it shows the zone's live flow byte. Otherwise it shows the percent
    last written from Home Assistant (`Valve.zone_flow`, default `DEFAULT_FLOW_PERCENT`),
    never the idle byte, which is not the flow setting (`docs/protocol/gcs_valve.md`
    §3.4). Every zone write that names no flow sends that remembered percent, so what is
    shown is what is commanded. It is stored on the zone's own scale and converted when
    a word is built (`Valve._wire_flow`).

    ⚠️ A first-generation Anthem touchscreen can rewrite both zones' flow when its flow
    panel opens (`docs/user_guide.md`, Known limitations); owners can disable the entity.
    """

    _attr_icon = "mdi:water-percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    # Whole percents, though the wire resolves 0.5 %: half steps add slider travel and no
    # difference anyone can feel. A half-percent reading is rounded for display.
    _attr_native_step = 1

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone)
        self._attr_name = zone_label(valve, zone, "Flow")
        self._attr_unique_id = f"{self._device_id}_flow_zone_{zone}"
        # Stored on the valve, not the entity: every valve write (outlet toggle,
        # temperature change) re-sends this zone's flow.
        self._valve.zone_flow.setdefault(zone, DEFAULT_FLOW_PERCENT)

    def _flow_limits(self) -> tuple[int, int] | None:
        """This zone's flow bounds in bytes, or None with no state or no such zone.

        Checks the zone itself: `zone_flow_limits` returns the default 16/200 for a zone
        the layout has lost.
        """
        state = self._state
        if state is None or self._zone not in self._valve.model.zones:
            return None
        try:
            return state.zone_flow_limits(self._zone)
        except ValueError:
            return None

    @property
    def _flow_is_live(self) -> bool:
        """Whether this zone's own word has outlets open and no pause."""
        word = self._word
        return bool(word is not None and word.outlet_mask and not word.paused)

    def _reported_percent(self, high: int) -> float | None:
        """This zone's flow byte as a percent of its own ceiling, unrounded."""
        word = self._word
        if word is None:
            return None
        # `word.flow_percent` is decoded against a 200 ceiling; recover the raw byte
        # exactly and re-scale it against this zone's own ceiling.
        byte = round(word.flow_percent * FLOW_PER_PERCENT)
        return flow_byte_to_percent(byte, high)

    @property
    def native_min_value(self) -> float:
        """The valve's minimum for this zone, read live: limits can arrive after setup."""
        limits = self._flow_limits()
        if limits is None:
            return FLOW_BYTE_MIN / FLOW_PER_PERCENT
        low, high = limits
        # Ceiling: a half-percent bound would put every whole step on a .5, and the bound
        # must not sit below what the valve accepts.
        return math.ceil(flow_byte_to_percent(low, high))

    @property
    def native_max_value(self) -> float:
        limits = self._flow_limits()
        if limits is None:
            return FLOW_BYTE_MAX / FLOW_PER_PERCENT
        _, high = limits
        # Always 100 %; computed like the minimum for symmetry.
        return math.floor(flow_byte_to_percent(high, high))

    @property
    def native_value(self) -> float | None:
        """The zone's live flow while its water runs; otherwise the remembered setting."""
        limits = self._flow_limits()
        if limits is not None and self._flow_is_live:
            reported = self._reported_percent(limits[1])
            if reported is not None:
                # Rounded to the step so the slider can show it.
                return round(reported)
        return self._valve.zone_flow.get(self._zone, DEFAULT_FLOW_PERCENT)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Whether the value is live, the raw reading, and the valve's bounds for this zone.

        Bounds are published so a slider pinned by a valve with flow control disabled can
        say why.
        """
        limits = self._flow_limits()
        if limits is None:
            return {}
        low, high = limits
        return {
            # Raw byte the percent is relative to; "50 %" on two valves need not be the
            # same flow.
            "maximum_flow_byte": high,
            # From this zone's own word (`GcsState.flow_is_live` reads zone 1 only).
            "flow_is_live": self._flow_is_live,
            # What the valve holds now, live or idle, unrounded.
            "reported_flow_percent": self._reported_percent(high),
            "minimum_percent": flow_byte_to_percent(low, high),
            "maximum_percent": flow_byte_to_percent(high, high),
            # False when min == max: flow control is off at the fixture.
            "flow_control_available": low != high,
        }

    async def async_set_native_value(self, value: float) -> None:
        key = "zone1_flow" if self._zone == 1 else "zone2_flow"
        # On success the valve remembers it in `zone_flow`.
        await self._valve.async_apply_valve(**{key: float(value)})
        self.async_write_ha_state()


class _OutletTemperatureNumber(KohlerValveEntity, NumberEntity):
    """Shared plumbing for the two writable outlet temperatures (config entities).

    Both are fields of every outlet's record, written and verified by
    `Valve.async_write_outlet_setting`.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        unit = coordinator.temperature_unit
        self._fahrenheit = unit.lower().startswith("f")
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.FAHRENHEIT
            if self._fahrenheit
            else UnitOfTemperature.CELSIUS
        )

    def _display(self, tenths: int | None) -> float | None:
        """Tenths of °C from the valve, in the account's unit, as the app shows them."""
        if tenths is None:
            return None
        return _whole_degrees(tenths, self._fahrenheit)

    def _ceiling(self, tenths: int | None) -> float | None:
        """A limit as the highest whole value that stays within it — see `_whole_ceiling`."""
        if tenths is None:
            return None
        return _whole_ceiling(tenths, self.coordinator.temperature_unit)

    def _tenths(self, value: float) -> int:
        """The account's unit back to the tenths of °C the wire wants."""
        celsius = unit_to_celsius(float(value), self.coordinator.temperature_unit)
        return round(celsius * 10)

    @property
    def _limits(self):
        """The lowest-numbered outlet's limits; all outlets carry the same settings."""
        limits = self._state.outlet_limits if self._state else {}
        return limits[min(limits)] if limits else None

    @property
    def available(self) -> bool:
        return super().available and self._limits is not None


class OutletMaxTemperatureNumber(_OutletTemperatureNumber):
    """The scald limit, the Konnect app's "Max Temperature" (92-118 °F). 🚨 Safety setting.

    The write is read back, and a failure (including a partial write across outlets)
    raises. A limit below the current Default Temperature is refused before sending;
    `Valve.async_write_outlet_setting` also checks it across every outlet.
    """

    _attr_name = "Max Temperature"
    _attr_icon = "mdi:thermometer-alert"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_max_temperature_setting"
        if self._fahrenheit:
            low, high = float(UI_TEMPERATURE_MIN_F), float(UI_TEMPERATURE_MAX_F)
        else:
            low = float(round(unit_to_celsius(UI_TEMPERATURE_MIN_F, "Fahrenheit")))
            # 47, never above the app's 118 °F top.
            high = _celsius_ceiling(UI_TEMPERATURE_MAX_F)
        self._attr_native_min_value = low
        self._attr_native_max_value = high

    @property
    def native_value(self) -> float | None:
        limits = self._limits
        return (
            None if limits is None else self._display(limits.maximum_temperature_tenths)
        )

    async def async_set_native_value(self, value: float) -> None:
        tenths = self._tenths(value)
        limits = self._limits
        default = None if limits is None else limits.default_temperature_tenths
        if default is not None and tenths < default:
            unit = self.native_unit_of_measurement
            raise HomeAssistantError(
                f"{value:.0f} {unit} is below this valve's Default Temperature of "
                f"{self._display(default):.0f} {unit}, so nothing was written. A shower "
                "cannot start hotter than the scald limit — lower Default Temperature "
                "first, or pick a higher maximum."
            )
        await self._valve.async_write_outlet_setting(maximum_temperature_tenths=tenths)
        self.async_write_ha_state()


class OutletDefaultTemperatureNumber(_OutletTemperatureNumber):
    """Where a shower starts when nothing else says: the app's "Default Temperature".

    The slider has a fixed top of 118 °F rather than tracking Max Temperature, because
    Home Assistant caches bounds and a moving range shows stale. A value above the current
    scald limit is refused instead; `GcsDevice.async_write_outlet_config` enforces the
    same rule.
    """

    _attr_name = "Default Temperature"
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_default_temperature"
        self._attr_native_max_value = (
            float(UI_TEMPERATURE_MAX_F)
            if self._fahrenheit
            else _celsius_ceiling(UI_TEMPERATURE_MAX_F)
        )
        self._attr_native_min_value = (
            float(UI_DEFAULT_TEMPERATURE_MIN_F)
            if self._fahrenheit
            else float(
                round(unit_to_celsius(UI_DEFAULT_TEMPERATURE_MIN_F, "Fahrenheit"))
            )
        )

    @property
    def native_value(self) -> float | None:
        limits = self._limits
        return (
            None if limits is None else self._display(limits.default_temperature_tenths)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The highest value this entity will accept (`_whole_ceiling` of the limit)."""
        limits = self._limits
        return {
            "scald_limit": None
            if limits is None
            else self._ceiling(limits.maximum_temperature_tenths)
        }

    async def async_set_native_value(self, value: float) -> None:
        # Checked here too so the message uses this entity's units and setting names.
        # ⚠️ Compare in tenths, as written: whole display degrees round 47.7 up to 48.
        tenths = self._tenths(value)
        limits = self._limits
        maximum = None if limits is None else limits.maximum_temperature_tenths
        if maximum is not None and tenths > maximum:
            unit = self.native_unit_of_measurement
            raise HomeAssistantError(
                f"{value:.0f} {unit} is above this valve's Max Temperature of "
                f"{self._ceiling(maximum):.0f} {unit}. A shower cannot start hotter than "
                "the scald limit — raise Max Temperature first, or pick a lower default."
            )
        await self._valve.async_write_outlet_setting(default_temperature_tenths=tenths)
        self.async_write_ha_state()
