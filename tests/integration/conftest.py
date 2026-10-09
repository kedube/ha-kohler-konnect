"""Fixtures for the tests that run the integration inside a real Home Assistant.

``FakeKohler`` stands in for Kohler's token endpoint and cloud API at the HTTP level, so the
real client, coordinators and platforms are exercised end to end. ``FakeMqttClient`` stands
in for paho, so the real MQTT stream runs without a network.

Adapted from the separate ``kohler_sensate`` integration's own suite, which these faucet
tests come from; the account here holds a faucet called "Kitchen", plus a valve when a test
asks for one.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from aiohttp import ClientError
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.kohler_konnect.const import (
    CONF_MOBILE_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    CONF_WATER_UNITS,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect import Envelope
from custom_components.kohler_konnect.konnect.const import (
    API_BASE,
    B2C_TOKEN_URL,
)

DEVICE_ID = "sen-test123456"
VALVE_ID = "gcs-shower0001"
TENANT_ID = "0f0f0f0f-1111-2222-3333-444444444444"
USERNAME = "owner@example.com"
MOBILE_ID = "ha0123456789abcd"

FAUCET = {"deviceId": DEVICE_ID, "sku": "SEN", "logicalName": "Kitchen"}
VALVE = {"deviceId": VALVE_ID, "sku": "GCS", "logicalName": "Shower"}


def make_jwt(claims: dict[str, Any]) -> str:
    def b64(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.signature"


class FakeKohler:
    """Programmable fake of Kohler's token endpoint and cloud API."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        self.issued = 0
        self.token_requests: list[dict[str, str]] = []
        # Queued token responses: (status, json) tuples or exceptions.
        self.token_queue: list[Any] = []
        # Queued API responses, keyed by path suffix: (status, json) or exceptions.
        self.api_queue: dict[str, list[Any]] = {}
        self.commands: list[tuple[str, dict[str, Any]]] = []
        # The account's devices; tests add a valve, or remove the faucet.
        self.devices: list[dict[str, Any]] = [dict(FAUCET)]
        # The Konnect account's unit setting: "Metric", or "Standard" for US units.
        self.water_units = "Metric"
        self.state: dict[str, Any] = {
            "status": "Off",
            "progress": "NotStarted",
            "handleState": "OPEN",
            "quantity": None,
        }
        # Shaped like a real Sensate's reply (firmware 16.0), which repeats the device id
        # under "id".
        self.config: dict[str, Any] = {
            "id": DEVICE_ID,
            "deviceId": DEVICE_ID,
            "configuration": {
                "about": {
                    "name": "SENSATE",
                    "model": "SEN",
                    "serialNumber": "SN-SECRET-1",
                    "firmware": {"version": "16.0", "latestVersion": "16.0"},
                    "hardware": "CC3235SF",
                }
            },
            "leakDetectionHistory": [],
        }
        # None leaves connectionState out of the reply.
        self.connection: str | None = "Connected"
        # The SKU in the faucet-state reply; None leaves it out.
        self.state_sku: str | None = "SEN"
        # This faucet's entries in customer-experience's "sensateExperiences".
        self.presets: list[dict[str, Any]] = []
        # Its entries in faucet-experience?DeviceIds=; None answers 404.
        self.faucet_presets: list[dict[str, Any]] | None = None
        # The firmware check's reply; None answers 404.
        self.firmware: dict[str, Any] | None = None
        # MQTT registrations removed with DELETE, by identity.
        self.unregistered: list[str] = []
        # Litres per period, as faucet-usage reports them.
        self.usage_months: dict[str, float] = {"2025-04": 1.593, "2026-10": 1.6144}
        self.usage_days: dict[str, float] = {"2026-10-07": 1.5274}
        self.push_registrations: list[dict[str, Any]] = []
        # A valve's outlet split, as its `gcsadvancestate` reports it; None answers 404,
        # which leaves the setup flow to ask for the model.
        self.valve_outlets: tuple[int, int] | None = (3, 0)
        mocker.post(B2C_TOKEN_URL, side_effect=self._token)
        for method in ("get", "post", "delete", "patch"):
            mocker.request(
                method, re.compile(re.escape(API_BASE)), side_effect=self._api
            )

    def _respond(self, method: str, url: Any, item: Any) -> AiohttpClientMockResponse:
        if isinstance(item, BaseException):
            return AiohttpClientMockResponse(method, url, exc=item)
        status, body, *rest = item
        headers = rest[0] if rest else None
        if isinstance(body, str):
            return AiohttpClientMockResponse(
                method, url, status=status, text=body, headers=headers
            )
        return AiohttpClientMockResponse(
            method, url, status=status, json=body, headers=headers
        )

    async def _token(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        self.token_requests.append(dict(data))
        if self.token_queue:
            return self._respond(method, url, self.token_queue.pop(0))
        self.issued += 1
        return self._respond(
            method,
            url,
            (
                200,
                {
                    "access_token": make_jwt({"oid": TENANT_ID, "n": self.issued}),
                    "refresh_token": f"refresh-{self.issued}",
                    "expires_in": "3600",
                },
            ),
        )

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        for suffix, queue in self.api_queue.items():
            if path.endswith(suffix) and queue:
                return self._respond(method, url, queue.pop(0))
        if method.lower() == "delete":
            if "/mobile/settings/" in path:
                self.unregistered.append(path.rsplit("/", 1)[-1])
                return self._respond(method, url, (200, {}))
            return self._respond(method, url, (404, {"message": "not found"}))
        if path.endswith("/mobile/settings"):
            self.push_registrations.append(data)
            n = len(self.push_registrations)
            return self._respond(
                method,
                url,
                (
                    200,
                    {
                        "ioTHubSettings": {
                            "ioTHub": "hub.example.net",
                            "deviceId": f"mobile-{data['mobileDeviceId']}",
                            "username": "hub.example.net/user",
                            "password": f"SharedAccessSignature sig-{n}",
                        }
                    },
                ),
            )
        if method.lower() == "post":
            self.commands.append((path.rsplit("/", 1)[-1], data))
            return self._respond(
                method, url, (200, {"correlationId": "c", "timestamp": 1})
            )
        if "/customer-device/" in path:
            return self._respond(
                method,
                url,
                (
                    200,
                    {
                        "temperatureUnit": "Fahrenheit",
                        "waterUnits": self.water_units,
                        "customerHome": [
                            {"address": "1 Main St", "devices": self.devices}
                        ],
                    },
                ),
            )
        if "/faucet-state/" in path:
            body: dict[str, Any] = {"state": dict(self.state)}
            if self.state_sku is not None:
                body["sku"] = self.state_sku
            if self.connection is not None:
                body["connectionState"] = self.connection
                body["lastConnected"] = "2026-10-06T12:00:00Z"
            return self._respond(method, url, (200, body))
        if "/customer-experience/" in path:
            # Presets of every device on the account, a shower's among them.
            body = {
                "experiences": [],
                "sensateExperiences": [
                    {"deviceId": DEVICE_ID, "sku": "SEN", **p} for p in self.presets
                ],
                "gcsExperiences": [
                    {"deviceId": VALVE_ID, "experienceId": "20", "title": "Cool Down"}
                ],
            }
            return self._respond(method, url, (200, body))
        if "/faucet-usage/" in path:
            interval = url.query.get("Interval")
            usage = {"MONTH": self.usage_months, "DAY": self.usage_days}.get(interval)
            if usage is None or not url.query.get("FromDate"):
                return self._respond(method, url, (400, {"message": "Bad Request"}))
            rows = [
                {"intervalKey": key, "quantity": liters, "waterUsage": liters}
                for key, liters in usage.items()
            ]
            return self._respond(
                method, url, (200, {"faucetUsageDataDetailsList": rows})
            )
        if "/faucet-configuration/" in path:
            return self._respond(method, url, (200, self.config))
        if path.endswith("/faucet-experience") and self.faucet_presets is not None:
            if url.query.get("DeviceIds") != DEVICE_ID:
                return self._respond(method, url, (400, {"message": "Bad Request"}))
            group = {"deviceId": DEVICE_ID, "experience": self.faucet_presets}
            return self._respond(method, url, (200, {"faucetExperienceList": [group]}))
        if "/gcs-state/gcsadvancestate/" in path and self.valve_outlets is not None:
            first, second = self.valve_outlets
            setting = {
                "valveSettings": [
                    {"valve": "valve1", "noOfOutlets": first},
                    {"valve": "valve2", "noOfOutlets": second},
                ]
            }
            return self._respond(method, url, (200, {"setting": setting}))
        if "/firmware/sensate/" in path and self.firmware is not None:
            return self._respond(method, url, (200, self.firmware))
        return self._respond(method, url, (404, {"message": "not found"}))

    def fail_api(self, suffix: str, *items: Any) -> None:
        self.api_queue.setdefault(suffix, []).extend(items)

    def network_error(self) -> ClientError:
        return ClientError("connection reset")

    @property
    def state_polls(self) -> int:
        return sum(
            1 for _, url, _, _ in self.mocker.mock_calls if "/faucet-state/" in str(url)
        )


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload
        self.qos = 1
        self.retain = False


class FakeMqttClient:
    """Stands in for paho (callback API version 1); connects instantly."""

    instances: ClassVar[list[FakeMqttClient]] = []
    refuse: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, bytes]] = []
        self.subscribed: list[str] = []
        self.stopped = False
        FakeMqttClient.instances.append(self)

    def username_pw_set(self, username: str, password: str) -> None:
        self.username, self.password = username, password

    def tls_set_context(self, context: Any) -> None:
        self.tls = context

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.host, self.port = host, port

    def loop_start(self) -> None:
        if FakeMqttClient.refuse:
            # As paho does: a refused CONNACK, then the disconnect it leads to.
            self.on_connect(self, None, None, 5)
            self.on_disconnect(self, None, 5)
            return
        self.on_connect(self, None, None, 0)

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: bytes, qos: int) -> None:
        self.published.append((topic, payload))

    def disconnect(self) -> None:
        pass

    def loop_stop(self) -> None:
        self.stopped = True

    # helpers for tests
    def deliver(self, payload: dict[str, Any], rid: str = "1") -> None:
        self.on_message(
            self,
            None,
            Message(
                f"$iothub/methods/POST/ExecuteControlCommand/?$rid={rid}",
                json.dumps(payload).encode(),
            ),
        )

    def drop(self) -> None:
        self.on_disconnect(self, None, 7)


