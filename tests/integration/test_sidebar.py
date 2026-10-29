"""The sidebar panel: when it is in the sidebar, and what it is told about the devices.

The account here holds one of everything — a two-zone valve, an Anthem Plus controller and a
faucet — so every role and every kind of descriptor is exercised. Home Assistant's web
server is set up before the entry, as it is at startup, so the panel's bundle can be served.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.kohler_konnect.const import (
    CONF_MAX_RUN_MINUTES,
    CONF_SIDEBAR_PANEL,
    CONF_VALVES,
    CONF_WARMUP_AUTO_RESTORE,
    CONF_ZONE_GROUPING,
    DEFAULT_MAX_RUN_MINUTES,
    DEFAULT_ZONE_GROUPING,
    DOMAIN,
    OUTLET_TYPE_NAMES,
    OUTLET_TYPE_VARIANTS,
)
from custom_components.kohler_konnect.konnect.usage import GALLONS_PER_LITRE
from custom_components.kohler_konnect.number import zone_temperature_range
from custom_components.kohler_konnect.sidebar import BUNDLE, STATIC_URL, URL_PATH
from custom_components.kohler_konnect.sidebar.roles import resolve_roles, role_for

from .conftest import (
    DEVICE_ID,
    FAUCET,
    USERNAME,
    VALVE,
    VALVE_ID,
    account,
    ha_device_id,
)
from .test_controller_entities import CONFIGURATION, EXPERIENCES, FAVORITES
from .test_presets import PRESETS
from .test_shower_entities import HUB, HUB_ID, ShowerKohler, setup

KINDS = {VALVE_ID: "valve", HUB_ID: "controller", DEVICE_ID: "faucet"}


class PanelKohler(ShowerKohler):
    """`ShowerKohler`, with the reads behind the panel's usage, About and Diagnostics.

    Each usage read returns the days or months between its `FromDate` and `ToDate`, summed
    from `used`: litres, seconds and switch-ons per device and day. The valve's
    `gcs-configuration/{id}/about` answers `about`, and its `gcs-diagnostics` `faults`.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.used: dict[str, dict[str, tuple[float, float, int]]] = {}
        self.about: dict[str, Any] | None = None
        self.faults: list[dict[str, Any]] = []

    def usage_reads(self) -> list[tuple[str, str, str, str]]:
        """Every usage read so far: endpoint, interval, first and last day."""
        return [
            (
                url.path.rsplit("/", 2)[-2],
                url.query["Interval"],
                url.query["FromDate"],
                url.query["ToDate"],
            )
            for _, url, _, _ in self.mocker.mock_calls
            if "-usage/" in url.path
        ]

    async def _api(self, method: str, url: Any, data: Any) -> Any:
        path = url.path
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if method.lower() == "get" and not queued:
            if "-usage/" in path:
                return self._respond(method, url, self._usage(url))
            if path.endswith(f"/gcs-configuration/{VALVE_ID}/about") and self.about:
                return self._respond(method, url, (200, copy.deepcopy(self.about)))
            if path.endswith(f"/gcs-diagnostics/{VALVE_ID}"):
                body = {"errorDetails": copy.deepcopy(self.faults)}
                return self._respond(method, url, (200, body))
        return await super()._api(method, url, data)

    def _usage(self, url: Any) -> tuple[int, dict[str, Any]]:
        first = date.fromisoformat(url.query["FromDate"])
        last = date.fromisoformat(url.query["ToDate"])
        width = 10 if url.query["Interval"] == "DAY" else 7
        buckets: dict[str, list[float]] = {}
        for day, figures in self.used.get(url.path.rsplit("/", 1)[-1], {}).items():
            if first <= date.fromisoformat(day) <= last:
                bucket = buckets.setdefault(day[:width], [0.0, 0.0, 0])
                for index, figure in enumerate(figures):
                    bucket[index] += figure
        if "/faucet-usage/" in url.path:
            rows = [
                {"intervalKey": key, "waterUsage": litres, "usageDuration": seconds}
                for key, (litres, seconds, _) in sorted(buckets.items())
            ]
            return 200, {"faucetUsageDataDetailsList": rows}
        rows = [
            {
                "intervalKey": key,
                "volume": litres,
                "onDuration": seconds,
                "numberOfTimesValveSwitchedOn": int(uses),
            }
            for key, (litres, seconds, uses) in sorted(buckets.items())
        ]
        return 200, {"gcsUsageDataDetailsList": rows}


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> PanelKohler:
    """One of everything: a K-28212 (two zones), an Anthem Plus and a Sensate."""
    fake = PanelKohler(aioclient_mock)
    fake.devices = [dict(VALVE), dict(HUB), dict(FAUCET)]
    fake.valve_outlets = (3, 3)
    fake.outlet_types = {0: 11, 1: 52, 2: 1, 3: 39, 4: 11, 5: 21}
    configuration = copy.deepcopy(CONFIGURATION)
    configuration["zonetwo"] = {"configuredoutlets": "3"}
    fake.hub_configuration = configuration
    fake.hub_favorites = copy.deepcopy(FAVORITES)
    fake.hub_experiences = copy.deepcopy(EXPERIENCES)
    fake.faucet_presets = [copy.deepcopy(PRESETS[0])]
    return fake


