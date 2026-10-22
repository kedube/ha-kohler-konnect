"""Selects for the Anthem valve and the Anthem Plus controller.

Favorite and experience dropdowns both start a stored scene and show which one is
running, from what the device reports (``presetOrExperienceId`` on the valve), so a
scene started from the app or the touchscreen shows here too. Also the valve's warmup
mode and Max Shower Duration.

"Favorite" is the app's word and the entity's; "preset" is the protocol's, used below
the entity layer. The controller's favorites are a separate list on its own device.

⚠️ Unique ids and attributes keep the `favourite` spelling (`*_favourite`,
`favourite_count`, `no_favourites_reason`); renaming them would orphan automations and
history.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    OPTIMISTIC_GRACE_SECONDS,
    OUTLET_RUN_TIME_APP_SAFE_MAX_SECONDS,
    OUTLET_RUN_TIME_CHOICES_SECONDS,
    PRESET_HIDDEN_IDS,
    WARMUP_LABELS,
    ZONE_GROUPING_OUTLET_LABELS,
    ZONE_GROUPING_SUBDEVICES,
)
from .coordinator import Controller, KohlerKonnectCoordinator, Valve
from .entity import KohlerControllerEntity, KohlerValveEntity, outlet_name
from .faucet.select import add_faucet_selects
from .konnect import WARMUP_MODES_CURRENT

# Shown when nothing is running. A select's current option must be in its options, or
# Home Assistant logs an error on every update.
OPTION_OFF = "Off"


class OptimisticOptionMixin:
    """Hold a just-chosen option until the device confirms it, or the grace runs out.

    The select counterpart of `switch.OptimisticSwitchMixin`, which states the rule:
    clear only when the device reports the chosen option or `OPTIMISTIC_GRACE_SECONDS`
    expires, never on an unrelated update. The grace matters here too: these dropdowns
    can start water, and a write the device ignored must not leave them claiming it
    worked. A subclass supplies `_device_option`; this supplies `current_option`.
    """

    _optimistic: str | None = None
    _optimistic_cancel = None

    @property
    def _device_option(self) -> str | None:
        """What the device itself says, ignoring anything chosen but not yet confirmed."""
        raise NotImplementedError

    @property
    def current_option(self) -> str | None:
        if self._optimistic is not None:
            return self._optimistic
        return self._device_option

    def _pending_option(self) -> str | None:
        """An option chosen and still awaiting the device, other than ``Off``."""
        return None if self._optimistic in (None, OPTION_OFF) else self._optimistic

    def _set_optimistic(self, option: str) -> None:
        self._cancel_optimistic_timer()
        self._optimistic = option
        self.async_write_ha_state()

    def _clear_optimistic(self) -> None:
        self._cancel_optimistic_timer()
        if self._optimistic is None:
            return
        self._optimistic = None
        self.async_write_ha_state()

    def _cancel_optimistic_timer(self) -> None:
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
            self._optimistic_cancel = None

    def _arm_optimistic_expiry(self) -> None:
        """Give up on the guess after the grace, whatever the device has or has not said."""
        self._cancel_optimistic_timer()

        @callback
        def _expire(_now) -> None:
            self._optimistic_cancel = None
            self._clear_optimistic()

        self._optimistic_cancel = async_call_later(
            self.hass, OPTIMISTIC_GRACE_SECONDS, _expire
        )

    async def _async_command(self, option: str, action: Awaitable[Any]) -> None:
        """Show ``option`` at once, send ``action``, and hold it until the device agrees.

        Any exit but success, cancellation included, clears the guess; see
        `switch.OptimisticSwitchMixin._async_switch`.
        """
        self._set_optimistic(option)
        try:
            await action
        except BaseException:
            # Failed or cancelled: show the device's own report again.
            self._clear_optimistic()
            raise
        # Accepted; the device's confirmation follows a second or two later.
        self._arm_optimistic_expiry()

    @callback
    def _handle_coordinator_update(self) -> None:
        # The device now reports what was asked for, so the guess is no longer needed.
        if self._optimistic is not None and self._device_option == self._optimistic:
            self._cancel_optimistic_timer()
            self._optimistic = None
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_optimistic_timer()
        await super().async_will_remove_from_hass()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the favorite selector when the account has a valve."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for valve in coordinator.valves:
        entities.append(FavoriteSelect(coordinator, valve))
        entities.append(ValveExperienceSelect(coordinator, valve))
        entities.append(ValveWarmupSelect(coordinator, valve))
        entities.append(OutletRunTimeSelect(coordinator, valve))
    # Each controller keeps its own favorites, on a different command surface from the
    # valve's, so each gets its own dropdowns on its own device.
    for controller in coordinator.controllers:
        entities.append(HubFavoriteSelect(coordinator, controller))
        entities.append(HubExperienceSelect(coordinator, controller))
    async_add_entities(entities)
    # A faucet's Preset select appears once it has a preset, which may be later.
    for faucet in coordinator.faucets:
        add_faucet_selects(entry, faucet, async_add_entities)


class FavoriteSelect(OptimisticOptionMixin, KohlerValveEntity, SelectEntity):
    """Start a stored favorite, and show which one is running."""

    _attr_icon = "mdi:playlist-play"
    _attr_name = "Favorite"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_favourite"

    @property
    def _presets(self):
        state = self._state
        if state is None:
            return []
        return state.selectable_presets(hidden=PRESET_HIDDEN_IDS)

    @property
    def options(self) -> list[str]:
        """``Off`` plus every selectable favorite, lowest slot first.

        Rebuilt on every read, so a favorite added, renamed or deleted in the app
        (pushed over MQTT) shows without a reload.
        """
        return [OPTION_OFF] + [preset.name for preset in self._presets]

    @property
    def _device_option(self) -> str | None:
        """The running favorite, or ``Off``.

        ``presetOrExperienceId`` is cleared by pause and stop and never set by a direct
        outlet command: this says what started the session, not whether water runs.
        """
        state = self._state
        if state is None:
            return None
        active = state.active_preset_id
        if active is None:
            return OPTION_OFF
        preset = state.presets.get(active)
        # An id with no option (hidden preset 1, an experience, or one that arrived
        # before the list) reads Off, since the current option must be in `options`.
        if preset is None or not preset.is_selectable:
            return OPTION_OFF
        if preset.preset_id in PRESET_HIDDEN_IDS:
            return OPTION_OFF
        return preset.name

    @property
    def _experiences(self) -> list[str]:
        """Names of the stored experiences, which the Experience select offers instead.

        They share the id space and start command with favorites but are firmware
        programs, listed separately as in the app. Named here so an automation asking
        this entity for one is pointed to the right entity.
        """
        state = self._state
        if state is None:
            return []
        return sorted(preset.name for preset in state.experiences())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        if state is None:
            return {}
        attributes: dict[str, Any] = {
            # The raw id, including hidden ones: useful when this reads Off but
            # something is running.
            "active_preset_id": state.active_preset_id,
            "favourite_count": len(self._presets),
        }
        # Named, not offered: the Experience select starts them.
        experiences = self._experiences
        if experiences:
            attributes["experiences"] = experiences
            attributes["experiences_note"] = (
                "Experiences are started from this valve's Experience select."
            )
        # An Off-only picker otherwise looks like one that failed to load.
        reason = self._empty_reason
        if reason is not None:
            attributes["no_favourites_reason"] = reason
        return attributes

    @property
    def _empty_reason(self) -> str | None:
        """Why `options` holds nothing but `Off`, or None when it holds a favorite.

        Slot 1 of the valve's ten is the default shower, hidden as in the app and run by
        the Shower switch (`PRESET_HIDDEN_IDS`), so a valve with no favorites created
        offers nothing; that is normal. Diagnostics' `selectable` counts slot 1, so it
        can read 1 while this offers nothing.
        """
        if self._presets:
            return None
        state = self._state
        if state is None:
            return None
        if self._experiences:
            return (
                "No favorites to start. This valve's stored slots are experiences, "
                "which the Experience select starts, plus the default-shower slot, which "
                "the Shower switch runs. Create a favorite in the Kohler Konnect app and "
                "it appears here."
            )
        return (
            "No favorites have been created on this valve. The default-shower slot is "
            "run by the Shower switch rather than listed here. Create a favorite in the "
            "Kohler Konnect app and it appears here — no reload needed."
        )

    async def async_select_option(self, option: str) -> None:
        """Start the named favorite, or stop the shower.

        Resolved by name at call time, never by a remembered id: slots are reused.
        ``Off`` goes through `Valve.async_stop_shower`, superseding queued commands.
        """
        if option == OPTION_OFF:
            await self._async_command(OPTION_OFF, self._valve.async_stop_shower())
            return

        state = self._state
        preset = (
            None if state is None else state.preset_by_name(option, PRESET_HIDDEN_IDS)
        )
        if preset is None:
            # Only selectable slots resolve; say so when the name is an experience.
            if option in self._experiences:
                raise HomeAssistantError(
                    f"{option!r} is an Anthem experience, not a favorite. Start it from "
                    "this valve's Experience select instead."
                )
            raise HomeAssistantError(
                f"No Anthem favorite called {option!r}. It may have been renamed or "
                "deleted in the Konnect app."
            )
        await self._async_command(
            option, self._valve.async_activate_preset(preset.preset_id)
        )


class ValveExperienceSelect(OptimisticOptionMixin, KohlerValveEntity, SelectEntity):
    """Start one of the valve's stored experiences, and show which one is running.

    Experiences are firmware programs (Wake Up, Cool Down, the ice-shower routines)
    stored beside the favorites in ``gcs-preset`` (``isExperience: "True"``, ids 17 and
    up) and started with the same ``controlpresetorexperience {preset, action}`` body,
    as Konnect 3.0.6 sends it.

    Starting is live-verified; stopping is app-confirmed only
    (`docs/protocol/gcs_valve.md` §10). If a valve ignores the command, the dropdown
    falls back to ``Off`` once the grace runs out.
    """

    _attr_icon = "mdi:creation"
    _attr_name = "Experience"

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_experience"

    @property
    def options(self) -> list[str]:
        state = self._state
        names = [] if state is None else [x.name for x in state.experiences()]
        return [OPTION_OFF, *names]

    def _running(self):
        state = self._state
        if state is None or state.active_preset_id is None:
            return None
        preset = state.presets.get(state.active_preset_id)
        return preset if preset is not None and preset.is_experience else None

    @property
    def _device_option(self) -> str | None:
        """The running experience, from ``presetOrExperienceId``, or ``Off``."""
        if self._state is None:
            return None
        running = self._running()
        return OPTION_OFF if running is None else running.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        running = self._running()
        return {"active_experience_id": None if running is None else running.preset_id}

    async def async_select_option(self, option: str) -> None:
        """Start the named experience, or stop the running one. Resolved by name."""
        if option == OPTION_OFF:
            running = self._running()
            if running is None and (pending := self._pending_option()) is not None:
                # Started a moment ago, not yet confirmed: stop that one, or it would
                # start after this Off was ignored.
                state = self._state
                running = None if state is None else state.experience_by_name(pending)
            if running is None:
                return
            await self._async_command(
                OPTION_OFF,
                self._valve.async_control_experience(running.preset_id, False),
            )
            return
        state = self._state
        experience = None if state is None else state.experience_by_name(option)
        if experience is None:
            raise HomeAssistantError(
                f"No Anthem experience called {option!r} on this valve. Add it in the "
                "Konnect app first."
            )
        await self._async_command(
            option, self._valve.async_control_experience(experience.preset_id, True)
        )


class ValveWarmupSelect(OptimisticOptionMixin, KohlerValveEntity, SelectEntity):
    """The valve's warmup mode, with ``Off`` as one of the choices.

    The mode (``warmUpState.warmUp``), not whether a warm-up runs now
    (``warmUpState.state``, shown by `System Status`); both are attributes.

    Three options, all with no start delay: the modes the app writes, under this
    integration's labels (`WARMUP_LABELS`). Which outlets ``Started Outlets`` warms is
    set on the device; no cloud API exposes it.

    ⚠️ A valve can hold one of two legacy delayed-start modes. It joins the options
    while in force, so the state is truthful, but is never offered otherwise: nothing
    defines its delay.

    ⚠️ An Anthem Plus controller's web UI sets this to Off on every signed-in use; the
    Warmup Auto-Restore switch mitigates it (`docs/protocol/gcs_valve.md` §5).

    Read at setup, on every reconnect and after every write, since a 200 from the cloud
    does not mean the valve applied it; the valve's `GCS_WARM_STS` echo, a few seconds
    after a write, corrects it too.
    """

    _attr_icon = "mdi:thermometer-water"
    _attr_name = "Warmup"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_unique_id = f"{self._device_id}_warmup"

    @property
    def _mode(self) -> str | None:
        state = self._state
        return None if state is None else state.warmup_mode

    @property
    def options(self) -> list[str]:
        """The three current modes, plus the valve's mode if it is not one of them.

        A `current_option` missing from this list logs an error on every update.
        """
        labels = [WARMUP_LABELS[mode] for mode in WARMUP_MODES_CURRENT]
        held = self._mode
        if held is not None and held not in WARMUP_MODES_CURRENT:
            labels.append(WARMUP_LABELS.get(held, held))
        return labels

    @property
    def _device_option(self) -> str | None:
        mode = self._mode
        if mode is None:
            # Never reported. `Off` would be a guess.
            return None
        return WARMUP_LABELS.get(mode, mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The raw mode string, and the axis this entity deliberately does not show."""
        state = self._state
        if state is None:
            return {}
        attributes: dict[str, Any] = {
            "warmup_mode": state.warmup_mode,
            "warmup_in_progress": state.warmup_in_progress,
        }
        # A valve can report no mode at all (no `warmUpState`, no `GCS_WARM_STS`), and
        # the cloud accepts a write to it with HTTP 200 whether or not it applies it.
        if state.warmup_mode is None:
            attributes["mode_never_reported"] = True
            attributes["note"] = (
                "This valve has not reported a warm-up mode. The cloud accepts a warm-up "
                "command with HTTP 200 whether or not the valve applies it, so a change "
                "made here may silently do nothing. Watch this entity after setting it: "
                "if the value does not stick, the valve is not honouring the command."
            )
        return attributes

    async def async_select_option(self, option: str) -> None:
        """Write the mode behind the chosen label.

        Optimistic: the valve's `GCS_WARM_STS` echo is the real confirmation; a 200 from
        the cloud only means the command was accepted.
        """
        mode = next(
            (value for value, label in WARMUP_LABELS.items() if label == option), None
        )
        if mode is None:
            raise HomeAssistantError(f"{option!r} is not a warmup mode")
        if mode not in WARMUP_MODES_CURRENT and mode != self._mode:
            raise HomeAssistantError(
                f"{option!r} is a legacy mode this integration does not write. It is listed "
                "only because the valve is currently holding it."
            )
        # `async_set_warmup` reads the mode back, so the device normally agrees by the
        # time this returns; the expiry covers a command the cloud accepts and the valve
        # ignores.
        await self._async_command(option, self._valve.async_set_warmup(mode))


