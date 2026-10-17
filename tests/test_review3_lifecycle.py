"""Unit tests for the final review's smaller lifecycle and sign-in fixes (2026-10-16)."""

from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.kohler_konnect.const import CONF_REFRESH_TOKEN
from custom_components.kohler_konnect.coordinator import KohlerKonnectCoordinator
from custom_components.kohler_konnect.konnect.auth import (
    AuthError,
    _raise_for_b2c_error,
)
from custom_components.kohler_konnect.konnect.mqtt import Envelope


@pytest.mark.parametrize("code", [90053, 90053.0, True, ["AADB2C90053"]])
def test_a_b2c_error_code_that_is_not_text_does_not_crash_the_sign_in(code):
    """`{"status": "400", "errorCode": 90053}` raised TypeError: "Unknown error"."""
    try:
        _raise_for_b2c_error(code)
    except AuthError:
        pass  # a recognised code is an answer, which is the point


def test_a_token_taken_up_from_the_entry_is_one_this_coordinator_has_seen():
    """So a rotation it finishes after closing is still stored, not refused as stale."""
    stand_in = SimpleNamespace(
        closed=False,
        entry=SimpleNamespace(data={CONF_REFRESH_TOKEN: "stored-by-the-old-one"}),
        _entry_token="built-with",
    )
    read = KohlerKonnectCoordinator._stored_refresh_token.__get__(stand_in)
    assert read() == "stored-by-the-old-one"
    assert stand_in._entry_token == "stored-by-the-old-one"


def test_a_message_held_before_the_account_read_keeps_its_arrival_time():
    """Replayed seconds later, it used to be stamped with the replay."""
    stand_in = SimpleNamespace(_recent_messages=deque())
    remember = KohlerKonnectCoordinator._remember_message.__get__(stand_in)
    arrived = time.time() - 10
    remember(
        Envelope(
            sku="GCS",
            device_id="gcs-1",
            code="GCS_SOLO_STS",
            attributes=[{"warmUpStatus": "warmUpNotInProgress"}],
            received_at=arrived,
            received_monotonic=time.monotonic() - 10,
        )
    )
    (record,) = stand_in._recent_messages
    stamped = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
    assert abs(stamped.timestamp() - arrived) < 1
    assert time.monotonic() - record["at"] == pytest.approx(10, abs=1)
    assert stamped.tzinfo is UTC or stamped.utcoffset().total_seconds() == 0


# --------------------------------------------------------------------------- #
# Follow-up review (2026-10-16)
# --------------------------------------------------------------------------- #
def test_an_unloading_entry_s_devices_are_not_offered_to_actions():
    """It stays listed until its shutdown is through — seconds, waiting on the stream and
    a token refresh — and a command sent through it then outlived the unload."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.kohler_konnect.const import DOMAIN
    from custom_components.kohler_konnect.faucet.services import _target_faucets
    from custom_components.kohler_konnect.services import _resolve_valve

    unloading = SimpleNamespace(closed=True)
    unloading.valves = [SimpleNamespace(name="Shower Left")]
    unloading.faucets = [SimpleNamespace(account=unloading)]
    hass = SimpleNamespace(data={DOMAIN: {"entry": unloading}})
    with pytest.raises(HomeAssistantError, match="Shower Left is being reloaded"):
        _resolve_valve(hass, None)
    with pytest.raises(HomeAssistantError) as raised:
        _target_faucets(hass, SimpleNamespace(data={}))
    assert raised.value.translation_key == "no_faucet"

    # Still counted when picking the only one: a call meant for it went to the other's.
    other = SimpleNamespace(closed=False)
    other.valves = [SimpleNamespace(name="Shower Right")]
    other.faucets = [SimpleNamespace(account=other)]
    hass.data[DOMAIN]["other"] = other
    with pytest.raises(HomeAssistantError, match="More than one Anthem valve"):
        _resolve_valve(hass, None)
    with pytest.raises(HomeAssistantError) as raised:
        _target_faucets(hass, SimpleNamespace(data={}))
    assert raised.value.translation_key == "faucet_required"


def test_a_valve_is_read_whole_on_the_first_connect_by_what_answered():
    """Not by what is filled in: a message replayed from before the devices were known
    filled the outlet records over a settings read that had failed, and a valve with no
    presets at all was read whole at every start."""
    from custom_components.kohler_konnect.coordinator import Valve

    incomplete = Valve.seed_incomplete.fget
    valve = SimpleNamespace(
        _topology_checked=True,
        _settings_read=False,
        _presets_read=True,
        gcs_state=SimpleNamespace(outlet_limits={1: {"maximumTemperature": 450}}),
    )
    valve.gcs_state.presets = [SimpleNamespace(preset_id="1")]
    assert incomplete(valve)
    valve._settings_read = True
    valve.gcs_state = SimpleNamespace(outlet_limits={1: {}}, presets=[])
    assert not incomplete(valve)
