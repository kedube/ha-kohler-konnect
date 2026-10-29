"""Each product's entities, platform by platform; the platform modules ask for them here.

Apart from the product records (`products.py`) because the entity modules import the
account coordinator, which imports those.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import KohlerKonnectCoordinator
from .evocycle.entities import evocycle_entities
from .faucet.binary_sensor import faucet_binary_sensors
from .faucet.button import faucet_buttons
from .faucet.number import faucet_numbers
from .faucet.select import add_faucet_selects
from .faucet.sensor import faucet_sensors
from .faucet.switch import faucet_switches
from .faucet.update import faucet_updates
from .ice_bath.entities import ice_bath_entities
from .products import EVOCYCLES, FAUCETS, ICE_BATHS, SMART_FANS, TOILETS
from .smart_fan.entities import smart_fan_entities
from .toilet.entities import toilet_entities

__all__ = ["product_entities"]

#: One device's entities for one platform. Those that appear later (a faucet's preset
#: controls, once it has a preset) are added through the callback when they do.
EntityBuilder = Callable[
    [HomeAssistant, ConfigEntry, Any, AddEntitiesCallback], Iterable[Entity]
]


# Every platform; a product's builder answers none for those it doesn't use.
_PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
)


def _faucet_selects(
    hass: HomeAssistant, entry: ConfigEntry, faucet: Any, add: AddEntitiesCallback
) -> Iterable[Entity]:
    add_faucet_selects(entry, faucet, add)
    return ()


_BUILDERS: dict[str, dict[Platform, EntityBuilder]] = {
    FAUCETS.key: {
        Platform.BINARY_SENSOR: lambda hass, entry, faucet, add: faucet_binary_sensors(
            faucet
        ),
        Platform.BUTTON: faucet_buttons,
        Platform.NUMBER: lambda hass, entry, faucet, add: faucet_numbers(faucet),
        Platform.SELECT: _faucet_selects,
        Platform.SENSOR: lambda hass, entry, faucet, add: faucet_sensors(faucet),
        Platform.SWITCH: lambda hass, entry, faucet, add: faucet_switches(faucet),
        Platform.UPDATE: lambda hass, entry, faucet, add: faucet_updates(faucet),
    },
    SMART_FANS.key: {
        platform: (
            lambda hass, entry, fan, add, platform=platform: smart_fan_entities(
                fan, platform
            )
        )
        for platform in _PLATFORMS
    },
    TOILETS.key: {
        platform: (
            lambda hass, entry, toilet, add, platform=platform: toilet_entities(
                toilet, platform
            )
        )
        for platform in _PLATFORMS
    },
    EVOCYCLES.key: {
        platform: (
            lambda hass, entry, evo, add, platform=platform: evocycle_entities(
                evo, platform
            )
        )
        for platform in _PLATFORMS
    },
    ICE_BATHS.key: {
        platform: (
            lambda hass, entry, journal, add, platform=platform: ice_bath_entities(
                journal, platform
            )
        )
        for platform in _PLATFORMS
    },
}


def product_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: KohlerKonnectCoordinator,
    platform: Platform,
    add: AddEntitiesCallback,
) -> list[Entity]:
    """Every product device's entities for ``platform``, product by product."""
    entities: list[Entity] = []
    for key, devices in coordinator.products.items():
        build = _BUILDERS.get(key, {}).get(platform)
        if build is None:
            continue
        for device in devices:
            entities.extend(build(hass, entry, device, add))
    return entities