async def serve_http(hass: HomeAssistant) -> None:
    """Home Assistant's web server, ready before the entry as it is at startup."""
    assert await async_setup_component(hass, "http", {})


def panels(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.get(DATA_PANELS, {})


async def next_event(client: Any) -> dict[str, Any]:
    async with asyncio.timeout(5):
        message = await client.receive_json()
    assert message["type"] == "event", message
    return message["event"]


async def subscribe(client: Any) -> dict[str, Any]:
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe_devices"})
    async with asyncio.timeout(5):
        result = await client.receive_json()
    assert result["success"], result
    return await next_event(client)


def settle(hass: HomeAssistant) -> None:
    """Let a debounced re-send go."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))


# --------------------------------------------------------------------------- #
# In the sidebar, or not
# --------------------------------------------------------------------------- #


async def test_the_panel_is_in_the_sidebar_by_default(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)

    panel = panels(hass)[URL_PATH]
    assert panel.sidebar_title == "Kohler Konnect"
    assert not panel.require_admin
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "kohler-konnect-panel"
    # The URL carries the bundle's version, which the panel also gets to compare.
    assert custom["module_url"] == f"{STATIC_URL}?v={panel.config['version']}"


async def test_turning_the_option_off_takes_the_panel_out_without_a_reload(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    coordinator = account(hass, config_entry)

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SIDEBAR_PANEL: False}
    )
    await hass.async_block_till_done()
    assert URL_PATH not in panels(hass)
    # Same coordinator: the push stream never dropped.
    assert account(hass, config_entry) is coordinator

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SIDEBAR_PANEL: True}
    )
    await hass.async_block_till_done()
    assert URL_PATH in panels(hass)
    assert account(hass, config_entry) is coordinator


async def test_the_panel_stays_while_any_account_wants_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    # A second account, never set up: wanting the panel doesn't depend on loading.
    other = MockConfigEntry(
        domain=DOMAIN, title="Kohler Konnect (second)", unique_id="second", data={}
    )
    other.add_to_hass(hass)

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_SIDEBAR_PANEL: False}
    )
    await hass.async_block_till_done()
    assert URL_PATH in panels(hass)

    await hass.config_entries.async_remove(other.entry_id)
    await hass.async_block_till_done()
    assert URL_PATH not in panels(hass)


async def test_disabling_the_last_account_takes_the_panel_out(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)

    await hass.config_entries.async_set_disabled_by(
        config_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert URL_PATH not in panels(hass)


async def test_a_reload_keeps_the_panel_and_serves_the_bundle_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    await serve_http(hass)
    with patch.object(
        hass.http,
        "async_register_static_paths",
        wraps=hass.http.async_register_static_paths,
    ) as served:
        await setup(hass, config_entry)
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()

    assert URL_PATH in panels(hass)
    assert served.call_count == 1
    (config,) = served.call_args.args[0]
    assert config.url_path == STATIC_URL
    assert Path(config.path) == BUNDLE


async def test_no_web_server_means_no_panel(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """Without a server to serve its bundle, a panel would load nothing; none is added."""
    await setup(hass, config_entry)
    assert URL_PATH not in panels(hass)


async def test_a_missing_bundle_means_no_panel_and_says_so(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    await serve_http(hass)
    with patch(
        "custom_components.kohler_konnect.sidebar.BUNDLE", tmp_path / "missing.js"
    ):
        await setup(hass, config_entry)
    assert URL_PATH not in panels(hass)
    assert "bundle is missing" in caplog.text


# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #


async def test_every_entity_has_a_role(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: ShowerKohler
) -> None:
    """A new entity must be given a role in `sidebar/roles.py`, or the panel can't see it."""
    await setup(hass, config_entry)
    entities = er.async_entries_for_config_entry(
        er.async_get(hass), config_entry.entry_id
    )
    assert len(entities) > 80

    missing = []
    for entity in entities:
        kohler_id = next(k for k in KINDS if entity.unique_id.startswith(f"{k}_"))
        suffix = entity.unique_id.removeprefix(f"{kohler_id}_")
        if role_for(KINDS[kohler_id], entity.domain, suffix) is None:
            missing.append((KINDS[kohler_id], entity.domain, suffix))
    assert missing == []


# --------------------------------------------------------------------------- #
# What the panel is told
# --------------------------------------------------------------------------- #


async def test_the_panel_hears_about_every_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    event = await subscribe(await hass_ws_client(hass))

    assert event["entries"] == [
        {
            "entry_id": config_entry.entry_id,
            "title": f"Kohler Konnect ({USERNAME})",
            "sidebar_panel": True,
            "stream_connected": True,
        }
    ]
    devices = {d["kind"]: d for d in event["devices"]}
    assert set(devices) == {"valve", "controller", "faucet"}

    valve = devices["valve"]
    assert valve["id"] == ha_device_id(hass, VALVE_ID)
    assert valve["model"] == "K-28212"
    assert [z["zone"] for z in valve["zones"]] == [1, 2]
    assert valve["zones"][0]["outlets"][1] == {
        "position": 2,
        "code": 52,
        "name": OUTLET_TYPE_NAMES[52],
        "variant": OUTLET_TYPE_VARIANTS.get(52),
    }
    assert [o["code"] for o in valve["zones"][1]["outlets"]] == [39, 11, 21]
    for role in ("shower", "temperature:2", "outlet:2:3", "favorite", "water_today"):
        assert hass.states.get(valve["roles"][role]) is not None, role
    # Off by default, so the panel knows it exists but can't use it.
    assert "restart" in valve["disabled_roles"]
    assert "restart" not in valve["roles"]
    assert valve["units"] == {"temperature": "°F", "volume": "L"}
    # Beside a controller, Warmup Auto-Restore exists.
    assert valve["capabilities"] == {"usage": True, "warmup_auto_restore": False}

    controller = devices["controller"]
    assert controller["id"] == ha_device_id(hass, HUB_ID)
    assert controller["capabilities"]["steam"] is True
    assert controller["capabilities"]["web_url"].startswith("http://192.168.1.40")
    # Its water is the valve's, which reports it.
    assert "usage" not in controller["capabilities"]
    assert "outlet:1:1" in controller["roles"]

    faucet = devices["faucet"]
    assert faucet["id"] == ha_device_id(hass, DEVICE_ID)
    assert faucet["units"]["dispense"] == "mL"
    assert faucet["quick_amounts"][0] == {
        "key": "50ml",
        "amount": 50,
        "unit": "mL",
        "label": "50 mL",
    }
    assert faucet["capabilities"]["presets"] == [
        {"name": "Pasta pot", "amount": 3000, "unit": "mL"}
    ]
    assert faucet["capabilities"]["usage"] is True
    assert hass.states.get(faucet["roles"]["water"]) is not None


async def test_no_kohler_id_reaches_the_browser(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    """Kohler's device ids are cloud addresses; the panel addresses devices by HA's ids."""
    await serve_http(hass)
    await setup(hass, config_entry)
    text = json.dumps(await subscribe(await hass_ws_client(hass)))
    for kohler_id in KINDS:
        assert kohler_id not in text


async def test_a_non_admin_is_not_told_the_account_email_or_lan_address(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    event = await subscribe(await hass_ws_client(hass, hass_read_only_access_token))

    assert event["entries"][0]["title"] is None
    controller = next(d for d in event["devices"] if d["kind"] == "controller")
    assert "web_url" not in controller["capabilities"]


async def event_where(client: Any, hass: HomeAssistant, wanted: Any) -> dict[str, Any]:
    """The first event that satisfies ``wanted``, moving time on until one does."""
    for _ in range(10):
        settle(hass)
        await hass.async_block_till_done()
        event = await next_event(client)
        if wanted(event):
            return event
    pytest.fail("no matching event")


async def subscribe_device(client: Any, device_id: str) -> dict[str, Any]:
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/subscribe_device", "device_id": device_id}
    )
    async with asyncio.timeout(5):
        result = await client.receive_json()
    assert result["success"], result
    return await next_event(client)


async def test_a_valve_screen_is_told_simple_starts_shower_and_how_warm_to_stage(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    kohler.valve_outlets = (3, 0)
    await serve_http(hass)
    await setup(hass, config_entry)
    valve_id = ha_device_id(hass, VALVE_ID)
    live = await subscribe_device(await hass_ws_client(hass), valve_id)

    assert live["device_id"] == valve_id
    assert live["stream_connected"] is True
    # Preset 1: the rainhead at 38.8 °C, which the Temperature number shows as 102 °F.
    assert live["default_shower"] == {
        "zones": [{"zone": 1, "outlets": [1], "temperature": 102}],
        "refused_because": None,
    }
    # In the account's °F, as `custom_shower` takes them, though this Home Assistant is
    # metric and shows the Temperature number in °C. Staging starts above the Cold step,
    # and tops out at the valve's scald limit.
    valve = account(hass, config_entry).valves[0]
    floor, ceiling = zone_temperature_range(valve, 1, "Fahrenheit")
    assert (floor, live["staging_bounds"]) == (
        59,
        [{"zone": 1, "min": 59, "max": ceiling}],
    )
    temperature = er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{VALVE_ID}_temperature_zone_1"
    )
    shown = hass.states.get(temperature).attributes
    assert shown["unit_of_measurement"] == "°C"
    assert shown["max"] == pytest.approx((ceiling - 32) / 1.8, abs=0.1)


async def test_a_faucet_screen_hears_when_its_water_turns_itself_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    live = await subscribe_device(client, ha_device_id(hass, DEVICE_ID))
    assert live["auto_off_at"] is None
    assert live["cloud_connected"] is True

    water = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{DEVICE_ID}_water"
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": water}, blocking=True
    )
    live = await event_where(client, hass, lambda e: e["auto_off_at"] is not None)
    assert dt_util.parse_datetime(live["auto_off_at"]) > dt_util.utcnow()


async def test_an_unknown_device_is_refused(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/subscribe_device", "device_id": "not-a-device"}
    )
    async with asyncio.timeout(5):
        result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "not_found"


async def test_the_panel_hears_a_rename_and_the_option_going_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: ShowerKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    await subscribe(client)

    area = ar.async_get(hass).async_create("Primary Bath")
    dr.async_get(hass).async_update_device(
        ha_device_id(hass, VALVE_ID), name_by_user="Rain Shower", area_id=area.id
    )
    settle(hass)
    event = await next_event(client)
    valve = next(d for d in event["devices"] if d["kind"] == "valve")
    assert (valve["name"], valve["area"]) == ("Rain Shower", "Primary Bath")

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_SIDEBAR_PANEL: False}
    )
    await hass.async_block_till_done()
    settle(hass)
    event = await next_event(client)
    assert event["entries"][0]["sidebar_panel"] is False
    assert event["devices"] == []


