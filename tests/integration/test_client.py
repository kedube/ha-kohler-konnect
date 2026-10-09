"""The shared Kohler client, as the faucets use it: tokens, errors, commands, logging.

Adapted from the faucet integration's client tests. Signing in now runs through the
showers' `B2C_1A_signin` refresh token instead of a stored password, so the password tests
went; the rest check the same behaviour against the client every device shares.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.kohler_konnect.konnect import (
    AuthError,
    DeviceOffline,
    FaucetDevice,
    FirmwareInfo,
    KohlerAuth,
    KohlerClient,
    KohlerError,
)
from custom_components.kohler_konnect.konnect.faucet import (
    parse_display_quantity,
    parse_firmware,
)

from .conftest import DEVICE_ID, TENANT_ID, FakeKohler, make_jwt


@pytest.fixture
def client(hass: HomeAssistant, kohler: FakeKohler) -> KohlerClient:
    session = async_get_clientsession(hass)
    return KohlerClient(session, KohlerAuth(session, "refresh-0"))


@pytest.fixture
def device(client: KohlerClient) -> FaucetDevice:
    return FaucetDevice(client, DEVICE_ID)


async def test_401_renews_token_and_retries_once(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    await client.async_get_faucet_state(DEVICE_ID)
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {"message": "expired"}))

    payload = await client.async_get_faucet_state(DEVICE_ID)

    assert payload["state"]["status"] == "Off"
    assert [r["grant_type"] for r in kohler.token_requests] == [
        "refresh_token",
        "refresh_token",
    ]


async def test_repeated_401_is_an_api_error(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """A fresh token being refused is not something signing in again would fix."""
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {}), (401, {}))
    with pytest.raises(KohlerError, match="HTTP 401") as err:
        await client.async_get_faucet_state(DEVICE_ID)
    assert not isinstance(err.value, AuthError)
    assert not err.value.rejected  # an expired token is not a refusal


async def test_error_messages_carry_no_ids(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.fail_api(
        f"/faucet-state/{DEVICE_ID}", (403, {"message": f"tenant {TENANT_ID} denied"})
    )
    with pytest.raises(KohlerError) as err:
        await client.async_get_faucet_state(DEVICE_ID)
    assert TENANT_ID not in str(err.value)
    assert DEVICE_ID not in str(err.value)
    assert "faucet-state/<id>" in str(err.value)
    assert "<account>" in str(err.value)
    assert err.value.rejected


async def test_non_json_error(client: KohlerClient, kohler: FakeKohler) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (502, "<html>Bad gateway</html>"))
    with pytest.raises(KohlerError, match="HTTP 502"):
        await client.async_get_faucet_state(DEVICE_ID)


async def test_network_error(client: KohlerClient, kohler: FakeKohler) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", kohler.network_error())
    with pytest.raises(KohlerError, match="connection reset"):
        await client.async_get_faucet_state(DEVICE_ID)


async def test_timeout_is_a_kohler_error(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    """aiohttp raises a bare `TimeoutError`, which used to escape every caller."""
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", TimeoutError())
    with pytest.raises(KohlerError, match="TimeoutError"):
        await client.async_get_faucet_state(DEVICE_ID)


async def test_unreadable_state_is_an_unexpected_response(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (200, {"unexpected": True}))
    with pytest.raises(KohlerError) as err:
        await client.async_get_faucet_state(DEVICE_ID)
    assert err.value.rejected


async def test_throttling_says_how_long_to_wait(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (429, {}, {"Retry-After": "120"}))
    with pytest.raises(KohlerError) as err:
        await client.async_get_faucet_state(DEVICE_ID)
    assert err.value.retry_after == 120
    assert not err.value.rejected


async def test_concurrent_requests_share_one_refresh(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    await asyncio.gather(*(client.async_get_faucet_state(DEVICE_ID) for _ in range(5)))
    assert len(kohler.token_requests) == 1


async def test_customer_lists_both_konnect_faucets(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    kohler.devices += [
        {"deviceId": "set-1", "sku": "SET", "logicalName": "Bar"},
        {"deviceId": "gcs-1", "sku": "GCS", "logicalName": "Shower"},
        # Not a Kohler SKU; the app has no such device.
        {"deviceId": "faucet-1", "sku": "FAUCET", "logicalName": "Other"},
    ]
    customer = await client.async_get_customer()
    assert [(d.device_id, d.sku) for d in customer.faucet_devices] == [
        (DEVICE_ID, "SEN"),
        ("set-1", "SET"),
    ]
    assert [d.device_id for d in customer.supported_devices] == [
        "gcs-1",
        DEVICE_ID,
        "set-1",
    ]
    assert customer.describe() == (
        "1 Anthem valve(s) and 2 faucet(s) (ignoring other Kohler devices: FAUCET)"
    )


async def test_commands(device: FaucetDevice, kohler: FakeKohler) -> None:
    await device.async_dispense(0.236588237)
    await device.async_set_water(True)
    await device.async_set_water(False)

    body = {"deviceId": DEVICE_ID, "sku": "SEN", "tenantId": TENANT_ID}
    assert kohler.commands == [
        ("dispense", {**body, "quantity": 0.2366}),
        ("onoff", {**body, "action": "ON"}),
        ("onoff", {**body, "action": "OFF"}),
    ]


async def test_commands_send_the_device_sku(
    client: KohlerClient, kohler: FakeKohler
) -> None:
    setra = FaucetDevice(client, "set-1", "SET")
    await setra.async_dispense(0.25)
    await setra.async_set_water(False)
    assert [body["sku"] for _, body in kohler.commands] == ["SET", "SET"]


@pytest.mark.parametrize("code", ["906", 906, " 903 "])
async def test_command_refused_in_a_200_reply(
    device: FaucetDevice, kohler: FakeKohler, code: object
) -> None:
    """HTTP 200 with a failure statusCode means the command did nothing."""
    kohler.fail_api("/faucet/dispense", (200, {"statusCode": code, "message": "x"}))
    with pytest.raises(KohlerError) as err:
        await device.async_dispense(0.25)
    assert err.value.code == str(code).strip()
    assert err.value.status == 200


async def test_offline_refusal(device: FaucetDevice, kohler: FakeKohler) -> None:
    kohler.fail_api("/faucet/dispense", (200, {"statusCode": "900"}))
    with pytest.raises(DeviceOffline) as err:
        await device.async_dispense(0.25)
    assert err.value.code == "900"


async def test_command_refusal_is_explained(
    device: FaucetDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api(
        "/faucet/dispense",
        (400, {"statusCode": "906", "message": "Something went wrong"}),
    )
    with pytest.raises(KohlerError) as err:
        await device.async_dispense(0.25)
    assert err.value.code == "906"
    assert "the faucet could not dispense water (statusCode 906)" in str(err.value)


async def test_harmless_codes_are_not_refusals(
    device: FaucetDevice, kohler: FakeKohler
) -> None:
    kohler.fail_api("/faucet/onoff", (200, {"statusCode": "200", "correlationId": "c"}))
    await device.async_set_water(False)


async def test_unregister(client: KohlerClient, kohler: FakeKohler) -> None:
    await client.async_get_customer()
    await client.async_unregister_mobile_device("ha-identity")
    assert kohler.unregistered == ["ha-identity"]


def test_safe_paths_for_faucets() -> None:
    for path in (
        f"/devices/api/v1/device-management/faucet-state/{DEVICE_ID}",
        f"/devices/api/v1/device-management/faucet-usage/{DEVICE_ID}?Interval=DAY",
        f"/devices/api/v1/device-management/faucet-experience?DeviceIds={DEVICE_ID}",
        f"/devices/api/v1/device-management/customer-experience/{TENANT_ID}",
        f"/platform/api/v1/firmware/sensate/{DEVICE_ID}",
        f"/platform/api/v1/mobile/settings/{TENANT_ID}/ha-identity",
    ):
        safe = KohlerClient.safe_path(path)
        assert DEVICE_ID not in safe and TENANT_ID not in safe, safe
        assert "ha-identity" not in safe, safe


@pytest.mark.parametrize(
    ("text", "liters"),
    [
        ("750 Milliliters", 0.75),
        ("¾ Liters", 0.75),
        ("1¾ Quarts", 1.75 * 0.946353),
        ("1 Cups", 0.2365880012512207),
        ("3 Gallons", 3 * 3.785411784),
        ("1.5 liters", 1.5),
        ("Cups", None),
        ("0 Liters", None),
        ("2 Pints", None),
        (None, None),
    ],
)
def test_parse_display_quantity(text: object, liters: float | None) -> None:
    assert parse_display_quantity(text) == (
        pytest.approx(liters) if liters is not None else None
    )


def test_parse_firmware() -> None:
    assert parse_firmware(
        {
            "firmwareUpdateAvailable": True,
            "firmware": "17.1",
            "currentFirmware": 16.0,
            "mandatoryUpdate": True,
            "otaStatus": "NotStarted",
        }
    ) == FirmwareInfo(available=True, latest="17.1", current="16.0", mandatory=True)
    assert parse_firmware({"firmwareUpdateAvailable": False, "firmware": ""}) == (
        FirmwareInfo(available=False, latest=None, current=None, mandatory=False)
    )
    # Without a clear yes or no, the answer is unknown.
    assert parse_firmware({"firmwareUpdateAvailable": "true"}) is None
    assert parse_firmware(["junk"]) is None


async def test_credentials_never_logged(
    client: KohlerClient,
    device: FaucetDevice,
    kohler: FakeKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tokens and the MQTT password stay out of even the debug request log.

    That log is opt-in and records whole replies on purpose — ids included — so fields
    Kohler sends that nothing reads yet can be reported. The paths in it carry no ids.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.kohler_konnect")
    await client.async_get_customer()
    await device.async_dispense(0.25)
    await client.async_register_mobile_device("ha-identity")
    await client.async_unregister_mobile_device("ha-identity")

    assert "API GET /devices/api/v1/device-management/customer-device/<id>" in (
        caplog.text
    )
    access_tokens = [
        make_jwt({"oid": TENANT_ID, "n": n}) for n in range(1, kohler.issued + 1)
    ]
    for secret in (
        "refresh-0",
        "refresh-1",
        "SharedAccessSignature",
        *access_tokens,
    ):
        assert secret not in caplog.text, secret