def feed_message(code: str, **attributes: Any) -> dict[str, Any]:
    """A message shaped like those a real Sensate sends."""
    return {
        "sysid": "SEN-TEST",
        "deviceid": DEVICE_ID,
        "tenantid": TENANT_ID,
        "sku": "SEN",
        "type": "STS",
        "timestamp": "1791397638",
        "data": {
            "type": "Status",
            "code": code,
            "attributes": [{"code": code, **attributes}],
        },
    }


def to_envelope(payload: dict[str, Any]) -> Envelope:
    """A decoded message, as the stream hands it on (see `KonnectMqttStream._on_message`)."""
    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    attributes = data.get("attributes")
    return Envelope(
        sku=str(payload.get("sku") or ""),
        device_id=str(payload.get("deviceid") or payload.get("deviceId") or ""),
        code=str(data.get("code") or ""),
        attributes=[a for a in (attributes or []) if isinstance(a, dict)],
        received_at=0.0,
        raw=payload,
    )


def water_message(status: str, handle: str = "OPEN") -> dict[str, Any]:
    return feed_message("SENSATE_STS", status=status, handle=handle)


def preset_message(name: str, status: str) -> dict[str, Any]:
    return feed_message(
        "SENSATE_EXP_STS", name=name, experienceid="exp-1", status=status
    )