# --------------------------------------------------------------------------- #
# Water usage
# --------------------------------------------------------------------------- #

USED = {
    "2025-03-10": (55.0, 420.0, 1),
    "2026-07-04": (60.0, 480.0, 1),
    "2026-07-20": (40.0, 300.0, 1),
    "2026-09-29": (30.0, 240.0, 1),
    "2026-10-05": (50.0, 600.0, 2),
    "2026-10-26": (45.5, 540.0, 1),
    "2026-10-28": (20.0, 300.0, 1),
    # This evening's shower, which Kohler files under its UTC date.
    "2026-10-29": (10.0, 120.0, 1),
}


@pytest.fixture
async def evening(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """8 pm on Wednesday 28 October 2026 in Los Angeles: already the 29th in UTC.

    Ask for it before `hass_ws_client`, whose sign-in token must be issued at this time.
    """
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-28T20:00:00-07:00")


async def usage_history(
    client: Any, device_id: str, period: str, **fields: Any
) -> dict[str, Any]:
    """The whole reply, so a test can check a refusal too."""
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/usage_history",
            "device_id": device_id,
            "period": period,
            **fields,
        }
    )
    async with asyncio.timeout(5):
        return await client.receive_json()


async def charted(client: Any, device_id: str, period: str, **fields: Any) -> Any:
    reply = await usage_history(client, device_id, period, **fields)
    assert reply["success"], reply
    return reply["result"]


