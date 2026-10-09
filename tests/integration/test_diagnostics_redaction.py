"""Diagnostics redaction across a whole account: two valves, a controller and two faucets.

The report is written to be attached to public GitHub issues, and Kohler device ids and
serials double as cloud addresses. So every reply the fake cloud gives here is salted with
the identity a real one echoes — device ids under `id` and `deviceId`, serials, the tenant,
a LAN address, an IoT connection string, a signed firmware URL — and the downloaded file is
searched for every one of them, in values **and** in keys (a valve id used as a dictionary
key is how one leaked before).

The rest checks the other half of the contract: the report describes the whole
installation whichever button was pressed, and only `requested_for` and the singular
`valve` / `controller` blocks follow the button.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    _get_diagnostics_for_config_entry,
    _get_diagnostics_for_device,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.kohler_konnect import diagnostics
from custom_components.kohler_konnect.const import (
    CONF_VALVES,
    DOMAIN,
    FAUCET_SCAN_INTERVAL_IDLE,
)

from .conftest import (
    BAR,
    DEVICE_ID,
    FAUCET,
    MOBILE_ID,
    TENANT_ID,
    USERNAME,
    VALVE,
    VALVE_ID,
    FakeKohler,
    account,
    registered_device,
    unload,
    wait_for,
)

VALVE_2_ID = "gcs-shower0002"
HUB_ID = "hub-ctrl000001"
BAR_ID = BAR["deviceId"]
# Still keyed in the entry's per-valve options, long after it left the account.
REMOVED_VALVE_ID = "gcs-removed0009"
SAS_KEY = "c2VjcmV0LWtleS0x"
FIRMWARE_SIG = "fw-sig-0f9e8d7c"
LAN_IP = "192.168.1.50"
MAC = "aa:bb:cc:dd:ee:ff"

# Per valve: (serial on the account, gateway serial, valve-body serial, valve firmware,
# the fault it reports). Distinct on purpose, so a report that mixes them up shows it.
VALVES = {
    VALVE_ID: ("VALVE-SN-0001", "GW-SN-0001", "PV-SN-0001", "10", "E3"),
    VALVE_2_ID: ("VALVE-SN-0002", "GW-SN-0002", "PV-SN-0002", "11", "E7"),
}

#: Everything that must never appear in a downloaded report, matched case-insensitively.
SECRETS = (
    USERNAME,
    TENANT_ID,
    MOBILE_ID,
    "refresh-",  # every refresh token the fake issues, and the entry's original one
    VALVE_ID,
    VALVE_2_ID,
    REMOVED_VALVE_ID,
    HUB_ID,
    DEVICE_ID,
    BAR_ID,
    *(serial for row in VALVES.values() for serial in row[:3]),
    "UI-SN-",
    "HUB-SN-0001",
    "HUB-VALVE-SN-1",
    "KITCHEN-SN-0001",
    "BAR-SN-0001",
    "SN-SECRET-1",
    "SN-SECRET-BAR",
    # The live IoT Hub session: password, user and host.
    "SharedAccessSignature",
    "hub.example.net",
    SAS_KEY,
    FIRMWARE_SIG,
    "azure-devices.net",
    LAN_IP,
    MAC,
    "1 Main St",
    # The owner's own words.
    "Kids Bath",
    "Morning Routine",
)


def _leaks(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Every (secret, where) found in a JSON document, keys included."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaks(str(key), f"{path}.<key {key}>")
            yield from _leaks(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _leaks(item, f"{path}[{index}]")
    elif isinstance(node, str):
        lowered = node.lower()
        for secret in SECRETS:
            if secret.lower() in lowered:
                yield secret, path


def assert_clean(document: Any) -> None:
    found = list(_leaks(document))
    assert not found, "identity leaked into the report:\n" + "\n".join(
        f"  {secret!r} at {where}" for secret, where in found
    )


# --------------------------------------------------------------------------- #
# The fake cloud, extended to valves and a controller
# --------------------------------------------------------------------------- #
def _valve_index(device_id: str) -> int:
    return list(VALVES).index(device_id)


class AccountKohler(FakeKohler):
    """`FakeKohler` with the valve and controller endpoints answered, as Kohler shapes them.

    Every reply repeats the identity Kohler's own replies carry. Paths the base fake knows
    (and anything queued with `fail_api`) are left to it.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        super().__init__(mocker)
        self.config["configuration"]["about"]["serialNumber"] = "SN-SECRET-1"
        self._routes = [
            (r"/gcs-state/(gcs-[^/]+)$", self._gcs_state),
            (r"/gcs-configuration/(gcs-[^/]+)/about$", self._gcs_about),
            (r"/gcs-configuration/(gcs-[^/]+)$", self._gcs_configuration),
            (r"/gcs-diagnostics/(gcs-[^/]+)$", self._gcs_diagnostics),
            (r"/gcs-preset/(gcs-[^/]+)$", self._gcs_presets),
            (r"/firmware/gcs/gateway/([^/]+)$", self._firmware),
            (r"/firmware/gcs/([^/]+)$", self._firmware),
            (r"/firmware/hub/([^/]+)$", self._firmware),
            (r"/hub-configuration/([^/]+)$", self._hub_configuration),
            (r"/hub-diagnostics/([^/]+)/active$", self._hub_errors),
            (r"/hub-experience/([^/]+)/favorites$", self._hub_favorites),
            (r"/faucet-configuration/([^/]+)$", self._faucet_configuration),
        ]

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        queued = any(path.endswith(s) and q for s, q in self.api_queue.items())
        if method.lower() == "get" and not queued:
            for pattern, handler in self._routes:
                if match := re.search(pattern, path):
                    return self._respond(method, url, (200, handler(match.group(1))))
        return await super()._api(method, url, data)

    # Valves ---------------------------------------------------------------- #
    def _gcs_state(self, device_id: str) -> dict[str, Any]:
        return {
            "deviceId": device_id,
            "tenantId": TENANT_ID,
            "sku": "GCS",
            "connectionState": "Connected",
            "lastConnected": "2026-10-06T12:00:00Z",
            "state": {
                "valve1": {
                    "out1": "0",
                    "temperatureSetpoint": 38.5,
                    "flowSetpoint": 50,
                    "pauseFlag": "0",
                }
            },
        }

    def _gcs_configuration(self, device_id: str) -> dict[str, Any]:
        account_serial, gateway_serial, _, firmware, _ = VALVES[device_id]
        return {
            "id": device_id,
            "deviceId": device_id,
            "tenantId": TENANT_ID,
            "serialNumber": account_serial,
            "createdTime": "2024-03-11T14:22:31Z",
            "updatedTimestamp": "2026-09-10T08:00:00Z",
            "iot": {
                "connectionString": (
                    f"HostName=kohler.azure-devices.net;DeviceId={device_id};"
                    f"SharedAccessKey={SAS_KEY}"
                ),
                "hubName": "kohler.azure-devices.net",
                "firmwareVersion": "00.74",
                # A version-shaped key holding a device id: copied, it would leak.
                "deviceVersionId": device_id,
            },
            "configuration": {
                "about": {
                    "uI2": {"firmware": "2.2", "assetsFirmware": "2.0"},
                    "primaryValve": {"firmware": firmware},
                    "gateway": {"firmware": "00.74", "serialNo": gateway_serial},
                }
            },
            "otaReportedProperties": {
                "firmwareType": "Application",
                "initialVersion": "2.20",
                "updatedVersion": "2.20",
            },
            "firmwareUpdate": {
                "firmwareType": "Assets",
                "version": "2.00",
                "status": "Completed",
            },
            "zoneone": None,
            "systemSettings": None,
        }

    def _gcs_about(self, device_id: str) -> dict[str, Any]:
        _, gateway_serial, valve_serial, firmware, _ = VALVES[device_id]
        n = _valve_index(device_id)
        return {
            "deviceId": device_id,
            "gatewayConfigInfo": {
                "model": "GW-1",
                "firmware": "00.74",
                "installDate": "2024-03-11",
                "serialNo": gateway_serial,
            },
            "valvesConfigInfo": [
                {
                    "model": "K-28210",
                    "name": "Valve 1",
                    "firmware": firmware,
                    "status": "Connected",
                    "serialNo": valve_serial,
                }
            ],
            "interfacesConfigInfo": [
                {"name": "UI", "firmware": "2.2", "status": "Connected"},
                {"name": "UI2", "serialNo": f"UI-SN-{n}"},
            ],
        }

    def _gcs_diagnostics(self, device_id: str) -> dict[str, Any]:
        code = VALVES[device_id][4]
        return {
            "deviceId": device_id,
            "id": f"diag-{device_id}",
            "sku": "GCS",
            "tenantId": TENANT_ID,
            "errorDetails": [
                {
                    "id": f"err-{device_id}",
                    "type": "Fault",
                    "errorCode": code,
                    "title": "Low inlet pressure",
                    "description": "Check the supply valves.",
                    "details": f"Raised by {device_id} for tenant {TENANT_ID}",
                    "errorState": "1",
                    "isActive": True,
                    "timestamp": "2026-10-01T00:00:00Z",
                    "valveId": "1",
                    "component": "valve",
                    "area": "inlet",
                },
                # The app hides `errorCode "0"`; so must the report.
                {"errorCode": "0", "title": "No fault", "details": device_id},
            ],
        }

    def _gcs_presets(self, device_id: str) -> dict[str, Any]:
        return {
            "gcsPresetExperienceDetails": [
                {
                    "presetId": "3",
                    "title": "Kids Bath",
                    "isExperience": "False",
                    "deviceId": device_id,
                }
            ]
        }

    def _firmware(self, device_id: str) -> dict[str, Any]:
        return {
            "deviceId": device_id,
            "currentFirmware": "10",
            "firmware": "12",
            "firmwareUpdateAvailable": True,
            "mandatoryUpdate": False,
            "otaStatus": "NotStarted",
            # The image's download address, signed. Never part of a report.
            "url": f"https://fw.blob.core.windows.net/{device_id}.bin?sv=1&sig={FIRMWARE_SIG}",
        }

    # Controller ------------------------------------------------------------ #
    def _hub_configuration(self, device_id: str) -> dict[str, Any]:
        return {
            "deviceId": device_id,
            "tenantId": TENANT_ID,
            "configuration": {
                "parts": {"valve1": "Connected", "steam": "Connected"},
                "about": {
                    "hub": {
                        "serialNumber": "HUB-SN-0001",
                        "wlan": {"ip": LAN_IP, "macAddress": MAC},
                    },
                    "valve1": {"serialNumber": "HUB-VALVE-SN-1"},
                },
                "systemSettings": {
                    "maxShowerDuration": "60",
                    "showerMaxTemperature": "116",
                    "flowRateEnable": "0",
                },
                "steamSettings": {"defaultTemperature": 110, "defaultTime": 15},
            },
        }

    def _hub_errors(self, device_id: str) -> dict[str, Any]:
        return {
            "deviceId": device_id,
            "tenantId": TENANT_ID,
            "errorDetails": [
                {
                    "id": f"err-{device_id}",
                    "errorCode": "S4",
                    "title": "Steam generator",
                    "component": "steam",
                    "isActive": True,
                    "details": f"{device_id} steam fault",
                }
            ],
        }

    def _hub_favorites(self, device_id: str) -> dict[str, Any]:
        return {
            "favorites": [
                {"favoriteId": 1, "name": "Morning Routine", "deviceId": device_id}
            ]
        }

    # Faucets: each answers with its own identity -------------------------- #
    def _faucet_configuration(self, device_id: str) -> dict[str, Any]:
        if device_id == DEVICE_ID:
            return self.config
        config = json.loads(json.dumps(self.config).replace(DEVICE_ID, device_id))
        config["configuration"]["about"]["serialNumber"] = "SN-SECRET-BAR"
        return config


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> AccountKohler:
    """Overrides the shared fixture: the same fake, answering for valves and controllers."""
    return AccountKohler(aioclient_mock)