class HubFavoriteSelect(OptimisticOptionMixin, KohlerControllerEntity, SelectEntity):
    """Start a stored controller favorite, and show which one is running.

    Favorites are the controller's only unit of control: it has no "set temperature and
    outlets now" command. Unlike the valve's slots, favorite ids are reassigned when one
    is deleted, so they are resolved by name at call time. Editing is refused while the
    system runs (``statusCode 902``); activating is allowed at any time.

    Options are seeded over REST and kept current by ``FAVORITES_SNAPSHOT``, which the
    controller pushes after every create, edit and delete. See
    `docs/protocol/hub_controller.md` §2-§5.
    """

    _attr_icon = "mdi:playlist-star"
    _attr_name = "Favorite"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_favourite"

    @staticmethod
    def _name_of(favorite: dict) -> str:
        """The favorite's name: ``title`` over REST, ``name`` in ``FAVORITES_SNAPSHOT``.

        Snapshots replace the REST seed wholesale, so both keys must be read.
        """
        return str(favorite.get("name") or favorite.get("title") or "").strip()

    @property
    def _favorites(self) -> list[dict]:
        """Named favorites only, excluding experiences.

        ``isExperience`` is in the REST list but not the MQTT snapshot, so a missing
        flag counts as "not an experience": better to show one than hide one.
        """
        return [
            f
            for f in self._controller.favorites
            if self._name_of(f)
            and str(f.get("isExperience", "")).strip().lower() != "true"
        ]

    @property
    def options(self) -> list[str]:
        names = [self._name_of(f) for f in self._favorites]
        # `FAVORITE_STS` can name a favorite the list lacks (just created, or before the
        # first snapshot); carry it while running, as `ValveWarmupSelect.options` does.
        state = self._state
        running = None if state is None else state.active_favorite_name
        if running and running not in names:
            names.append(running)
        return [OPTION_OFF, *names]

    @property
    def _device_option(self) -> str | None:
        """The running favorite, or ``Off``.

        Prefers the name ``FAVORITE_STS`` carries, which is right before the list is
        seeded and immune to id reassignment; falls back to the id.
        ``active_favorite_id`` of None (``status: "OFF"`` or id ``"0"``) means nothing
        runs; an unnamed id reads ``Off``.
        """
        state = self._state
        if state is None:
            return None
        active = state.active_favorite_id
        if active is None:
            return OPTION_OFF
        # Safe: `options` carries this name even before the list knows it.
        if state.active_favorite_name:
            return state.active_favorite_name
        for favorite in self._favorites:
            if str(favorite.get("id")) == str(active):
                return self._name_of(favorite)
        return OPTION_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._state
        return {
            "active_favorite_id": None if state is None else state.active_favorite_id,
            "active_favorite_name": (
                None if state is None else state.active_favorite_name
            ),
            "favourite_count": len(self._favorites),
        }

    async def async_select_option(self, option: str) -> None:
        """Activate the named favorite, or stop the water.

        Resolved by name at call time, since ids shift when a favorite is deleted.
        """
        if option == OPTION_OFF:
            # `valvecontrol OFF`, not `stopall`: stop water, leave music, steam and
            # lighting alone. The System switch stops everything.
            await self._async_command(
                OPTION_OFF,
                self.coordinator.async_set_hub_shower(self._controller, False),
            )
            return
        wanted = option.strip().lower()
        favorite = next(
            (f for f in self._favorites if self._name_of(f).lower() == wanted), None
        )
        if favorite is None:
            raise HomeAssistantError(
                f"No Anthem Plus favorite called {option!r}. It may have been renamed or "
                "deleted in the Konnect app."
            )
        await self._async_command(
            option,
            self.coordinator.async_activate_favorite(
                self._controller, favorite.get("id"), self._name_of(favorite)
            ),
        )


