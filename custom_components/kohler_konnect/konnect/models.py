"""Kohler Anthem hardware models and their outlet topology.

How many outlets a model has, and which valve each one lives on, varies by model; setup
detects the split (``topology.py``) or asks the user, and everything downstream derives
from the resulting model. Never assume valve 1 carries outlets 1-3: on a K-28211, outlet
3 is valve 2's first.

Each valve's command word has three outlet bits whatever is installed; a model simply
uses fewer of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class ValveModel:
    """One Anthem digital valve model and how its outlets map onto the two valves."""

    sku: str
    name: str
    outlets_valve1: int
    outlets_valve2: int

    @property
    def total_outlets(self) -> int:
        """How many physical outlets this model has."""
        return self.outlets_valve1 + self.outlets_valve2

    @property
    def uses_valve2(self) -> bool:
        """Whether the second valve is populated.

        When it is not, ``secondaryValve1`` is still sent, as the all-zero ignore word.
        """
        return self.outlets_valve2 > 0

    @property
    def zones(self) -> list[int]:
        """The zone numbers this model actually has — ``[1]`` or ``[1, 2]``.

        Entities are named per zone, the unit every API surface addresses, so they need
        no model-dependent global outlet numbering.
        """
        return [1, 2] if self.uses_valve2 else [1]

    def outlets_in_zone(self, zone: int) -> int:
        """How many outlets that zone has."""
        if zone == 1:
            return self.outlets_valve1
        if zone == 2:
            return self.outlets_valve2
        raise ValueError(f"{self.sku} has zones {self.zones}; got {zone}")

    def outlet_location(self, outlet: int) -> tuple[int, int]:
        """Map a 1-based global outlet number to ``(zone, bit_index)``.

        Only for places that need a flat sequence, such as combining the controller's
        two zone arrays. The mapping is model-dependent, which is why entities are per
        zone.
        """
        if not 1 <= outlet <= self.total_outlets:
            raise ValueError(
                f"{self.sku} has outlets 1-{self.total_outlets}; got {outlet}"
            )
        if outlet <= self.outlets_valve1:
            return 1, outlet - 1
        return 2, outlet - self.outlets_valve1 - 1

    def outlet_id(self, zone: int, outlet: int) -> int:
        """Map a 1-based ``(zone, outlet)`` to the valve's 0-based ``outLetId``.

        Each valve body has three ``outLetId`` slots (0-2 valve 1, 3-5 valve 2) however
        many outlets are fitted, so zone 2's first outlet is always 3, even on a K-28211
        (``docs/protocol/gcs_valve.md`` §1.1).
        """
        count = self.outlets_in_zone(zone)
        if not 1 <= outlet <= count:
            raise ValueError(
                f"{self.sku} zone {zone} has outlets 1-{count}; got {outlet}"
            )
        return (zone - 1) * 3 + (outlet - 1)

    def outlet_from_id(self, outlet_id: int) -> int | None:
        """Map a 0-based hardware ``outLetId`` to a 1-based global outlet, or ``None``.

        The inverse of :meth:`outlet_id`, into the ``1..total_outlets`` numbering of
        :meth:`outlet_location`. ``None`` when the id is not a populated outlet.
        """
        if outlet_id < 0:
            return None
        zone = outlet_id // 3 + 1
        bit = outlet_id % 3
        if zone not in self.zones or bit >= self.outlets_in_zone(zone):
            return None
        return (bit + 1) if zone == 1 else (self.outlets_valve1 + bit + 1)

    def split_outlets(self, outlets: list[bool]) -> tuple[list[bool], list[bool]]:
        """Split per-outlet flags into (valve1 flags, valve2 flags)."""
        if len(outlets) != self.total_outlets:
            raise ValueError(
                f"{self.sku} has {self.total_outlets} outlets, got {len(outlets)} flags"
            )
        return (
            outlets[: self.outlets_valve1],
            outlets[self.outlets_valve1 :],
        )


# The four Anthem digital valve models (``docs/protocol/gcs_valve.md`` §1).
VALVE_MODELS: dict[str, ValveModel] = {
    "K-28209": ValveModel("K-28209", "Anthem 2-outlet valve", 2, 0),
    "K-28210": ValveModel("K-28210", "Anthem 3-outlet valve", 3, 0),
    "K-28211": ValveModel("K-28211", "Anthem 4-outlet valve", 2, 2),
    "K-28212": ValveModel("K-28212", "Anthem 6-outlet valve", 3, 3),
}

DEFAULT_VALVE_MODEL = "K-28212"


def model_for_topology(outlets_valve1: int, outlets_valve2: int) -> ValveModel:
    """Build a model from a detected outlet split, naming it if it matches a known SKU.

    Real installs are not obliged to match Kohler's four catalogue models, so an
    unrecognised split still produces a usable model rather than an error.
    """
    for model in VALVE_MODELS.values():
        if (model.outlets_valve1, model.outlets_valve2) == (
            outlets_valve1,
            outlets_valve2,
        ):
            return model
    label = (
        f"{outlets_valve1}+{outlets_valve2}" if outlets_valve2 else str(outlets_valve1)
    )
    return ValveModel(
        sku="detected",
        name=f"Detected valve ({label} outlets)",
        outlets_valve1=outlets_valve1,
        outlets_valve2=outlets_valve2,
    )


def get_valve_model(sku: str) -> ValveModel:
    """Look up a valve model by SKU, case-insensitively."""
    model = VALVE_MODELS.get(sku.strip().upper())
    if model is None:
        raise ValueError(
            f"Unknown valve model {sku!r}; expected one of {sorted(VALVE_MODELS)}"
        )
    return model


# Whether a 2-outlet valve (K-28209, or each half of a K-28211) is hardware-verified to
# use mask bits 0x01/0x02 for its two outlets. The app does so
# (``docs/protocol/gcs_valve.md`` §1.1); this stays False until a 2-outlet valve is seen
# running the right head for each bit. If one disagrees, ``outlet_location`` is the
# place to correct it.
TWO_OUTLET_BIT_MAPPING_VERIFIED = False


# ---------------------------------------------------------------------------
# Where outlet / temperature / flow state comes from
# ---------------------------------------------------------------------------
class OutletStateSource(Enum):
    """Which channel feeds the outlet, temperature, and flow entities."""

    GCS_VALVE_HEX = "gcs_valve_hex"
    """The GCS ``GCS_SOLO_STS`` valve command word. Authoritative and always current."""

    HUB_MQTT = "hub_mqtt"
    """The HUB ``SHOWER_VALVE_STS`` message. The only option on a HUB-only account."""


def resolve_outlet_source(has_gcs: bool, has_hub: bool) -> OutletStateSource | None:
    """Pick the outlet state source for an account, or ``None`` with neither device.

    Whenever a GCS device is present, use the GCS valve word, never the HUB's: the HUB
    reports valve-driven sessions late or not at all, and preset-driven ones never
    (``docs/protocol/hub_controller.md`` §5). A HUB-only account has no valve word; its
    sessions run through favorites and the touchscreen, which the HUB does report.
    """
    if has_gcs:
        return OutletStateSource.GCS_VALVE_HEX
    if has_hub:
        return OutletStateSource.HUB_MQTT
    return None


# ---------------------------------------------------------------------------
# Touchscreen interfaces — which controller gets a device into the Konnect app
# ---------------------------------------------------------------------------
# K-28214 (first generation, plugs into the valve) is the only way to add a GCS valve to
# the Konnect app; K-28214-ASC (Anthem Plus, plugs into the HUB controller) adds only
# the HUB. A valve can have both at once, so one shower can appear as a GCS and a HUB
# (``docs/protocol/gcs_valve.md`` §1).
TOUCHSCREEN_GCS = "K-28214"
TOUCHSCREEN_HUB = "K-28214-ASC"
