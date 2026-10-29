"""Lights: the Smart Fan's, for now the only product with one."""

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
    """Every product device's lights."""
    coordinator: KohlerKonnectCoordinator = hass.data[DOMAIN][entry.entry_id]
    if entities := product_entities(
        hass, entry, coordinator, Platform.LIGHT, async_add_entities
    ):
        async_add_entities(entities)