class HubExperienceSelect(OptimisticOptionMixin, KohlerControllerEntity, SelectEntity):
    """Start one of the controller's experiences, and show which one is running.

    The catalogue (Breathe, Cool Down, Focus, steam coaches, ice showers) comes from
    ``hub-experience/{id}/experiences`` in three categories, each with its own endpoint
    taking ``{name: <title>, status}``
    (`KohlerKonnectCoordinator.async_control_hub_experience` picks it). Run state
    arrives as ``SHOWER_EXP_STS`` / ``STEAM_EXP_STS`` / ``ICE_SHOWER_EXP_STS``.
    Experiences run from zone 1's first outlet. See
    `docs/protocol/hub_controller.md` §3.
    """

    _attr_icon = "mdi:creation"
    _attr_name = "Experience"

    def __init__(
        self, coordinator: KohlerKonnectCoordinator, controller: Controller
    ) -> None:
        super().__init__(coordinator, controller)
        self._attr_unique_id = f"{self._device_id}_experience"

    @property
    def _titles(self) -> list[str]:
        titles: list[str] = []
        for items in self._controller.experiences.values():
            for item in items:
                title = str(item.get("title") or item.get("name") or "").strip()
                if title and title not in titles:
                    titles.append(title)
        return titles

    @property
    def options(self) -> list[str]:
        titles = self._titles
        state = self._state
        running = None if state is None else state.active_experience
        # Carry a running title the catalogue lacks, like `HubFavoriteSelect.options`.
        if running and running not in titles:
            titles.append(running)
        return [OPTION_OFF, *titles]

    @property
    def _device_option(self) -> str | None:
        state = self._state
        if state is None:
            return None
        return state.active_experience or OPTION_OFF

    async def async_select_option(self, option: str) -> None:
        """Start the named experience, or stop the running one."""
        if option == OPTION_OFF:
            state = self._state
            running = None if state is None else state.active_experience
            # As on the valve: one started a moment ago is the one to stop.
            running = running or self._pending_option()
            if running is None:
                return
            await self._async_command(
                OPTION_OFF,
                self.coordinator.async_control_hub_experience(
                    self._controller, running, False
                ),
            )
            return
        await self._async_command(
            option,
            self.coordinator.async_control_hub_experience(
                self._controller, option, True
            ),
        )


