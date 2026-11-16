"""Faucet preset amounts that are not a positive, finite number of litres.

Python's JSON parser reads a bare `NaN` or `Infinity` as a float, `1e400` overflows to
infinity, and a long run of digits is an integer too large for a float. Such a preset is
skipped, and the time a dispense is followed for never raises on any amount. The
end-to-end faucet guards are in `integration/test_faucet_guards.py`. Each test here failed
on the code before its fix, unless its docstring says it guards the fix itself.
"""

from __future__ import annotations

import json
import math

import pytest

from custom_components.kohler_konnect.const import DISPENSE_MAX
from custom_components.kohler_konnect.faucet.coordinator import dispense_limit
from custom_components.kohler_konnect.konnect.faucet import (
    FaucetPreset,
    _parse_preset_items,
)


def _parsed(raw_amount: str) -> list[FaucetPreset]:
    items = json.loads(
        f'[{{"experienceId": "p", "title": "Pot", "dispenseAmount": {raw_amount}}}]'
    )
    return _parse_preset_items(items, None)


@pytest.mark.parametrize(
    "raw",
    ["NaN", "Infinity", "1e400", "1" + "0" * 400],
    ids=["nan", "infinity", "overflow", "huge-integer"],
)
def test_a_preset_whose_amount_is_not_finite_is_skipped(raw: str) -> None:
    assert _parsed(raw) == []


def test_a_non_finite_display_quantity_does_not_stand_in() -> None:
    items = [
        {
            "experienceId": "p",
            "title": "Pot",
            "displayQuantity": "1" + "0" * 400 + " Liters",
        }
    ]
    assert _parse_preset_items(items, None) == []


@pytest.mark.parametrize(("raw", "liters"), [("1.2", 1.2), ("3", 3.0)])
def test_a_finite_amount_is_kept(raw: str, liters: float) -> None:
    """Guards the fix itself: an ordinary amount, float or integer, still reads."""
    assert _parsed(raw) == [FaucetPreset("p", "Pot", liters)]


@pytest.mark.parametrize("raw", ["0", "-1.5"])
def test_a_preset_that_pours_nothing_is_still_skipped(raw: str) -> None:
    """Guards the fix itself."""
    assert _parsed(raw) == []


@pytest.mark.parametrize("liters", [math.inf, math.nan, 1e308])
def test_dispense_limit_allows_for_the_largest_dispense_whatever_the_amount(
    liters: float,
) -> None:
    assert dispense_limit(liters) == dispense_limit(None)


def test_dispense_limit_is_unchanged_for_real_amounts() -> None:
    """Guards the fix itself."""
    assert dispense_limit(0.25) == DISPENSE_MAX.total_seconds()
    assert dispense_limit(11.36) == dispense_limit(None) > DISPENSE_MAX.total_seconds()
