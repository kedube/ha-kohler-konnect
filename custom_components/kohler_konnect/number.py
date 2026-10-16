"""Per-zone temperature for the Anthem valve.

One entity per zone, because the valve carries an independent temperature byte for each. A
zone maps to a valve: zone 1 is the primary, zone 2 the secondary. Zone 2 entities only
exist on models that have a second valve.

Setting the value re-sends the complete valve command — the valve accepts no partial write —
preserving whichever outlets are currently open, a pause the zone is holding, and the flow
the zone's Flow number holds (Home Assistant's own, never the valve's idle byte). That
mirrors the Konnect app, which POSTs a fresh command on every adjustment, and it means
changing the temperature mid-shower takes effect immediately rather than at the next start.

**Whole degrees are the Konnect app's**: rounded half-up from the exact Celsius value, as
the app's ``round(c * 1.8 + 32)`` does. Rounding twice — to 0.1 °F in
`valve_hex.celsius_to_unit`, then to whole degrees with Python's half-to-even — showed
38.6 °C as 102 °F where the app shows 101. And a **ceiling** is never rounded up: the
highest whole value offered is one whose write does not exceed the limit, so a 47.7 °C scald
limit offers 47 °C, not a 48 that would write 48.0.

**Flow is a per-zone control here, restored 2026-09-10.** It was removed on 2026-08-13
because a first-gen Anthem touchscreen was observed rewriting both zones' flow the instant
its flow panel was opened, making a Home Assistant setpoint impossible to rely on. That
finding stands — the capture is in ``docs/protocol/gcs_valve.md`` — but the conclusion drawn from it
was too broad: it came from **one** install, and it was applied to every valve
unconditionally, so owners whose valve honours a written flow byte had no control either.

Restored as a valve entity because flow is the valve's own capability: the codec encodes
and decodes byte 2 in full, ``async_apply_valve`` has always accepted ``zone1_flow`` /
``zone2_flow``, and the valve honours what it is given within its calibrated range. On an
install whose panel does fight it, the entity can be disabled; that is a better failure than
withholding the control from everyone.

The bounds are the valve's **own** reported limits, not a constant — ``zone_flow_limits``
reads the per-outlet minimum and maximum the hardware announces, so a valve with flow
control disabled reports a narrow range rather than being offered one it will not honour.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
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
from .faucet.number import faucet_numbers
from .konnect.valve_hex import (
    FLOW_BYTE_MAX,
    FLOW_BYTE_MIN,
    FLOW_PER_PERCENT,
    flow_byte_to_percent,
    unit_to_celsius,
)


def _whole_degrees(tenths: int, fahrenheit: bool) -> int:
    """Tenths of °C as the whole degree the Konnect app shows, rounded half-up.

    Integer arithmetic on the exact value — °F is ``(18 * tenths + 3200) / 100`` — so there
    is one rounding, the app's, and no float can land a hair either side of a half.
    """
    if fahrenheit:
        return (18 * tenths + 3200 + 50) // 100
    return (tenths + 5) // 10


def _whole_ceiling(tenths: int, temperature_unit: str) -> int:
    """The highest whole degree whose write does not exceed ``tenths`` of °C.

    What a slider bounded by a limit may offer. Rounding the limit can land above it — a
    47.7 °C scald limit rounds to 48 °C, which writes 48.0 — so this steps down until the
    value, converted the way a write converts it, fits. In °F that is Kohler's own table,
    which already sits at or below the arithmetic, so 47.7 °C still offers 118 °F.
    """
    whole = _whole_degrees(tenths, temperature_unit.lower().startswith("f"))
    while round(unit_to_celsius(whole, temperature_unit) * 10) > tenths:
        whole -= 1
    return whole


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a temperature and a flow number for each zone the valve has."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    # The controller offers no live temperature or flow control — only favorites — so a
    # controller-only account gets nothing here. One set per valve otherwise, each with
    # the zones its own layout has.
    entities: list[NumberEntity] = []
    for valve in coordinator.valves:
        for zone in valve.model.zones:
            entities.append(ZoneTemperatureNumber(coordinator, valve, zone))
            entities.append(ZoneFlowNumber(coordinator, valve, zone))
        # Per valve, not per zone: `writeoutletconfig` replaces every outlet's record with
        # the same value, and the Konnect app offers one setting for the whole valve.
        entities.append(OutletMaxTemperatureNumber(coordinator, valve))
        entities.append(OutletDefaultTemperatureNumber(coordinator, valve))
    # Each faucet is its own device, with its own coordinator.
    for faucet in coordinator.faucets:
        entities += faucet_numbers(faucet)
    async_add_entities(entities)


class ZoneNumberBase(ZoneWordEntity, NumberEntity):
    """Shared plumbing for the per-zone numbers.

    `__init__` and `_word` come from `ZoneWordEntity`.
    """

    # SLIDER rather than BOX, as the app's own control is a slider. The zone range is
    # 58-118 °F at most (Cold, then 59 up to the valve's maximum), narrow enough to drag.
    _attr_mode = NumberMode.SLIDER

    @property
    def available(self) -> bool:
        """Unavailable once the valve's reported layout no longer has this zone.

        `Valve._apply_topology` can shrink the model after setup; until the entry reloads,
        an entity for a zone that is gone must say so rather than raise — one listener that
        raises stops every listener after it.
        """
        return super().available and self._zone in self._valve.model.zones


class ZoneTemperatureNumber(ZoneNumberBase):
    """Temperature setpoint for one zone — **the Konnect app's slider, Cold stop included.**

    Presented in the account's unit as a whole number, with 0.1 °C resolution underneath, so
    a whole degree Fahrenheit is always representable.

    **The range is the app's** (owner's decision, 2026-10-07, from Konnect 3.0.6
    ``qa0/p.java`` / ``db0/c.java`` ``n0()``):

    * the top is the valve's **current** ``maximumOutletTemperature`` — the scald limit the
      Max Temperature setting writes, read live, so raising it in the app widens this;
    * the floor is the outlet's ``minimumOutletTemperature`` — 59 °F / 15 °C on every
      captured outlet;
    * **one step below the floor is Cold.** Choosing it sends 0 °C, "full cold": the valve
      stops mixing hot and delivers whatever the supply provides. It will not produce
      freezing water; on the system captured, the cold supply bottomed out near 60 °F while
      the setpoint read 32 °F. The app labels the same stop ``COLD``.

    Until 2026-10-07 this was a fixed 92-118 °F, justified as matching the app's slider —
    but that is the Max Temperature *setting's* range, not this control's.

    Both ends come from the zone's first outlet, matching the app, which bounds the slider
    with ``outletConfigurations[0]`` of each valve; until the valve has announced one, the
    floor is 59 °F and the top 118 °F, the highest the app lets Max Temperature go.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_step = 1

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone)
        # `Temperature`, or disambiguated by the zone grouping on a two-zone valve —
        # `Temperature 2`, or `Temperature (Showerhead, Body Sprays)`. See `zone_label`.
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

    def _zone_limits(self):
        state = self._valve.gcs_state
        try:
            outlet_id = self._valve.model.outlet_id(self._zone, 1)
        except ValueError:
            # The zone has no outlets any more — see `available`.
            return None
        return state.outlet_limits.get(outlet_id)

    @property
    def _floor(self) -> float:
        """The lowest real setpoint — the outlet's minimum, else 59 °F / 15 °C."""
        limits = self._zone_limits()
        tenths = None if limits is None else limits.minimum_temperature_tenths
        if tenths:
            return float(_whole_degrees(tenths, self._fahrenheit))
        if self._fahrenheit:
            return float(ZONE_TEMPERATURE_MIN_F)
        return float(round(unit_to_celsius(ZONE_TEMPERATURE_MIN_F, "Fahrenheit")))

    @property
    def native_min_value(self) -> float:
        """The Cold stop: one step below the floor, as the app's ``coldTemperature`` is."""
        return self._floor - 1

    @property
    def native_max_value(self) -> float:
        """The valve's current scald limit, else the top of the app's Max Temperature range.

        The limit's whole-degree **ceiling that does not exceed it** (`_whole_ceiling`),
        never the nearest whole degree: a 47.7 °C limit offered 48 °C until 2026-10-16.
        """
        limits = self._zone_limits()
        tenths = None if limits is None else limits.maximum_temperature_tenths
        if tenths:
            ceiling = _whole_ceiling(tenths, self.coordinator.temperature_unit)
            return max(float(ceiling), self._floor)
        if self._fahrenheit:
            return float(UI_TEMPERATURE_MAX_F)
        return float(round(unit_to_celsius(UI_TEMPERATURE_MAX_F, "Fahrenheit")))

    @property
    def native_value(self) -> float | None:
        """The valve's setpoint — the Cold stop for anything below the floor.

        A setpoint under the floor is full cold in all but name (0 °C is what the app's Cold
        stop writes), and reporting it raw would leave the slider with no position to
        render. Above the top it is clamped for the same reason. The unclamped reading is
        published as `reported_temperature`, and `cold` says when the stop is engaged.
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
            # The Cold stop. 0 °C in the account's unit — 32 °F, which `unit_to_celsius`
            # maps to 0 by arithmetic, as it is outside the app's 59-122 °F table.
            value = 32.0 if self._fahrenheit else 0.0
        await self._valve.async_apply_valve(**{key: value})


class ZoneFlowNumber(ZoneNumberBase):
    """Flow setpoint for one zone, as a percentage.

    Writes byte 2 of that zone's valve word through the same ``async_apply_valve`` path the
    temperature uses, so the outlets currently open are preserved and a change mid-shower
    takes effect immediately.

    **The range is the valve's own.** ``zone_flow_limits`` returns the minimum and maximum
    flow bytes this zone's first outlet reports — the same pair the Konnect app bounds its
    slider with — falling back to the protocol limits (8-100 %) only when the valve has not
    announced them yet. A valve with flow control disabled therefore offers the narrow range
    it will actually honour rather than a full sweep it will ignore.

    > ⚠️ **A first-gen Anthem touchscreen may overwrite this.** Opening that panel's flow
    > control was captured rewriting *both* zones before any adjustment was made, applying
    > its own linked scaling and a calibration-derived ceiling. On such an install a
    > setpoint written here can change on its own, which is why this entity was withdrawn
    > between 2026-08-13 and 2026-09-10. If yours behaves that way, disable this entity —
    > the protocol layer is unaffected either way.

    **While the shower is off, this reads the last flow Home Assistant wrote, or 100 %.**
    It deliberately does not echo the valve's idle byte, which is not the flow setting: one
    install carries transient junk there, and another carries *stable* junk — 24.5 % and
    26.5 %, unmoving across hours, while the owner's panel held 100 % on both valves. A
    number that does not move is more convincing than one that flickers, not less, so
    neither is shown as a setpoint.

    A control has to display something, so the entity remembers what was last written and
    falls back to `DEFAULT_FLOW_PERCENT` — and every write that names no flow for this zone
    (a temperature change, an outlet toggle) sends exactly that remembered value, so the
    displayed value and the commanded value agree. It is kept as the percent chosen, on
    this zone's own scale, and converted only when a word is built (`Valve._wire_flow`):
    until 2026-10-16 it was stored already converted to the 200-byte scale, so on a zone
    whose ceiling is another byte the idle entity showed a different number than chosen
    (50 % read back as 25 %). Once water is running the valve's own byte is authoritative
    and is shown directly.

    `flow_is_live` says which of the two you are looking at — **for this zone**, from this
    zone's own word. `GcsState.flow_is_live` reads zone 1 only, so zone 2's entity used to
    show its remembered value while zone 2 ran and its idle byte while only zone 1 did.
    """

    _attr_icon = "mdi:water-percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    # **Whole percents, by the owner's decision.** The wire resolves to 0.5 % — one byte is
    # half a percent — and 0.7.4 briefly exposed that. It was reverted here because a control
    # is for choosing a flow, not for mirroring the device's internal precision: half-percent
    # steps double the travel needed to cross the range and offer a distinction nobody can
    # feel in a shower.
    #
    # The cost is deliberate and bounded. Where the valve reports a half value — both of the
    # owner's valves do, 24.5 % and 26.5 % — `native_value` rounds it for display, so the
    # entity reads 25 % and 27 % while the valve holds the half. Adjusting the slider then
    # writes the whole number, moving the real flow by at most 0.5 %: below the resolution of
    # anything a person notices, and it only happens when the control is actually used.
    #
    # Every whole percent is exactly representable (percent * 2 is always an integer byte),
    # so nothing is lost on the write path.
    _attr_native_step = 1

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, valve: Valve, zone: int
    ) -> None:
        super().__init__(coordinator, valve, zone)
        self._attr_name = zone_label(valve, zone, "Flow")
        self._attr_unique_id = f"{self._device_id}_flow_zone_{zone}"
        # The chosen flow lives on the valve rather than on this entity, because every
        # write needs it: the valve takes no partial write, so an outlet toggle or a
        # temperature change rewrites this zone's flow too, and without a shared value it
        # would reset the flow this entity had set. Seeded with `DEFAULT_FLOW_PERCENT`.
        self._valve.zone_flow.setdefault(zone, DEFAULT_FLOW_PERCENT)

    def _flow_limits(self) -> tuple[int, int] | None:
        """This zone's flow bounds in bytes, or None with no state or no such zone.

        The zone is checked here rather than left to `zone_flow_limits`, which answers the
        default 16/200 bounds for a zone the layout has lost (it raised until 2026-10-16).
        Bounds for a zone that is gone are not this entity's to publish — see
        `ZoneNumberBase.available`.
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
        # Re-derived from the raw byte against **this zone's own ceiling** rather than read
        # off `word.flow_percent`, which `decode_word` computes with the 200 default.
        # Identical wherever the ceiling is 200; correct where it is not. See
        # `flow_byte_to_percent`. `word.flow_percent` was decoded against the 200 default,
        # so multiplying it back recovers the raw byte exactly — `decode_word` does
        # `byte * 100 / 200`, and this undoes precisely that. Cheaper and less invasive
        # than threading per-valve limits through the decoder, which has 28 call sites and
        # no per-valve context.
        byte = round(word.flow_percent * FLOW_PER_PERCENT)
        return flow_byte_to_percent(byte, high)

    @property
    def native_min_value(self) -> float:
        """The valve's own minimum for this zone, read live rather than fixed at setup.

        Per-outlet limits arrive gradually over MQTT, so a bound captured in ``__init__``
        would be the fallback for as long as the valve stayed quiet.
        """
        limits = self._flow_limits()
        if limits is None:
            return FLOW_BYTE_MIN / FLOW_PER_PERCENT
        low, high = limits
        # Rounded UP, and away from the forbidden side: an odd limit byte would otherwise put
        # the bound on a half and, with a whole-number step, every position on the slider
        # would carry that .5 — defeating the point. Ceiling rather than round, so the bound
        # never sits below what the valve will accept.
        return math.ceil(flow_byte_to_percent(low, high))

    @property
    def native_max_value(self) -> float:
        limits = self._flow_limits()
        if limits is None:
            return FLOW_BYTE_MAX / FLOW_PER_PERCENT
        _, high = limits
        # The ceiling **is** 100 % by definition — percent is a ratio against it, so the
        # maximum can only be 100. Kept as arithmetic rather than a literal so the two bounds
        # visibly come from the same place.
        return math.floor(flow_byte_to_percent(high, high))

    @property
    def native_value(self) -> float | None:
        """The zone's byte while its water runs; otherwise what was last written, or 100 %.

        See the class docstring for why the idle byte is not echoed.
        """
        limits = self._flow_limits()
        if limits is not None and self._flow_is_live:
            reported = self._reported_percent(limits[1])
            if reported is not None:
                # Rounded to the step. The valve resolves finer than this control does, so
                # an unrounded value would sit between two positions the slider can occupy —
                # Home Assistant would render a number the user cannot return to.
                return round(reported)
        return self._valve.zone_flow.get(self._zone, DEFAULT_FLOW_PERCENT)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Whether water is moving, and the valve's own bounds for this zone.

        `flow_is_live` is **information, not a warning**: it says an outlet is open, which
        is worth knowing when reading history, but the value is trustworthy either way on
        hardware whose idle byte is stable. See `GcsState.flow_is_live`.

        The bounds are published too, because a valve with flow control disabled reports a
        narrow range and a slider that will not move needs to say why.
        """
        limits = self._flow_limits()
        if limits is None:
            return {}
        low, high = limits
        return {
            # The outlet's own ceiling, in raw byte units. Published because percent is a
            # ratio against it: two valves showing "50 %" are at the same fraction of their
            # own maximum, not necessarily the same flow.
            "maximum_flow_byte": high,
            # False means the value above is what Home Assistant last wrote, not a reading
            # from the valve — see the class docstring. This zone's own word decides.
            "flow_is_live": self._flow_is_live,
            # The byte this zone's valve is actually holding, whatever it means, as a
            # percent of this zone's ceiling. Published so the idle-byte behaviour stays
            # observable rather than merely asserted.
            "reported_flow_percent": self._reported_percent(high),
            "minimum_percent": flow_byte_to_percent(low, high),
            "maximum_percent": flow_byte_to_percent(high, high),
            # True where the valve reports a single-point range — flow control is off at
            # the fixture, so the slider is fixed and that is the hardware's doing.
            "flow_control_available": low != high,
        }

    async def async_set_native_value(self, value: float) -> None:
        key = "zone1_flow" if self._zone == 1 else "zone2_flow"
        # The percent as chosen, on this zone's own scale. The valve restates it for the
        # wire and, once the write is accepted, remembers it in `zone_flow`, so an idle
        # entity shows what was asked for and the next write of any kind preserves it.
        await self._valve.async_apply_valve(**{key: float(value)})
        self.async_write_ha_state()


class _OutletTemperatureNumber(KohlerValveEntity, NumberEntity):
    """Shared plumbing for the two writable outlet temperatures.

    Both live in `outletConfigurations` and both are written by `writeoutletconfig`, which
    replaces an outlet's **whole record** — so the coordinator does the write, one call per
    outlet, and reads the value back before reporting success. See
    `Valve.async_write_outlet_setting`.

    Configuration entities rather than diagnostics: these are settings the Konnect app
    offers, and a read-only copy of a setting someone can change is a worse answer than
    either a real control or nothing.
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
        """Any outlet's limits — they agree, and a disagreement is a fault, not a setting."""
        limits = self._state.outlet_limits if self._state else {}
        return limits[min(limits)] if limits else None

    @property
    def available(self) -> bool:
        return super().available and self._limits is not None


