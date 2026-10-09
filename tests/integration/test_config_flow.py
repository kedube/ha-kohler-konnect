"""Adding the account, signing in again, and the options — for any mix of devices."""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_konnect.const import (
    CONF_MAX_RUN_MINUTES,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    CONF_WATER_UNITS,
    CONF_ZONE_GROUPING,
    CONF_ZONE_OUTLETS,
    DOMAIN,
)
from custom_components.kohler_konnect.konnect import (
    InvalidCredentials,
    KohlerAuth,
    TokenSet,
)

from .conftest import TENANT_ID, USERNAME, VALVE, FakeKohler, make_jwt, unload

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def sign_in() -> Generator[list[tuple[str, str]]]:
    """The server-side B2C sign-in, which drives Kohler's web pages, stood in for."""
    calls: list[tuple[str, str]] = []

    async def _sign_in(self: KohlerAuth, username: str, password: str) -> TokenSet:
        calls.append((username, password))
        if password != PASSWORD:
            raise InvalidCredentials("Kohler rejected that email or password.")
        self._tokens = TokenSet(
            access_token=make_jwt({"oid": TENANT_ID}),
            refresh_token="refresh-signed-in",
            expires_at=time.time() + 3600,
        )
        self._refresh_token = "refresh-signed-in"
        return self._tokens

    with patch.object(KohlerAuth, "async_sign_in", _sign_in):
        yield calls


async def _start(hass: HomeAssistant, username: str = USERNAME, password=PASSWORD):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: username, CONF_PASSWORD: password}
    )


async def test_an_account_with_only_a_faucet_asks_nothing_more(
    hass: HomeAssistant, kohler: FakeKohler, sign_in: list
) -> None:
    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await _start(hass, username="Owner@Example.com")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kohler Konnect (Owner@Example.com)"
    data = result["data"]
    assert data[CONF_REFRESH_TOKEN] == "refresh-signed-in"
    assert data[CONF_TENANT_ID] == TENANT_ID
    assert data[CONF_WATER_UNITS] == "Metric"
    # No valve, so no valve model.
    assert CONF_VALVE_MODEL not in data and CONF_ZONE_OUTLETS not in data
    # Never the password.
    assert PASSWORD not in str(data)
    assert result["result"].unique_id == "owner@example.com"


async def test_a_detected_valve_layout_skips_the_question(
    hass: HomeAssistant, kohler: FakeKohler, sign_in: list
) -> None:
    kohler.devices.append(dict(VALVE))
    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await _start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VALVE_MODEL] == "K-28210"
    assert result["data"][CONF_ZONE_OUTLETS] == [3, 0]


async def test_an_undetected_valve_layout_is_asked_for(
    hass: HomeAssistant, kohler: FakeKohler, sign_in: list
) -> None:
    kohler.devices.append(dict(VALVE))
    kohler.valve_outlets = None
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "valve"
    assert "faucet" in result["description_placeholders"]["summary"]

    with patch("custom_components.kohler_konnect.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_VALVE_MODEL: "K-28212"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ZONE_OUTLETS] == [3, 3]


async def test_an_account_with_nothing_supported(
    hass: HomeAssistant, kohler: FakeKohler, sign_in: list
) -> None:
    kohler.devices = [{"deviceId": "dtv-1", "sku": "DTV", "logicalName": "Steam"}]
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}


async def test_a_wrong_password(
    hass: HomeAssistant, kohler: FakeKohler, sign_in: list
) -> None:
    result = await _start(hass, password="wrong")
    assert result["errors"] == {"base": "invalid_auth"}


async def test_the_same_account_twice(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_sign_in_again(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler, sign_in
) -> None:
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert sign_in == [(USERNAME, PASSWORD)]
    # The reload that follows signs in with the new token, which Kohler then rotates.
    assert kohler.token_requests[0]["refresh_token"] == "refresh-signed-in"
    assert config_entry.data[CONF_REFRESH_TOKEN] not in ("refresh-0", None)
    await unload(hass, config_entry)


async def test_options_for_a_faucet_only_account(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    # Only what applies: no zone grouping without a valve or controller.
    assert [str(key) for key in result["data_schema"].schema] == [CONF_MAX_RUN_MINUTES]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MAX_RUN_MINUTES: 15.0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {CONF_MAX_RUN_MINUTES: 15}


async def test_options_for_a_mixed_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.devices.append(dict(VALVE))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert [str(key) for key in result["data_schema"].schema] == [
        CONF_ZONE_GROUPING,
        CONF_MAX_RUN_MINUTES,
    ]