def by_key(history: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {bucket["key"]: bucket for bucket in history["buckets"]}


async def test_this_month_is_charted_from_what_the_sensors_read(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    reads = len(kohler.usage_reads())

    history = await charted(client, ha_device_id(hass, VALVE_ID), "month")

    # Nothing read: the integration holds this month already.
    assert len(kohler.usage_reads()) == reads
    assert (history["start"], history["end"], history["unit"]) == (
        "2026-10-01",
        "2026-10-31",
        "L",
    )
    days = by_key(history)
    assert len(days) == 31
    # This evening's shower is today's, though Kohler filed it under tomorrow.
    assert days["2026-10-28"] == {
        "key": "2026-10-28",
        "volume": 30.0,
        "minutes": 7.0,
        "uses": 2,
    }
    assert days["2026-10-29"]["volume"] is None
    assert days["2026-10-04"] == {
        "key": "2026-10-04",
        "volume": 0.0,
        "minutes": 0.0,
        "uses": None,
    }
    roles = resolve_roles(hass, config_entry.entry_id, "valve", VALVE_ID).enabled
    month = hass.states.get(roles["water_month"])
    assert history["total"]["volume"] == float(month.state) == 125.5
    minutes = hass.states.get(roles["duration_month"])
    assert history["total"]["minutes"] == float(minutes.state) == 26.0
    assert history["high"] == {"volume": 50.0, "minutes": 10.0}
    # Per day, over the 28 days so far.
    assert history["average"] == {"volume": 4.48, "minutes": 0.9}
    assert (history["previous"], history["next"]) == ("2026-09-30", None)


async def test_an_earlier_month_is_read_from_kohler_once(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    valve = ha_device_id(hass, VALVE_ID)
    reads = len(kohler.usage_reads())

    july = await charted(client, valve, "month", anchor="2026-07-15")
    assert kohler.usage_reads()[reads:] == [
        ("gcs-usage", "DAY", "2026-07-01", "2026-07-31")
    ]
    assert [
        (key, bucket["volume"])
        for key, bucket in by_key(july).items()
        if bucket["volume"]
    ] == [("2026-07-04", 60.0), ("2026-07-20", 40.0)]
    assert july["total"]["volume"] == 100.0
    assert (july["previous"], july["next"]) == ("2026-06-30", "2026-08-01")

    # Long over, so kept: the same month again is not read again.
    assert await charted(client, valve, "month", anchor="2026-07-02") == {
        **july,
        "anchor": "2026-07-02",
    }
    assert len(kohler.usage_reads()) == reads + 1


async def test_a_failed_read_is_not_kept(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    valve = ha_device_id(hass, VALVE_ID)
    reads = len(kohler.usage_reads())

    kohler.fail_api(f"/gcs-usage/{VALVE_ID}", (500, {"message": "unavailable"}))
    failed = await usage_history(client, valve, "month", anchor="2026-07-15")
    assert not failed["success"]
    assert failed["error"]["code"] == "home_assistant_error"

    july = await charted(client, valve, "month", anchor="2026-07-15")
    assert july["total"]["volume"] == 100.0
    assert len(kohler.usage_reads()) == reads + 2


async def test_a_year_is_charted_month_by_month(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    valve = ha_device_id(hass, VALVE_ID)
    reads = len(kohler.usage_reads())

    year = await charted(client, valve, "year")
    assert len(kohler.usage_reads()) == reads
    months = by_key(year)
    assert list(months) == [f"2026-{month:02d}" for month in range(1, 13)]
    assert [months[key]["volume"] for key in ("2026-07", "2026-09", "2026-10")] == [
        100.0,
        30.0,
        125.5,
    ]
    assert months["2026-11"]["volume"] is None
    roles = resolve_roles(hass, config_entry.entry_id, "valve", VALVE_ID).enabled
    assert year["total"]["volume"] == float(hass.states.get(roles["water_year"]).state)
    assert year["total"]["volume"] == 255.5

    last_year = await charted(client, valve, "year", anchor="2025-06-01")
    assert kohler.usage_reads()[reads:] == [
        ("gcs-usage", "MONTH", "2025-01-01", "2025-12-31")
    ]
    assert by_key(last_year)["2025-03"]["volume"] == 55.0
    assert last_year["total"]["volume"] == 55.0
    assert None not in [bucket["volume"] for bucket in last_year["buckets"]]
    assert last_year["next"] == "2026-01-01"


async def test_a_week_starts_on_the_first_weekday_asked_for(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    valve = ha_device_id(hass, VALVE_ID)

    sunday = await charted(client, valve, "week")
    assert (sunday["start"], sunday["end"]) == ("2026-10-25", "2026-10-31")
    assert sunday["total"]["volume"] == 75.5

    monday = await charted(client, valve, "week", week_start=0)
    assert (monday["start"], monday["end"]) == ("2026-10-26", "2026-11-01")
    assert monday["buckets"][0]["volume"] == 45.5
    assert monday["total"]["volume"] == 75.5


async def test_paging_stays_between_the_valves_registration_and_today(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    kohler.gcs_configuration = {"createdTime": "2026-07-10T15:00:00Z"}
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    valve = ha_device_id(hass, VALVE_ID)

    july = await charted(client, valve, "month", anchor="2026-07-15")
    assert (july["previous"], july["next"]) == (None, "2026-08-01")
    # Per day since registration: 100 L over 10 to 31 July.
    assert july["average"]["volume"] == round(100 / 22, 2)

    before = await charted(client, valve, "month", anchor="2020-01-01")
    assert (before["anchor"], before["start"]) == ("2026-07-10", "2026-07-01")
    after = await charted(client, valve, "month", anchor="2027-03-01")
    assert (after["anchor"], after["start"]) == ("2026-10-28", "2026-10-01")


async def test_a_us_account_is_charted_in_gallons(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[VALVE_ID] = dict(USED)
    kohler.water_units = "Standard"
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)

    history = await charted(client, ha_device_id(hass, VALVE_ID), "month")
    assert history["unit"] == "gal"
    assert by_key(history)["2026-10-28"]["volume"] == round(30 * GALLONS_PER_LITRE, 2)
    assert history["total"]["volume"] == round(125.5 * GALLONS_PER_LITRE, 1)


async def test_a_faucet_charts_its_own_usage(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.used[DEVICE_ID] = {
        "2026-10-27": (2.5, 90.0, 0),
        "2026-06-02": (1.0, 30.0, 0),
    }
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    faucet_id = ha_device_id(hass, DEVICE_ID)

    week = await charted(client, faucet_id, "week")
    assert by_key(week)["2026-10-27"] == {
        "key": "2026-10-27",
        "volume": 2.5,
        "minutes": 1.5,
        "uses": None,
    }
    assert week["total"]["volume"] == 2.5

    reads = len(kohler.usage_reads())
    june = await charted(client, faucet_id, "month", anchor="2026-06-10")
    assert kohler.usage_reads()[reads:] == [
        ("faucet-usage", "DAY", "2026-06-01", "2026-06-30")
    ]
    assert june["total"]["volume"] == 1.0


async def test_usage_is_refused_for_a_controller_or_an_unknown_device(
    hass: HomeAssistant,
    evening: None,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)

    controller = await usage_history(client, ha_device_id(hass, HUB_ID), "month")
    assert controller["error"]["code"] == "not_supported"
    unknown = await usage_history(client, "not-a-device", "month")
    assert unknown["error"]["code"] == "not_found"
    fortnight = await usage_history(client, ha_device_id(hass, VALVE_ID), "fortnight")
    assert fortnight["error"]["code"] == "invalid_format"


# --------------------------------------------------------------------------- #
# Settings, About, Diagnostics and Account: administrators only
# --------------------------------------------------------------------------- #

ABOUT = {
    "gatewayConfigInfo": {
        "firmware": "3.4.2",
        "installDate": "2024-03-11",
        "model": "GW-1",
        "serialNo": "GW-SERIAL",
    },
    "valvesConfigInfo": [
        {
            "firmware": "1.28",
            "model": "K-28212",
            "name": "Valve 1",
            "serialNo": "V1-SERIAL",
            "status": "Connected",
        },
        {
            "firmware": "1.29",
            "model": "K-28212",
            "name": "Valve 2",
            "serialNo": "V2-SERIAL",
            "status": "Connected",
        },
    ],
    "interfacesConfigInfo": [
        {"firmware": "2.11", "name": "Anthem Interface", "status": "Connected"}
    ],
}


async def command(client: Any, name: str, **fields: Any) -> dict[str, Any]:
    """The whole reply to one of the panel's commands."""
    await client.send_json_auto_id({"type": f"{DOMAIN}/{name}", **fields})
    async with asyncio.timeout(5):
        return await client.receive_json()


async def details_of(client: Any, device_id: str) -> dict[str, Any]:
    reply = await command(client, "device_details", device_id=device_id)
    assert reply["success"], reply
    return reply["result"]


async def test_about_shows_a_valves_parts_firmware_and_faults(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.about = copy.deepcopy(ABOUT)
    kohler.gcs_configuration = {
        "createdTime": "2024-03-11T14:22:31Z",
        "configuration": {
            "about": {
                "uI2": {"firmware": "2.11"},
                "primaryValve": {"firmware": "1.28"},
                "secondaryValve1": {"firmware": "1.29"},
                "gateway": {"firmware": "3.4.2"},
            }
        },
    }
    kohler.faults = [
        {
            "deviceId": VALVE_ID,
            "errorCode": "37",
            "isActive": True,
            "area": "Algorithm",
            "valveId": "Valve 1",
            "timestamp": "2026-04-21T09:12:00Z",
        }
    ]
    await serve_http(hass)
    await setup(hass, config_entry)
    valve = ha_device_id(hass, VALVE_ID)

    details = await details_of(await hass_ws_client(hass), valve)

    assert (details["model"], details["install_date"], details["registered"]) == (
        "K-28212",
        "2024-03-11",
        "2024-03-11T14:22:31+00:00",
    )
    assert details["firmware"] == [
        {"key": "interface", "version": "2.11"},
        {"key": "valve", "version": "1.28"},
        {"key": "second_valve", "version": "1.29"},
        {"key": "gateway", "version": "3.4.2"},
    ]
    assert [(p["kind"], p["name"], p["serial"]) for p in details["parts"]] == [
        ("gateway", None, "GW-SERIAL"),
        ("valve", "Valve 1", "V1-SERIAL"),
        ("valve", "Valve 2", "V2-SERIAL"),
        ("interface", "Anthem Interface", None),
    ]
    assert details["faults"] == [
        {
            "code": "37",
            "title": None,
            "area": "Algorithm",
            "time": "2026-04-21T09:12:00Z",
        }
    ]
    assert details["diagnostics_path"] == (
        f"/api/diagnostics/config_entry/{config_entry.entry_id}/device/{valve}"
    )
    assert VALVE_ID not in json.dumps(details)


async def test_about_shows_a_controllers_firmware_errors_and_web_page(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.firmware_parts["hub"] = {"currentFirmware": "2.88"}
    kohler.hub_errors = [
        {
            "errorCode": "E12",
            "title": "Steam generator fault",
            "component": "Steam",
            "timeStamp": "2026-10-01T08:00:00Z",
            "isActive": True,
        }
    ]
    await serve_http(hass)
    await setup(hass, config_entry)

    details = await details_of(await hass_ws_client(hass), ha_device_id(hass, HUB_ID))

    assert details["firmware"] == [{"key": "controller", "version": "2.88"}]
    assert details["faults"] == [
        {
            "code": "E12",
            "title": "Steam generator fault",
            "area": "Steam",
            "time": "2026-10-01T08:00:00Z",
        }
    ]
    assert details["web_url"].startswith("http://192.168.1.40")


async def test_about_shows_a_faucets_firmware_and_leaks(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    about = kohler.config["configuration"]["about"]
    about["installedDate"] = "2024-11-30"
    about["firmware"] = {
        "version": "16.0",
        "latestVersion": "16.0",
        "application": {"version": "4.0"},
        "certificate": "3.0",
    }
    kohler.config["leakDetectionHistory"] = [
        {"leakDetectionTime": 1766180400000},
        {"leakDetectionTime": 1772435640},
    ]
    await serve_http(hass)
    await setup(hass, config_entry)

    details = await details_of(
        await hass_ws_client(hass), ha_device_id(hass, DEVICE_ID)
    )

    assert (details["serial"], details["hardware"], details["install_date"]) == (
        "SN-SECRET-1",
        "CC3235SF",
        "2024-11-30",
    )
    assert [f for f in details["firmware"] if f["version"]] == [
        {"key": "gateway", "version": "16.0"},
        {"key": "application", "version": "4.0"},
        {"key": "certificate", "version": "3.0"},
    ]
    # Newest first.
    assert details["leak_history"] == [
        "2026-03-02T07:14:00+00:00",
        "2025-12-19T21:40:00+00:00",
    ]


async def test_settings_about_and_account_are_for_administrators(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    valve = ha_device_id(hass, VALVE_ID)
    entry = config_entry.entry_id

    for name, fields in (
        ("device_details", {"device_id": valve}),
        ("restart_valve", {"device_id": valve}),
        ("set_warmup_auto_restore", {"device_id": valve, "enabled": True}),
        ("get_options", {"entry_id": entry}),
        ("update_options", {"entry_id": entry, "changes": {"sidebar_panel": False}}),
    ):
        reply = await command(client, name, **fields)
        assert reply["error"]["code"] == "unauthorized", name

    assert kohler.sent("gcs/valvereset") == []
    assert CONF_SIDEBAR_PANEL not in config_entry.options
    assert CONF_VALVES not in config_entry.options


async def test_restart_reboots_a_valve_and_nothing_else(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)

    reply = await command(
        client, "restart_valve", device_id=ha_device_id(hass, VALVE_ID)
    )
    assert reply["success"], reply
    assert [body["reset"] for body in kohler.sent("gcs/valvereset")] == [
        "productRestart"
    ]

    faucet_reply = await command(
        client, "restart_valve", device_id=ha_device_id(hass, DEVICE_ID)
    )
    assert faucet_reply["error"]["code"] == "not_supported"
    assert len(kohler.sent("gcs/valvereset")) == 1


async def test_warmup_auto_restore_is_set_even_with_its_switch_disabled(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    coordinator = account(hass, config_entry)
    switch = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{VALVE_ID}_warmup_auto_restore"
    )
    assert er.async_get(hass).async_get(switch).disabled_by is not None

    reply = await command(
        client,
        "set_warmup_auto_restore",
        device_id=ha_device_id(hass, VALVE_ID),
        enabled=True,
    )
    assert reply["success"], reply
    await hass.async_block_till_done()
    options = config_entry.options[CONF_VALVES][VALVE_ID]
    assert options[CONF_WARMUP_AUTO_RESTORE] is True
    # Read live: no reload.
    assert account(hass, config_entry) is coordinator
    assert coordinator.valves[0].warmup_auto_restore is True

    faucet = await command(
        client,
        "set_warmup_auto_restore",
        device_id=ha_device_id(hass, DEVICE_ID),
        enabled=True,
    )
    assert faucet["error"]["code"] == "not_supported"


async def test_warmup_auto_restore_needs_a_controller(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    kohler.devices = [dict(VALVE)]
    kohler.valve_outlets = (3, 0)
    await serve_http(hass)
    await setup(hass, config_entry)

    reply = await command(
        await hass_ws_client(hass),
        "set_warmup_auto_restore",
        device_id=ha_device_id(hass, VALVE_ID),
        enabled=True,
    )
    assert reply["error"]["code"] == "not_supported"
    assert CONF_VALVES not in config_entry.options


async def test_the_account_screen_offers_what_configure_does(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)

    reply = await command(
        await hass_ws_client(hass), "get_options", entry_id=config_entry.entry_id
    )
    assert reply["success"], reply
    options = reply["result"]
    assert options["title"] == f"Kohler Konnect ({USERNAME})"
    assert options["fields"] == [
        {
            "key": CONF_ZONE_GROUPING,
            "reloads": True,
            "kind": "select",
            "options": ["subdevices", "outlet_labels", "numbered"],
        },
        {
            "key": CONF_MAX_RUN_MINUTES,
            "reloads": True,
            "kind": "minutes",
            "min": 0,
            "max": 120,
        },
        {"key": CONF_SIDEBAR_PANEL, "reloads": False, "kind": "toggle"},
    ]
    assert options["values"] == {
        CONF_ZONE_GROUPING: DEFAULT_ZONE_GROUPING,
        CONF_MAX_RUN_MINUTES: DEFAULT_MAX_RUN_MINUTES,
        CONF_SIDEBAR_PANEL: True,
    }


async def test_saving_an_option_read_live_reloads_nothing(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    coordinator = account(hass, config_entry)

    reply = await command(
        await hass_ws_client(hass),
        "update_options",
        entry_id=config_entry.entry_id,
        changes={CONF_SIDEBAR_PANEL: False},
    )
    assert reply["success"], reply
    assert reply["result"]["reloads"] is False
    assert reply["result"]["values"][CONF_SIDEBAR_PANEL] is False
    await hass.async_block_till_done()
    assert account(hass, config_entry) is coordinator
    assert URL_PATH not in panels(hass)
    # Only what changed is written: an untouched option written out as its default would
    # count as a change, and reload.
    assert dict(config_entry.options) == {CONF_SIDEBAR_PANEL: False}


async def test_saving_the_safety_limit_reloads_as_configure_does(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    coordinator = account(hass, config_entry)

    reply = await command(
        await hass_ws_client(hass),
        "update_options",
        entry_id=config_entry.entry_id,
        changes={CONF_MAX_RUN_MINUTES: 30},
    )
    assert reply["success"], reply
    assert reply["result"]["reloads"] is True
    assert config_entry.options[CONF_MAX_RUN_MINUTES] == 30
    assert isinstance(config_entry.options[CONF_MAX_RUN_MINUTES], int)
    await hass.async_block_till_done()
    assert account(hass, config_entry) is not coordinator


async def test_an_option_configure_would_refuse_is_refused(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: PanelKohler,
    hass_ws_client: Any,
) -> None:
    await serve_http(hass)
    await setup(hass, config_entry)
    client = await hass_ws_client(hass)

    for changes in (
        {CONF_ZONE_GROUPING: "sideways"},
        {CONF_MAX_RUN_MINUTES: 500},
        {"colour": "teal"},
    ):
        reply = await command(
            client, "update_options", entry_id=config_entry.entry_id, changes=changes
        )
        assert reply["error"]["code"] == "invalid_format", changes
    unknown = await command(client, "get_options", entry_id="not-an-entry")
    assert unknown["error"]["code"] == "not_found"
    assert dict(config_entry.options) == {}
