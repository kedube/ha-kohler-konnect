"""Every translation matches strings.json, and Home Assistant loads them."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.generated.languages import LANGUAGES
from homeassistant.helpers.translation import async_get_translations

from custom_components.kohler_konnect.const import DOMAIN

INTEGRATION = Path(__file__).parents[2] / "custom_components" / DOMAIN
TRANSLATIONS = sorted((INTEGRATION / "translations").glob("*.json"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


SOURCE = _flatten(json.loads((INTEGRATION / "strings.json").read_text()))


def test_english_matches_strings() -> None:
    english = json.loads((INTEGRATION / "translations" / "en.json").read_text())
    assert _flatten(english) == SOURCE


def test_every_language_of_both_old_integrations_is_kept() -> None:
    """Kohler Anthem had six; Kohler Sensate had those and four more."""
    assert {path.stem for path in TRANSLATIONS} == {
        "de",
        "en",
        "es",
        "es-419",
        "fr",
        "it",
        "nl",
        "pl",
        "pt-BR",
        "sv",
    }


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda path: path.stem)
def test_translation_is_complete(path: Path) -> None:
    assert path.stem in LANGUAGES
    strings = _flatten(json.loads(path.read_text()))

    assert strings.keys() == SOURCE.keys()
    for key, text in strings.items():
        assert text.strip(), key
        # A dropped or renamed placeholder makes Home Assistant show the raw key or
        # fail to format the message.
        assert set(PLACEHOLDER.findall(text)) == set(
            PLACEHOLDER.findall(SOURCE[key])
        ), key


async def test_translations_load(hass: HomeAssistant) -> None:
    names = await async_get_translations(hass, "pt-BR", "entity", {DOMAIN})
    assert names[f"component.{DOMAIN}.entity.switch.water.name"] == "Água"

    # Each Spanish has its own word for the faucet.
    key = f"component.{DOMAIN}.exceptions.faucet_required.message"
    names = await async_get_translations(hass, "es", "exceptions", {DOMAIN})
    assert "grifo" in names[key]
    names = await async_get_translations(hass, "es-419", "exceptions", {DOMAIN})
    assert "llave" in names[key]

    # A language without a file falls back to English.
    names = await async_get_translations(hass, "fi", "entity", {DOMAIN})
    assert names[f"component.{DOMAIN}.entity.switch.water.name"] == "Water"