@pytest.fixture(autouse=True)
def fake_mqtt() -> Generator[type[FakeMqttClient]]:
    """Never open a real connection."""
    FakeMqttClient.instances = []
    FakeMqttClient.refuse = False
    with patch(
        "custom_components.kohler_konnect.konnect.mqtt.mqtt.Client", FakeMqttClient
    ):
        yield FakeMqttClient


@pytest.fixture(autouse=True)
def quick_reconnects() -> Generator[None]:
    """Reconnect almost at once.

    The stream waits with `asyncio.sleep`, which no fake clock moves. Not zero: the delay
    doubles on each failure, and zero would retry a refused connection in a tight loop.
    """
    with (
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MIN_SECONDS", 0.01
        ),
        patch(
            "custom_components.kohler_konnect.konnect.mqtt.RECONNECT_MAX_SECONDS", 0.05
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    yield


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> FakeKohler:
    return FakeKohler(aioclient_mock)


@pytest.fixture
async def config_entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    """An account entry, as the setup flow leaves it for a faucet-only account.

    Unloaded after the test whatever it did, so no timer or stream outlives it.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Kohler Konnect ({USERNAME})",
        unique_id=USERNAME,
        data={
            CONF_USERNAME: USERNAME,
            CONF_REFRESH_TOKEN: "refresh-0",
            CONF_TENANT_ID: TENANT_ID,
            CONF_TEMPERATURE_UNIT: "Fahrenheit",
            CONF_WATER_UNITS: "Metric",
            CONF_MOBILE_DEVICE_ID: MOBILE_ID,
        },
    )
    entry.add_to_hass(hass)
    yield entry
    await unload(hass, entry)


BAR = {"deviceId": "sen-bar", "sku": "SEN", "logicalName": "Bar"}


def registered_device(
    hass: HomeAssistant, kohler_device_id: str
) -> dr.DeviceEntry | None:
    """The device registry's entry for one of the account's devices, or None.

    `async_get_device` is deprecated from Home Assistant 2026.10, which fails a test that
    calls it; `async_get_devices`, its replacement, is not in 2026.3.
    """
    registry = dr.async_get(hass)
    identifiers = {(DOMAIN, kohler_device_id)}
    if not hasattr(registry, "async_get_devices"):
        return registry.async_get_device(identifiers=identifiers)
    devices = registry.async_get_devices(identifiers=identifiers)
    assert len(devices) <= 1, devices
    return devices[0] if devices else None


def ha_device_id(hass: HomeAssistant, kohler_device_id: str) -> str:
    """Home Assistant's device id for one of the account's devices."""
    device = registered_device(hass, kohler_device_id)
    assert device is not None, kohler_device_id
    return device.id


def account(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """The entry's account coordinator."""
    return hass.data[DOMAIN][entry.entry_id]


def faucet(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """The entry's (first) faucet coordinator."""
    return account(hass, entry).faucets[0]


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> AsyncGenerator[MockConfigEntry]:
    """The account, set up and connected to the (fake) MQTT stream.

    Until a message about the faucet arrives, polling runs at its usual pace, as it would
    without the stream.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await wait_for(
        hass, lambda: account(hass, config_entry).stream.connected, "the stream"
    )
    # Connecting re-reads the faucet; let that re-read's 1 s cooldown pass, so the
    # re-read after a test's first command is not held back by it.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    yield config_entry
    await unload(hass, config_entry)


async def unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload, so no timer or stream outlives the test."""
    if hass.config_entries.async_get_entry(entry.entry_id) and entry.state.recoverable:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
async def push_entry(setup_entry: MockConfigEntry) -> MockConfigEntry:
    """``setup_entry``, by the name the instant-update tests use."""
    return setup_entry


async def wait_for(
    hass: HomeAssistant, condition: Callable[[], bool], what: str
) -> None:
    """Wait for a background task to reach a state."""
    for _ in range(200):
        await hass.async_block_till_done()
        if condition():
            return
        await hass.async_add_executor_job(time.sleep, 0.01)
    pytest.fail(f"timed out waiting for {what}")
