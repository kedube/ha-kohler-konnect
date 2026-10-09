"""Volume units and quick-dispense amounts, metric or US.

Kohler's API always takes litres. Everything the user sees — the quick-dispense buttons, the
dispense-amount number, the last-dispense sensor and the default unit of the ``dispense``
action — follows the Konnect account's own unit setting (`waterUnits`), as the app does and
as the water-usage sensors do. The separate faucet integration had its own metric/imperial
option for this; one setting for the whole account replaced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.const import UnitOfVolume

from ..const import DISPENSE_MAX_ML
from ..konnect.usage import is_metric

# Units accepted by the ``dispense`` action, in millilitres per unit. The US customary
# factors match the ones the Konnect app uses.
ML_PER_UNIT: dict[str, float] = {
    "ml": 1.0,
    "l": 1000.0,
    "fl_oz": 29.5735295625,
    "cup": 236.5882365,
    "qt": 946.352946,
    "gal": 3785.411784,
}
UNIT_LABELS: dict[str, str] = {
    "ml": "mL",
    "l": "L",
    "fl_oz": "fl oz",
    "cup": "cups",
    "qt": "qt",
    "gal": "gal",
}

# Home Assistant unit strings that may appear in restored state.
HA_UNIT_KEYS: dict[str, str] = {
    UnitOfVolume.MILLILITERS: "ml",
    UnitOfVolume.LITERS: "l",
    UnitOfVolume.FLUID_OUNCES: "fl_oz",
    UnitOfVolume.GALLONS: "gal",
}


def to_ml(amount: float, unit: str) -> float:
    """Convert an amount in ``unit`` to millilitres."""
    return amount * ML_PER_UNIT[unit]


def from_ml(ml: float, unit: str) -> float:
    """Convert millilitres to ``unit``."""
    return ml / ML_PER_UNIT[unit]


@dataclass(frozen=True, kw_only=True)
class QuickAmount:
    """A one-tap dispense button."""

    key: str  # unique-id suffix
    ml: float
    translation_key: str
    placeholders: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class UnitProfile:
    """How dispense amounts are presented for one unit system."""

    service_unit: str  # default unit of the ``dispense`` action's ``amount``
    number_unit: str  # key into ML_PER_UNIT
    number_native_unit: str  # Home Assistant unit string
    number_min: float
    number_max: float
    number_step: float
    number_default: float
    precision: int
    quick_amounts: tuple[QuickAmount, ...]


def _metric(ml: int) -> QuickAmount:
    label = f"{ml / 1000:g} L" if ml >= 1000 else f"{ml} mL"
    return QuickAmount(
        key=f"{ml}ml",
        ml=float(ml),
        translation_key="quick_dispense",
        placeholders={"amount": label},
    )


def _imperial(key: str, unit: str, amount: float) -> QuickAmount:
    return QuickAmount(
        key=key, ml=to_ml(amount, unit), translation_key=f"dispense_{key}"
    )


METRIC = UnitProfile(
    service_unit="ml",
    number_unit="ml",
    number_native_unit=UnitOfVolume.MILLILITERS,
    number_min=10,
    number_max=DISPENSE_MAX_ML,
    number_step=10,
    number_default=250,
    precision=0,
    quick_amounts=tuple(_metric(ml) for ml in (50, 250, 500, 750, 1000, 2000, 3000)),
)

IMPERIAL = UnitProfile(
    service_unit="fl_oz",
    number_unit="fl_oz",
    number_native_unit=UnitOfVolume.FLUID_OUNCES,
    number_min=0.5,
    number_max=384,  # 3 gallons, the Konnect app's largest amount
    number_step=0.5,
    number_default=8,
    precision=1,
    quick_amounts=(
        _imperial("quarter_cup", "cup", 0.25),
        _imperial("half_cup", "cup", 0.5),
        _imperial("one_cup", "cup", 1),
        _imperial("two_cups", "cup", 2),
        _imperial("one_quart", "qt", 1),
        _imperial("half_gallon", "gal", 0.5),
        _imperial("one_gallon", "gal", 1),
    ),
)

# Every quick-dispense unique-id suffix, across both unit systems.
ALL_QUICK_KEYS: frozenset[str] = frozenset(
    q.key for profile in (METRIC, IMPERIAL) for q in profile.quick_amounts
)


def profile_for(water_units: str | None) -> UnitProfile:
    """The profile for an account's `waterUnits` — `Standard` is US, anything else metric."""
    return METRIC if is_metric(water_units) else IMPERIAL
