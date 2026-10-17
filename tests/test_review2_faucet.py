"""Faucet findings of the second 2026-10-16 review, at the level of single decisions.

Which preset list a read falls back to, and whether a failed ON can have turned the water
on — and so keeps the safety limit it set. The end-to-end cases are in
`integration/test_review2_faucet.py`. Each test here failed on the code before its fix,
unless its docstring says it guards the fix itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.kohler_konnect.faucet.coordinator import _not_carried_out
from custom_components.kohler_konnect.konnect import (
    AuthUnavailable,
    DeviceOffline,
    FaucetDevice,
    FaucetPreset,
    KohlerError,
)

DEVICE_ID = "sen-test123456"
KETTLE = {"experienceId": "p2", "title": "Kettle", "dispenseAmount": 1.2}
# Deleted in the app, but still in the account's legacy `sensateExperiences`.
DELETED = {
    "deviceId": DEVICE_ID,
    "experienceId": "old",
    "title": "Pot",
    "dispenseAmount": 8,
}


class _Client:
    """Answers the two preset reads with what each test queues."""

    def __init__(self, *faucet_replies: Any) -> None:
        self.faucet_replies = list(faucet_replies)
        self.account_reads = 0

    async def async_get_faucet_presets(self, device_id: str) -> Any:
        reply = self.faucet_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def async_get_customer_experience(self) -> Any:
        self.account_reads += 1
        return {"sensateExperiences": [DELETED]}


def _listed(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "faucetExperienceList": [{"deviceId": DEVICE_ID, "experience": list(items)}]
    }


def _http(status: int) -> KohlerError:
    return KohlerError(f"failed with HTTP {status}", {}, status)


NETWORK = KohlerError("Network error calling faucet-experience: connection reset")
KETTLE_ONLY = [FaucetPreset("p2", "Kettle", 1.2)]


# --- which preset list ------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [NETWORK, _http(500), _http(503), _http(429), _http(408)],
    ids=["network", "500", "503", "429", "408"],
)
@pytest.mark.asyncio
async def test_a_failure_to_answer_keeps_the_last_presets(failure: KohlerError) -> None:
    """It raises, so the coordinator keeps what it read; the legacy list stays out.

    Read once, or never: either way a failure to answer says nothing about the presets.
    """
    for first in ([], [_listed(KETTLE)]):
        client = _Client(*first, failure)
        device = FaucetDevice(client, DEVICE_ID)  # type: ignore[arg-type]
        if first:
            assert await device.async_get_presets() == (
                KETTLE_ONLY,
                "faucet-experience",
            )
        with pytest.raises(KohlerError):
            await device.async_get_presets()
        assert client.account_reads == 0


@pytest.mark.asyncio
async def test_a_404_after_the_list_was_read_means_no_presets() -> None:
    client = _Client(_listed(KETTLE), _http(404))
    device = FaucetDevice(client, DEVICE_ID)  # type: ignore[arg-type]
    await device.async_get_presets()
    assert await device.async_get_presets() == ([], "faucet-experience")
    assert client.account_reads == 0


@pytest.mark.parametrize(
    "refusal",
    [_http(400), _http(403), {"unexpected": True}],
    ids=["400", "403", "shape"],
)
@pytest.mark.asyncio
async def test_other_refusals_after_the_list_was_read_keep_the_last_presets(
    refusal: Any,
) -> None:
    client = _Client(_listed(KETTLE), refusal)
    device = FaucetDevice(client, DEVICE_ID)  # type: ignore[arg-type]
    await device.async_get_presets()
    with pytest.raises(KohlerError):
        await device.async_get_presets()
    assert client.account_reads == 0


@pytest.mark.parametrize(
    "refusal",
    [_http(404), _http(400), {"unexpected": True}],
    ids=["404", "400", "shape"],
)
@pytest.mark.asyncio
async def test_the_account_list_stands_in_only_where_the_faucets_never_worked(
    refusal: Any,
) -> None:
    """Guard: a faucet route Kohler refuses from the start still falls back."""
    client = _Client(refusal)
    device = FaucetDevice(client, DEVICE_ID)  # type: ignore[arg-type]
    presets, source = await device.async_get_presets()
    assert source == "customer-experience"
    assert [p.preset_id for p in presets] == ["old"]


# --- whether a failed ON may have turned the water on -----------------------------------


def _failed(cause: BaseException | None) -> HomeAssistantError:
    err = HomeAssistantError("command failed")
    err.__cause__ = cause
    return err


@pytest.mark.parametrize(
    ("cause", "did_nothing"),
    [
        (None, True),  # refused before it was sent: offline, handle closed, unloaded
        (AuthUnavailable("token endpoint unreachable"), True),
        (DeviceOffline("offline", {"statusCode": "900"}), True),
        (KohlerError("refused", {"statusCode": "906"}, 200), True),
        (_http(400), True),
        (_http(403), True),
        (_http(429), True),
        (NETWORK, False),  # no answer: it may have reached the faucet
        (_http(500), False),
        (_http(502), False),
        (_http(503), False),
        (_http(504), False),
    ],
    ids=[
        "before_sending",
        "sign_in",
        "offline",
        "refused_in_a_200",
        "400",
        "403",
        "429",
        "no_answer",
        "500",
        "502",
        "503",
        "504",
    ],
)
def test_only_a_refused_on_drops_its_limit(
    cause: BaseException | None, did_nothing: bool
) -> None:
    assert _not_carried_out(_failed(cause)) is did_nothing
