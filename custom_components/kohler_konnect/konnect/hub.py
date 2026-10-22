"""Commands for the Anthem Plus system controller (SKU ``HUB``).

The controller is favorite-centric: there is no "set outlets, temperature and flow now"
command. To run a configuration, create or edit a favorite and activate it. The only
direct commands are on/off for the controller's stored default (``valvecontrol``,
``steamcontrol``) and ``stopall``. See `docs/protocol/hub_controller.md` §3 and §4.

* Editing a favorite is refused while the system runs (``statusCode 902``, raised as
  :class:`~.client.DeviceRunning`); activating one is always allowed. So pre-create a
  favorite per state and switch between them by activation.
* An all-off favorite is not ``stopall``: it stops the outputs but the favorite still
  reports as running. Only ``stopall`` fully idles the system.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from .client import KohlerClient
from .const import (
    EXPERIENCE_ENDPOINTS,
    HUB_FAVORITE,
    HUB_FAVORITE_CONTROL,
    HUB_STEAM_CONTROL,
    HUB_STOP_ALL,
    HUB_VALVE_CONTROL,
    SKU_HUB,
)
from .models import ValveModel

ON = "ON"
OFF = "OFF"

# Read and write shapes differ (`docs/protocol/hub_controller.md` §1):
#   READ  water.zoneN.outlets = a count, and outletState = a 6-slot array
#   WRITE water.zoneN.outlets = a list of 0-based positions to open
# Outlet arrays (REST and MQTT SHOWER_VALVE_STS) are padded to six slots per zone; only
# the zone valve's leading slots mean anything: three on a K-28210/K-28212, two on a
# K-28209 or either half of a K-28211. zone1 is valve1, zone2 is valve2.
OUTLETS_PER_ZONE = 3
ZONE_ARRAY_SLOTS = 6


def outlet_positions(outlets: list[bool]) -> list[int]:
    """Convert one zone's outlet flags into the 0-based position list a write expects."""
    return [index for index, is_on in enumerate(outlets) if is_on]


def outlet_flags(
    outlet_state: list[int] | None, outlet_count: int = OUTLETS_PER_ZONE
) -> list[bool]:
    """Convert one zone's padded outlet array into that zone's real outlet flags.

    ``outlet_count`` is how many outlets the zone's valve has; later slots are padding.
    Slots are read through :func:`_slot_on`, so ``"0"`` is off, as ``0`` is.
    """
    state = outlet_state or []
    return [
        _slot_on(state[i]) if i < len(state) else False for i in range(outlet_count)
    ]


def _slot_on(value: Any) -> bool:
    """One outlet-array slot: a number, or the same number as a string.

    A numeric string reads by its whole part (``"1.0"`` on, ``"0.0"`` off) and
    ``"true"``/``"on"`` read as on; any other string is off.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "on"}:
            return True
        return bool(_int_or_none(text))
    return bool(value)


def zone_outlet_flags(
    model: ValveModel,
    zone1_outlets: list[int] | None,
    zone2_outlets: list[int] | None = None,
) -> list[bool]:
    """Combine both zones' padded arrays into global Home Assistant outlet flags.

    Returns one flag per physical outlet, numbered the way every surface in this
    integration numbers them: zone1's outlets first, then zone2's. On a 4-outlet K-28211
    that makes zone2's first outlet "Outlet 3".
    """
    flags = outlet_flags(zone1_outlets, model.outlets_valve1)
    if model.uses_valve2:
        flags += outlet_flags(zone2_outlets, model.outlets_valve2)
    return flags


# HUB payloads name the zone several ways (`zone: "1"`, `component: "valve1"`, …), all
# meaning the same; read them through `zone_number`. See
# `docs/protocol/hub_controller.md` §1.
_ZONE_FIELDS = ("zone", "component", "valve", "valveIndex", "zoneIndex")


def zone_number(attribute: dict[str, Any]) -> int | None:
    """Identify which zone/valve a HUB payload entry refers to, or None.

    Accepts every spelling seen in the wild: ``zone: "1"``, ``zone: 1``,
    ``component: "valve1"``, ``valveIndex: "Valve2"``, and so on.
    """
    for field in _ZONE_FIELDS:
        raw = attribute.get(field)
        if raw is None:
            continue
        text = str(raw).strip().lower()
        # Bare number: "1" / "2".
        if text in {"1", "2"}:
            return int(text)
        # Prefixed forms: "valve1", "zone2", "Valve1".
        for prefix in ("valve", "zone"):
            if text.startswith(prefix):
                suffix = text[len(prefix) :].strip()
                if suffix in {"1", "2"}:
                    return int(suffix)
    return None


CONNECTED = "Connected"

#: What `about.valveN.serialNumber` reads for a valve body that is not fitted.
_NO_SERIAL = "0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0:0"


def _connected(value: Any) -> bool:
    """`parts.X == "Connected"`, case-insensitively — the app's own comparison."""
    return str(value or "").strip().lower() == CONNECTED.lower()