class OutletMaxTemperatureNumber(_OutletTemperatureNumber):
    """The scald limit — the Konnect app's "Max Temperature". 🚨 **A safety setting.**

    Writable because it is writable in the app and on the panel, and a Home Assistant entity
    that could only watch it change was the odd one out. The range matches the app's:
    92-118 °F.

    The write is verified. `writeoutletconfig` returns a 201 that means *accepted for
    delivery* and carries no echo of the value; the app performs no read-back at all. This
    entity reports failure rather than optimism — including the partial-write case, where
    some outlets took the new limit and others did not.

    A limit below the current Default Temperature is refused before anything is sent, in
    this entity's own units — the valve refuses the combination, and the coordinator checks
    it across every outlet too (`Valve.async_write_outlet_setting`).
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
            high = float(round(unit_to_celsius(UI_TEMPERATURE_MAX_F, "Fahrenheit")))
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
    """Where a shower starts when nothing else says — the app's "Default Temperature".

    **The slider goes to 118 °F; the scald limit is enforced on the way in.** An earlier
    version moved `native_max_value` with `Max Temperature`, which was more faithful to the
    app and worse to use: Home Assistant caches an entity's bounds, so the slider's range
    changed shape underneath whoever was looking at it and could show stale limits until the
    next update. Reported by the owner 2026-09-11 as "takes a long time to update and appears
    unresponsive".

    A fixed range with a clear refusal is the more predictable trade. Setting a default above
    the current scald limit fails with a message naming both numbers, rather than the slider
    silently having a different maximum than it had a moment ago. The valve's own guard
    (`GcsDevice.async_write_outlet_config`) refuses the same combination independently, so
    the rule holds even if something reaches past this entity.
    """

    _attr_name = "Default Temperature"
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_default_temperature"
        self._attr_native_max_value = (
            float(UI_TEMPERATURE_MAX_F)
            if self._fahrenheit
            else float(round(unit_to_celsius(UI_TEMPERATURE_MAX_F, "Fahrenheit")))
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
        """The ceiling this entity will actually accept, which the slider cannot show.

        The highest whole value within the limit, not the nearest: a 47.7 °C limit accepts
        47 °C, and showing 48 would name a value that is refused.
        """
        limits = self._limits
        return {
            "scald_limit": None
            if limits is None
            else self._ceiling(limits.maximum_temperature_tenths)
        }

    async def async_set_native_value(self, value: float) -> None:
        # Checked here as well as in the client so the message names this entity's own
        # units and the setting the user would have to change — `Max Temperature`, not
        # `maximumOutletTemperature`. **Compared in tenths**, as written: comparing whole
        # display degrees let 48 °C through under a 47.7 °C limit, because 47.7 displays
        # as 48.
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
