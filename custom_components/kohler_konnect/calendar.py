"""Calendars: the Ice Bath journal's sessions, for now the only product with one."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import KohlerKonnectCoordinator
from .product_platforms import product_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Every product device's calendars."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    if entities := product_entities(
        hass, entry, coordinator, Platform.CALENDAR, async_add_entities
    ):
        async_add_entities(entities)