def _int_or_none(value: Any) -> int | None:
    # `OverflowError` too: `int(float("Infinity"))` raises it, not `ValueError`.
    try:
        return int(float(str(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def _ip_or_none(value: Any) -> str | None:
    """A usable IP address, or None for anything else — blank, "null", ``0.0.0.0``."""
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    return None if address.is_unspecified else str(address)


@dataclass(frozen=True)
class HubSettings:
    """The controller's own settings, as ``hub-configuration`` publishes them.

    ``max_shower_duration_minutes`` (``systemSettings.maxShowerDuration``) is the default
    and cap for a favorite's duration. ``disconnected`` lists accessories that are fitted
    but not ``Connected``, by the app's rules (`docs/protocol/hub_controller.md` §2.2);
    `HubCapabilities`, which reads ``parts`` alone, cannot see those. ``lan_ip`` is
    ``about.hub.wlan.ip``, else ``about.hub.eth.ip``: where the controller serves its web
    settings page.
    """

    max_shower_duration_minutes: int | None = None
    shower_max_temperature: int | None = None
    temperature_unit: str | None = None
    flow_rate_enabled: bool | None = None
    steam_default_temperature: int | None = None
    steam_default_time: int | None = None
    steam_max_temperature: int | None = None
    light_groups: tuple[str, ...] = ()
    lan_ip: str | None = None
    disconnected: tuple[str, ...] = ()

    @classmethod
    def from_configuration(cls, configuration: dict[str, Any]) -> HubSettings:
        """Read the settings from a ``hub-configuration`` ``configuration`` block."""
        configuration = configuration if isinstance(configuration, dict) else {}

        def block(name: str) -> dict[str, Any]:
            value = configuration.get(name)
            return value if isinstance(value, dict) else {}

        system = block("systemSettings")
        steam = block("steamSettings")
        amplifier = block("amplifierSettings")
        parts = block("parts")
        about = block("about")
        lights = configuration.get("lightSettings")
        lights = (
            [x for x in lights if isinstance(x, dict)]
            if isinstance(lights, list)
            else []
        )

        def serial(name: str) -> str | None:
            entry = about.get(name)
            if not isinstance(entry, dict):
                return None
            value = entry.get("serialNumber")
            return None if value in (None, "") else str(value)

        disconnected: list[str] = []
        for name in ("valve1", "valve2"):
            number = serial(name)
            if (
                number is not None
                and number != _NO_SERIAL
                and not _connected(parts.get(name))
            ):
                disconnected.append(name)
        steam_time = _int_or_none(steam.get("defaultTime"))
        if steam_time and not _connected(parts.get("steam")):
            disconnected.append("steam")
        amplifier_fitted = (
            amplifier.get("stereoVolume") is not None
            or amplifier.get("monoVolume") is not None
        )
        if amplifier_fitted and not _connected(parts.get("amplifier")):
            disconnected.append("amplifier")
        if lights and (
            not _connected(parts.get("light"))
            or any(str(x.get("connectivity")) == "No" for x in lights)
        ):
            disconnected.append("light")
        if amplifier_fitted:
            sd_card = str(amplifier.get("sdCard") or "").strip().lower()
            music = str(amplifier.get("music") or "").strip().lower()
            if sd_card == "notpresent":
                disconnected.append("sd_card")
            elif music in {"notpresent", "unknown"}:
                disconnected.append("sd_card_empty")

        flow = system.get("flowRateEnable")
        hub = about.get("hub")
        lan_ip = None
        if isinstance(hub, dict):
            for link in ("wlan", "eth"):
                details = hub.get(link)
                lan_ip = _ip_or_none(
                    details.get("ip") if isinstance(details, dict) else None
                )
                if lan_ip:
                    break
        return cls(
            max_shower_duration_minutes=_int_or_none(system.get("maxShowerDuration")),
            shower_max_temperature=_int_or_none(system.get("showerMaxTemperature")),
            temperature_unit=(
                str(system["temperatureUnit"])
                if system.get("temperatureUnit")
                else None
            ),
            flow_rate_enabled=None if flow is None else str(flow).strip() == "1",
            steam_default_temperature=_int_or_none(steam.get("defaultTemperature")),
            steam_default_time=steam_time,
            steam_max_temperature=_int_or_none(steam.get("maxTemperature")),
            light_groups=tuple(str(x.get("name")) for x in lights if x.get("name")),
            lan_ip=lan_ip,
            disconnected=tuple(disconnected),
        )

    @property
    def web_url(self) -> str | None:
        """The controller's web settings page, or None when its address is unknown."""
        if not self.lan_ip:
            return None
        host = f"[{self.lan_ip}]" if ":" in self.lan_ip else self.lan_ip
        return f"http://{host}/"

    @property
    def steam_ready(self) -> bool:
        """Whether the app would offer its "Steam start" card: a non-zero default time."""
        return bool(self.steam_default_time)


@dataclass(frozen=True)
class HubCapabilities:
    """Which accessories are attached, and so which favorite components mean anything.

    None of this affects activating a favorite, which takes only an id and a name.
    """

    water: bool = False
    music: bool = False
    light: bool = False
    steam: bool = False
    # Whether this came from a real read: a controller with no accessories is all-False
    # too, so the flags alone cannot tell "empty" from "unread".
    known: bool = False

    @classmethod
    def from_configuration(cls, configuration: dict[str, Any]) -> HubCapabilities:
        """Read capabilities from a ``hub-configuration`` ``configuration`` block.

        ``parts`` reports ``Connected`` / ``NotConnected`` / ``null`` per component,
        compared case-insensitively as the app does. ⚠️ ``parts.valve1``/``valve2`` count
        valve bodies, not zones: never gate zone-2 entities on ``parts.valve2``
        (`docs/protocol/hub_controller.md` §1).

        A block that is not an object, or names no parts at all, is a degraded reply, not
        a controller without accessories. It returns unread (``known=False``), so
        accessory entities are still created and the coordinator reads again.
        """
        if not isinstance(configuration, dict):
            return cls()
        parts = configuration.get("parts")
        if not isinstance(parts, dict) or not parts:
            return cls()

        def connected(*names: str) -> bool:
            return any(_connected(parts.get(name)) for name in names)

        return cls(
            water=connected("valve1", "valve2", "valveOne", "valveTwo"),
            music=connected("amplifier", "music"),
            light=connected("light", "lightBridge"),
            steam=connected("steam"),
            known=True,
        )

    def describe(self) -> str:
        """A short human summary of the attached accessories."""
        present = [n for n in ("water", "music", "light", "steam") if getattr(self, n)]
        return ", ".join(present) if present else "no accessories detected"


class HubDevice:
    """Command surface for one Anthem Plus system controller."""

    def __init__(
        self,
        client: KohlerClient,
        device_id: str,
        temperature_unit: str = "Fahrenheit",
    ) -> None:
        self._client = client
        self.device_id = device_id
        # The account's unit, used only to convert input: favorite temperatures are whole
        # °F on the wire whatever it is (`docs/protocol/hub_controller.md` §4).
        self.temperature_unit = temperature_unit

    def to_wire_temperature(self, temperature: float) -> int:
        """A temperature in the account's unit -> the whole °F a favorite carries."""
        if str(self.temperature_unit).strip().lower().startswith("c"):
            return 0 if int(temperature) == 0 else round(temperature * 1.8 + 32)
        return round(temperature)

    def _base(self) -> dict[str, Any]:
        return {
            "deviceId": self.device_id,
            "sku": SKU_HUB,
            "tenantId": self._client.tenant_id,
        }

    async def _async_base(self) -> dict[str, Any]:
        """`_base()`, signing in first if the tenant id is not known yet.

        Otherwise a command sent as the client's first call would carry ``tenantId: null``.
        """
        await self._client.async_tenant_id()
        return self._base()

    # ------------------------------------------------------------------ #
    # Direct control (the controller's own stored default)
    # ------------------------------------------------------------------ #
    async def async_set_shower(self, on: bool) -> Any:
        """Run or stop the controller's default shower configuration."""
        return await self._client.async_request(
            "POST",
            HUB_VALVE_CONTROL,
            json_body={**(await self._async_base()), "valveOnOff": ON if on else OFF},
        )

    async def async_set_steam(self, on: bool) -> Any:
        """Run or stop the controller's default steam configuration.

        Runs at ``steamSettings.defaultTemperature`` for ``defaultTime``; the body carries
        nothing else. The app refuses to start steam while the shower runs;
        ``KohlerKonnectCoordinator`` enforces the same guard.
        """
        return await self._client.async_request(
            "POST",
            HUB_STEAM_CONTROL,
            json_body={**(await self._async_base()), "steamOnOff": ON if on else OFF},
        )

    async def async_stop_all(self) -> Any:
        """Fully idle the system: the only true "off"."""
        return await self._client.async_request(
            "POST", HUB_STOP_ALL, json_body=await self._async_base()
        )

    # ------------------------------------------------------------------ #
    # Favorites
    # ------------------------------------------------------------------ #
    async def async_activate_favorite(
        self, favorite_id: Any, name: str, on: bool = True
    ) -> Any:
        """Start or stop a favorite. Allowed even while something else runs."""
        return await self._client.async_request(
            "POST",
            HUB_FAVORITE_CONTROL,
            json_body={
                **(await self._async_base()),
                # Control takes the id as a string; create, edit and delete as an int.
                "id": str(favorite_id),
                "name": name,
                "state": ON if on else OFF,
            },
        )

    async def async_create_favorite(
        self,
        name: str,
        *,
        zone1: dict[str, Any] | None = None,
        zone2: dict[str, Any] | None = None,
        steam: dict[str, Any] | None = None,
        music: dict[str, Any] | None = None,
        light: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Create a favorite. ``id: 0`` is what makes it a create, as the app sends it."""
        await self._client.async_tenant_id()
        body = self._favorite_body(
            name, zone1=zone1, zone2=zone2, steam=steam, music=music, light=light
        )
        body["id"] = 0
        return await self._client.async_request("POST", HUB_FAVORITE, json_body=body)

    async def async_edit_favorite(
        self,
        favorite_id: int,
        name: str,
        *,
        zone1: dict[str, Any] | None = None,
        zone2: dict[str, Any] | None = None,
        steam: dict[str, Any] | None = None,
        music: dict[str, Any] | None = None,
        light: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Edit a favorite.

        Raises :class:`~.client.DeviceRunning` if the system is active — stop it first.
        """
        await self._client.async_tenant_id()
        body = self._favorite_body(
            name, zone1=zone1, zone2=zone2, steam=steam, music=music, light=light
        )
        body["id"] = int(favorite_id)
        return await self._client.async_request("PATCH", HUB_FAVORITE, json_body=body)

    async def async_delete_favorite(self, favorite_id: int, name: str) -> Any:
        """Delete a favorite."""
        return await self._client.async_request(
            "DELETE",
            HUB_FAVORITE,
            json_body={
                **(await self._async_base()),
                "name": name,
                "id": int(favorite_id),
            },
        )

    def _favorite_body(
        self,
        name: str,
        *,
        zone1: dict[str, Any] | None,
        zone2: dict[str, Any] | None,
        steam: dict[str, Any] | None,
        music: dict[str, Any] | None,
        light: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Assemble a favorite body the way Konnect 3.0.6 does.

        Unused components are omitted, not nulled (`docs/protocol/hub_controller.md` §4):
        a zone only when it opens an outlet, ``water`` only with such a zone, ``steam``
        only when given, ``music`` only when not None, ``light`` only when non-empty.

        Raises ValueError for a favorite with both water and steam, which the app forbids
        and the controller's response to is untested.
        """
        water = {
            key: value
            for key, value in (("zone1", zone1), ("zone2", zone2))
            if value is not None and value.get("outlets")
        }
        if water and steam:
            raise ValueError(
                "A favorite cannot contain both shower and steam — the Konnect app forbids "
                "it, and what the controller would do with one is untested."
            )
        body: dict[str, Any] = {**self._base(), "name": name}
        if water:
            body["water"] = water
        if steam:
            body["steam"] = steam
        if music is not None:
            body["music"] = music
        if light:
            body["light"] = light
        return body

    @staticmethod
    def zone(
        temperature: int, outlets: list[bool], flowrate: int = 100
    ) -> dict[str, Any]:
        """Build a water zone for a favorite body.

        ``temperature`` is whole °F (convert with :meth:`to_wire_temperature`); the app
        bounds it to 59 °F through ``showerMaxTemperature``. ``flowrate`` is a percentage,
        10-100, editable only when ``flowRateEnable`` is ``"1"``. ``outlets`` are
        per-outlet flags, sent as 0-based positions.
        """
        return {
            "temperature": int(temperature),
            "flowrate": int(flowrate),
            "outlets": outlet_positions(outlets),
        }

    @staticmethod
    def zones_for(
        model: ValveModel,
        outlets: list[bool],
        temperature: int,
        flowrate: int = 100,
    ) -> dict[str, dict[str, Any] | None]:
        """Build both water zones from global outlet flags for this valve model.

        Pass one flag per physical outlet (four for a K-28211, six for a K-28212). A zone
        the model lacks comes back as ``None``; :meth:`_favorite_body` drops that and any
        zone with no outlet open.
        """
        zone1_flags, zone2_flags = model.split_outlets(outlets)
        zones: dict[str, dict[str, Any] | None] = {
            "zone1": HubDevice.zone(temperature, zone1_flags, flowrate)
        }
        zones["zone2"] = (
            HubDevice.zone(temperature, zone2_flags, flowrate)
            if model.uses_valve2
            else None
        )
        return zones

    @staticmethod
    def music(source: str, volume: int = 70) -> dict[str, Any]:
        """Build a music component. ``source`` is ``"Aux"`` or ``"SdCard"`` (no Bluetooth).

        ``volume`` is 0-100. ``songID`` and ``musicRepeat`` are sent empty, as the app does.
        """
        return {
            "source": source,
            "songID": "",
            "musicRepeat": "",
            "volume": int(volume),
        }

    # ------------------------------------------------------------------ #
    # Experiences
    # ------------------------------------------------------------------ #
    async def async_control_experience(
        self, title: str, category: str, on: bool = True
    ) -> Any:
        """Start or stop a firmware experience by its title (not its numeric id).

        ``category`` is the group the experience was listed under in the experiences read,
        and picks the endpoint; any other path does not work. Raises ValueError for an
        unknown category. Experiences always run on zone 1's first outlet; use a favorite
        for a specific outlet. Run state arrives as ``SHOWER_EXP_STS`` /
        ``STEAM_EXP_STS`` / ``ICE_SHOWER_EXP_STS``.
        """
        endpoint = EXPERIENCE_ENDPOINTS.get(category)
        if endpoint is None:
            raise ValueError(
                f"Unknown experience category {category!r}; expected one of "
                f"{sorted(EXPERIENCE_ENDPOINTS)}"
            )
        return await self._client.async_request(
            "POST",
            endpoint,
            json_body={
                **(await self._async_base()),
                "name": title,
                "status": ON if on else OFF,
            },
        )