@pytest.fixture
async def whole_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: AccountKohler
) -> AsyncGenerator[MockConfigEntry]:
    """Two valves, one controller and two faucets, set up and connected."""
    kohler.devices = [
        {**VALVE, "serialNumber": VALVES[VALVE_ID][0]},
        {
            "deviceId": VALVE_2_ID,
            "sku": "GCS",
            "logicalName": "Tub",
            "serialNumber": VALVES[VALVE_2_ID][0],
        },
        {
            "deviceId": HUB_ID,
            "sku": "HUB",
            "logicalName": "Anthem Plus",
            "serialNumber": "HUB-SN-0001",
        },
        {**FAUCET, "serialNumber": "KITCHEN-SN-0001"},
        {**BAR, "serialNumber": "BAR-SN-0001"},
    ]
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_VALVES: {
                VALVE_ID: {"warmup_auto_restore": True},
                VALVE_2_ID: {"warmup_auto_restore": False},
                REMOVED_VALVE_ID: {"warmup_auto_restore": True},
            }
        },
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = account(hass, config_entry)
    await wait_for(hass, lambda: coordinator.stream.connected, "the stream")
    await wait_for(
        hass,
        lambda: all(v.firmware_info for v in coordinator.valves),
        "the firmware reads",
    )
    yield config_entry
    await unload(hass, config_entry)