def _duration_label(seconds: int) -> str:
    """`1800` -> `"30 minutes"`, the way the Konnect app words it."""
    return f"{seconds // 60} minutes"


class OutletRunTimeSelect(KohlerValveEntity, SelectEntity):
    """Max Shower Duration: the Konnect app's six options, 15 to 60 minutes.

    A select, not a slider: the app offers only 15/20/25/30/45/60 minutes, and whether
    the valve accepts the values between (35, 40, 50, 55) is untested, so this offers
    exactly the app's list. 3600 s is live-verified writable.

    ⚠️ Konnect 3.0.1 shows anything above 30 minutes as 25, and saving there writes
    1500 s (fixed in 3.0.5); `long_duration_app_warning` flags such a value. See
    `docs/protocol/gcs_valve.md` §4.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-cog-outline"
    _attr_entity_registry_enabled_default = True

    @property
    def options(self) -> list[str]:
        """Built per instance — a shared mutable class attribute is one edit from a bug."""
        return [_duration_label(s) for s in OUTLET_RUN_TIME_CHOICES_SECONDS]

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        super().__init__(coordinator, valve)
        self._attr_name = "Max Shower Duration"
        self._attr_unique_id = f"{self._device_id}_max_run_time_setting"

    @property
    def _seconds(self) -> int | None:
        """What the valve currently holds — the shortest of its outlets' limits."""
        run_times = self._valve.outlet_run_times
        return min(run_times.values()) if run_times else None

    @property
    def current_option(self) -> str | None:
        """None when the valve holds a duration outside the six (2400 s, say).

        Inventing an option for it would let a save rewrite it; `reported_minutes` has
        the real figure.
        """
        seconds = self._seconds
        if seconds is None or seconds not in OUTLET_RUN_TIME_CHOICES_SECONDS:
            return None
        return _duration_label(seconds)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the valve holds, and whether its outlets agree.

        `outlets_agree: false` means a write was lost, not a different configuration:
        the one duration is written an outlet at a time, stopping at the first failure
        (`docs/protocol/gcs_valve.md` §4.1). Selecting a duration rewrites every outlet.
        """
        seconds = self._seconds
        run_times = self._valve.outlet_run_times
        attributes: dict[str, Any] = {
            # The real value, even outside the six options.
            "reported_minutes": None if seconds is None else seconds / 60,
            "in_app_picker": seconds in OUTLET_RUN_TIME_CHOICES_SECONDS,
            # Above what Konnect 3.0.1 can show; see the class docstring.
            "long_duration_app_warning": (
                seconds is not None and seconds > OUTLET_RUN_TIME_APP_SAFE_MAX_SECONDS
            ),
        }
        if run_times:
            attributes["outlets_agree"] = len(set(run_times.values())) == 1
            per_outlet: dict[str, float] = {}
            raw_grouping = self._valve.zone_grouping
            grouping = (
                ZONE_GROUPING_OUTLET_LABELS
                if raw_grouping == ZONE_GROUPING_SUBDEVICES
                else raw_grouping
            )
            for outlet, value in sorted(run_times.items()):
                # `outlet_run_times` is 1-based, as `outlet_location` expects: no `+ 1`.
                zone, index = self._valve.model.outlet_location(outlet)
                name = outlet_name(self._valve, zone, index + 1, grouping=grouping)
                per_outlet[name] = value / 60
            attributes["per_outlet"] = per_outlet
        return attributes

    async def async_select_option(self, option: str) -> None:
        try:
            seconds = next(
                s
                for s in OUTLET_RUN_TIME_CHOICES_SECONDS
                if _duration_label(s) == option
            )
        except (
            StopIteration
        ) as err:  # pragma: no cover - Home Assistant validates first
            raise HomeAssistantError(f"{option} is not a Max Shower Duration") from err
        await self._valve.async_write_outlet_setting(maximum_run_time=seconds)
        self.async_write_ha_state()
