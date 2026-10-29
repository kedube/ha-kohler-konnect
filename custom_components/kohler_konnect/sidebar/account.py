"""An account's options, as the panel's Account screen reads and saves them.

The same form as Configure on the integration's card (`config_flow.options_schema`), with
the same checks and the same merge, so the two can never disagree about what is valid.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    SelectSelector,
)

from ..config_flow import (
    merge_options,
    option_values,
    options_schema,
    validate_options,
)
from ..const import RELOAD_IGNORED_OPTION_KEYS
from ..coordinator import entry_reload_signature

__all__ = ["account_options", "async_update_account_options"]


def _field(key: str, selector: Any) -> dict[str, Any] | None:
    """How the panel draws one option, and whether saving it reloads the integration."""
    field: dict[str, Any] = {
        "key": key,
        "reloads": key not in RELOAD_IGNORED_OPTION_KEYS,
    }
    if isinstance(selector, SelectSelector):
        options = selector.config["options"]
        return {
            **field,
            "kind": "select",
            "options": [o if isinstance(o, str) else o["value"] for o in options],
        }
    if isinstance(selector, NumberSelector):
        config = selector.config
        if config.get("unit_of_measurement") != "min":
            return None
        return {**field, "kind": "minutes", "min": config["min"], "max": config["max"]}
    if isinstance(selector, BooleanSelector):
        return {**field, "kind": "toggle"}
    return None


def account_options(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """The options Configure offers this account, their values, and how to draw them."""
    fields = (
        _field(str(marker), selector)
        for marker, selector in options_schema(hass, entry).schema.items()
    )
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "values": option_values(hass, entry),
        "fields": [field for field in fields if field is not None],
    }


@callback
def async_update_account_options(
    hass: HomeAssistant, entry: ConfigEntry, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Save some of an account's options; ``reloads`` says the integration reconnects.

    Raises `vol.Invalid` for a value Configure would refuse. The reload itself is the
    update listener's, which compares the same signature.
    """
    checked = validate_options(hass, entry, changes)
    before = entry_reload_signature(entry)
    hass.config_entries.async_update_entry(entry, options=merge_options(entry, checked))
    return {
        "reloads": entry_reload_signature(entry) != before,
        "values": option_values(hass, entry),
    }