def _device(hass: HomeAssistant, kohler_device_id: str) -> dr.DeviceEntry:
    device = registered_device(hass, kohler_device_id)
    assert device is not None, kohler_device_id
    return device


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
async def test_the_downloaded_config_entry_report_carries_no_identity(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """The whole file as the browser saves it — Home Assistant's own sections included."""
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)

    assert_clean(document)
    report = document["data"]
    assert report["devices"] == {
        "valve_present": True,
        "valve_count": 2,
        "controller_present": True,
        "controller_count": 1,
        "faucet_count": 2,
    }


@pytest.mark.parametrize(
    "kohler_device_id", [VALVE_ID, VALVE_2_ID, HUB_ID, DEVICE_ID, BAR_ID]
)
async def test_every_device_report_carries_no_identity(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
    kohler_device_id: str,
) -> None:
    document = await _get_diagnostics_for_device(
        hass, hass_client, whole_account, _device(hass, kohler_device_id)
    )
    assert_clean(document)


async def test_entry_secrets_are_redacted_but_configuration_is_kept(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """Credentials and account identity go; the settings a report is read for stay."""
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    entry = document["data"]["entry"]

    for key in ("username", "refresh_token", "tenant_id", "mobile_device_id"):
        assert entry["data"][key] == "**REDACTED**", key
    assert entry["data"]["temperature_unit"] == "Fahrenheit"
    assert entry["data"]["water_units"] == "Metric"


async def test_per_valve_options_are_relabelled_not_keyed_by_device_id(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """The labels match `valves[]` order; an id no longer on the account is still hidden."""
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    valves = document["data"]["entry"]["options"][CONF_VALVES]

    assert valves == {
        "valve_0": {"warmup_auto_restore": True},
        "valve_1": {"warmup_auto_restore": False},
        "valve_unknown": {"warmup_auto_restore": True},
    }
    assert [v["warmup"]["auto_restore"] for v in document["data"]["valves"]] == [
        True,
        False,
    ]


async def test_serials_are_reduced_to_whether_they_are_present(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """Model, firmware and status are what a hardware report is read for; serials are not."""
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    about = document["data"]["valves"][1]["about"]

    assert about["gateway"] == {
        "model": "GW-1",
        "firmware": "00.74",
        "serial_present": True,
    }
    assert about["valves"] == [
        {
            "model": "K-28210",
            "name": "Valve 1",
            "firmware": "11",
            "status": "Connected",
            "serial_present": True,
        }
    ]
    assert [i["serial_present"] for i in about["interfaces"]] == [False, True]
    controller = document["data"]["controller"]
    assert controller["settings"]["lan_ip_present"] is True
    assert controller["settings"]["max_shower_duration_minutes"] == 60


async def test_the_configuration_record_is_summarised_not_reproduced(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """Versions by path, key names for the rest; a device id under a version key is described."""
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    configuration = document["data"]["valves"][0]["configuration"]

    assert configuration["read"] is True
    assert configuration["firmware"] == "2.2"
    fields = configuration["version_fields"]
    assert fields["iot.firmwareVersion"] == "00.74"
    assert fields["configuration.about.primaryValve.firmware"] == "10"
    assert fields["iot.deviceVersionId"] == f"<str, {len(VALVE_ID)} chars>"
    assert configuration["version_blocks"]["otaReportedProperties"] == {
        "firmwareType": "Application",
        "initialVersion": "2.20",
        "updatedVersion": "2.20",
    }
    assert configuration["populated"]["zoneone"] is False
    assert {"iot", "id", "deviceId", "serialNumber"} <= set(configuration["other_keys"])
    assert configuration["about_keys"] == ["gateway", "primaryValve", "uI2"]
    assert configuration["created_time"] == "2024-03-11T14:22:31Z"


async def test_fault_logs_keep_the_fault_and_drop_the_ids(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    report = document["data"]

    assert [f["errorCode"] for f in report["valves"][0]["fault_log"]] == ["E3"]
    assert [f["errorCode"] for f in report["valves"][1]["fault_log"]] == ["E7"]
    (fault,) = report["valves"][0]["fault_log"]
    assert set(fault) == {
        "errorCode",
        "title",
        "description",
        "errorState",
        "isActive",
        "timestamp",
        "valveId",
        "component",
        "area",
    }
    assert report["controller"]["active_errors"] == [
        {
            "errorCode": "S4",
            "title": "Steam generator",
            "component": "steam",
            "isActive": True,
        }
    ]


async def test_firmware_reads_report_versions_without_the_download_address(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    expected = {
        "currentFirmware": "10",
        "firmware": "12",
        "firmwareUpdateAvailable": True,
        "mandatoryUpdate": False,
        "otaStatus": "NotStarted",
    }
    valve = document["data"]["valves"][0]
    assert valve["firmware"] == {"gcs": expected, "gateway": expected}
    assert document["data"]["controller"]["firmware"] == expected


async def test_names_the_owner_chose_are_counted_not_copied(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    document = await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    report = document["data"]

    assert report["valves"][0]["presets"]["slots_seen"] == 1
    assert report["controller"]["favorites_count"] == 1


# --------------------------------------------------------------------------- #
# One report, many buttons
# --------------------------------------------------------------------------- #
async def test_the_config_entry_report_describes_the_first_of_each_device(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    report = (
        await _get_diagnostics_for_config_entry(hass, hass_client, whole_account)
    )["data"]

    assert report["requested_for"] == "config_entry"
    assert len(report["valves"]) == 2
    assert len(report["controllers"]) == 1
    assert len(report["faucets"]) == 2
    assert report["valve"]["fault_log"] == report["valves"][0]["fault_log"]
    assert report["valve"]["about"] == report["valves"][0]["about"]
    assert report["run_time"] == report["valves"][0]["run_time"]
    assert report["warmup"] == report["valves"][0]["warmup"]
    assert "run_time" not in report["valve"] and "warmup" not in report["valve"]
    assert report["valves"][0]["cloud_connected"] is True
    assert report["stream"] == {"mqtt_connected": True}


async def test_the_second_valves_button_describes_the_second_valve(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    """The singular block follows the button; `valve_1` above valve 0's data was the lie."""
    document = await _get_diagnostics_for_device(
        hass, hass_client, whole_account, _device(hass, VALVE_2_ID)
    )
    report = document["data"]

    assert report["requested_for"] == "valve_1"
    second = report["valves"][1]
    assert report["valve"]["about"] == second["about"]
    assert report["valve"]["fault_log"] == second["fault_log"]
    assert [f["errorCode"] for f in report["valve"]["fault_log"]] == ["E7"]
    assert report["warmup"] == second["warmup"]
    # The whole installation is still there.
    assert len(report["valves"]) == 2 and len(report["faucets"]) == 2
    assert report["controller"] == report["controllers"][0]


@pytest.mark.parametrize(
    ("kohler_device_id", "requested_for"),
    [(VALVE_ID, "valve_0"), (HUB_ID, "controller"), (DEVICE_ID, "faucet_0")],
)
async def test_each_device_page_names_itself_and_still_reports_everything(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
    kohler_device_id: str,
    requested_for: str,
) -> None:
    document = await _get_diagnostics_for_device(
        hass, hass_client, whole_account, _device(hass, kohler_device_id)
    )
    report = document["data"]

    assert report["requested_for"] == requested_for
    assert report["devices"]["valve_count"] == 2
    assert {"valve", "valves", "controller", "controllers", "faucets"} <= set(report)
    assert report["valve"]["fault_log"] == report["valves"][0]["fault_log"]


async def test_the_second_faucets_page_is_named_by_its_position(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    whole_account: MockConfigEntry,
) -> None:
    document = await _get_diagnostics_for_device(
        hass, hass_client, whole_account, _device(hass, BAR_ID)
    )
    assert document["data"]["requested_for"] == "faucet_1"


async def test_a_zone_sub_device_reports_for_its_valve(
    hass: HomeAssistant, whole_account: MockConfigEntry
) -> None:
    """In sub-device grouping each zone has its own page; its button means its valve."""
    zone_page = SimpleNamespace(identifiers={(DOMAIN, f"{VALVE_2_ID}_zone_1")})
    report = await diagnostics.async_get_device_diagnostics(
        hass, whole_account, zone_page
    )

    assert report["requested_for"] == "valve_1"
    assert report["valve"]["about"] == report["valves"][1]["about"]


async def test_a_device_this_integration_does_not_know_gets_the_default_report(
    hass: HomeAssistant, whole_account: MockConfigEntry
) -> None:
    """A diagnostics report that raises is a report nobody can attach to an issue."""
    stranger = SimpleNamespace(
        identifiers={("other_integration", VALVE_ID), (DOMAIN, "gcs-nobody")}
    )
    report = await diagnostics.async_get_device_diagnostics(
        hass, whole_account, stranger
    )

    assert report["requested_for"] == "unknown_device"
    assert report["valve"]["about"] == report["valves"][0]["about"]
    assert_clean(report)


async def test_a_single_valve_account_keeps_the_plain_labels(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    kohler: AccountKohler,
) -> None:
    """Reports from single-device accounts read as they always have: `valve`, `faucet`."""
    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    valve = await _get_diagnostics_for_device(
        hass, hass_client, config_entry, _device(hass, VALVE_ID)
    )
    faucet = await _get_diagnostics_for_device(
        hass, hass_client, config_entry, _device(hass, DEVICE_ID)
    )

    assert valve["data"]["requested_for"] == "valve"
    assert faucet["data"]["requested_for"] == "faucet"
    assert "controller" not in valve["data"]
    assert_clean(valve)
    await unload(hass, config_entry)


# --------------------------------------------------------------------------- #
# Repair issues ride along in the downloaded file
# --------------------------------------------------------------------------- #
async def test_a_raised_repair_issue_does_not_put_a_device_id_in_the_download(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_entry: MockConfigEntry,
    kohler: AccountKohler,
) -> None:
    kohler.fail_api(
        f"/faucet-state/{DEVICE_ID}", *[(400, {"message": "bad request"})] * 3
    )
    for _ in range(3):
        freezer.tick(FAUCET_SCAN_INTERVAL_IDLE + timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert ir.async_get(hass).issues, "precondition: the faucet raised its repair issue"

    document = await _get_diagnostics_for_config_entry(hass, hass_client, setup_entry)

    issue_ids = [issue["issue_id"] for issue in document["issues"]]
    assert issue_ids
    assert not [i for i in issue_ids if DEVICE_ID in i], issue_ids
