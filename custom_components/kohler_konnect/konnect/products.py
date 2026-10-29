"""The Kohler products supported beyond the Anthem shower, by the SKUs that name them.

What the account's device list is sorted by (`client.Customer`). The integration's
`products.py` adds how each is built and shown in Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    FAUCET_SKUS,
    SKU_EVOCYCLE,
    SKU_ICE_BATH,
    SKU_NUMI,
    SKU_PUREWASH,
    SKU_SMART_FAN,
)

__all__ = [
    "EVOCYCLE",
    "FAUCET",
    "ICE_BATH",
    "PRODUCT_KINDS",
    "SMART_FAN",
    "TOILET",
    "ProductKind",
]


@dataclass(frozen=True)
class ProductKind:
    """One product: its key, the SKUs the account lists it under, and what to call one."""

    #: Names the product everywhere: diagnostics blocks, the sidebar panel's `kind`.
    key: str
    skus: frozenset[str]
    #: How the setup summary counts them: "2 faucet(s)".
    noun: str

    def owns(self, sku: str) -> bool:
        """Whether a device listed under ``sku`` is one of these. Case-insensitive."""
        return sku.upper() in self.skus


FAUCET = ProductKind("faucet", FAUCET_SKUS, "faucet")
SMART_FAN = ProductKind("smart_fan", frozenset({SKU_SMART_FAN}), "smart fan")
# The Numi 2.0 and the Purewash E930 seat share one protocol (`toilet.py`).
TOILET = ProductKind(
    "toilet", frozenset({SKU_NUMI, SKU_PUREWASH}), "Numi toilet or Purewash seat"
)

EVOCYCLE = ProductKind("evocycle", frozenset({SKU_EVOCYCLE}), "EvoCycle shower")

# One person's session journal: the bath itself never talks to the cloud.
ICE_BATH = ProductKind("ice_bath", frozenset({SKU_ICE_BATH}), "Ice Bath journal")

PRODUCT_KINDS: tuple[ProductKind, ...] = (FAUCET, SMART_FAN, TOILET, EVOCYCLE, ICE_BATH)
