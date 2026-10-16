"""The pieces of the 2026-10-16 lifecycle review that need no Home Assistant around them.

Which entities a message re-renders, `Valve._track` once the coordinator is closing, what
counts as an active fault, and the diagnostics report's value checks. The behaviour they
add up to is exercised end to end in `integration/test_review_lifecycle.py`.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

from .conftest import make_controller, make_coordinator, make_valve


def _collect(name: str, coordinator) -> list:
    """Run a platform's real `async_setup_entry` and return what it added."""
    added: list = []
    hass = SimpleNamespace(data={"kohler_konnect": {"test": coordinator}})
    entry = SimpleNamespace(entry_id="test", data={}, options={})
    asyncio.run(
        importlib.import_module(
            f"custom_components.kohler_konnect.{name}"
        ).async_setup_entry(hass, entry, lambda e, *a, **k: added.extend(e))
    )
    return added


def _entity(coordinator, unique_id: str):
    for name in ("binary_sensor", "sensor", "switch", "number", "select"):
        for entity in _collect(name, coordinator):
            if entity.unique_id == unique_id:
                return entity
    raise AssertionError(unique_id)


def _writes(entity) -> list[int]:
    written: list[int] = []
    entity.async_write_ha_state = lambda: written.append(1)
    return written


# --------------------------------------------------------------------------- #
# Per-device updates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("changed", "writes"),
    [
        (None, 1),  # account-wide: a reseed, a cloud check, midnight
        (frozenset({"gcs-test0001"}), 1),  # its own valve
        (frozenset({"gcs-other0002"}), 0),  # another valve
        (frozenset({"hub-test0001"}), 0),  # a controller, unpaired
    ],
)
def test_a_valve_entity_renders_only_for_its_own_valve(valve_model, changed, writes):
    other = make_valve(valve_model, [31, 11, 1], device_id="gcs-other0002")
    coordinator = make_coordinator(
        [make_valve(valve_model, [31, 11, 1]), other], [make_controller(valve_model)]
    )
    entity = _entity(coordinator, "gcs-test0001_at_temperature")
    written = _writes(entity)
    coordinator.devices_changed = changed
    entity._handle_coordinator_update()
    assert len(written) == writes


def test_a_zone_sub_device_entity_follows_its_valve(valve_model):
    """A zone has no messages of its own; its entities are its valve's."""
    from custom_components.kohler_konnect.konnect.models import model_for_topology

    model = model_for_topology(3, 3)
    valve = make_valve(model, [31, 11, 1, 11, None, 21], zone_grouping="subdevices")
    coordinator = make_coordinator([valve], zone_grouping="subdevices")
    entity = _entity(coordinator, "gcs-test0001_zone_2_active")
    assert entity.device_info["identifiers"] == {
        ("kohler_konnect", "gcs-test0001_zone_2")
    }
    written = _writes(entity)
    coordinator.devices_changed = frozenset({"gcs-test0001"})
    entity._handle_coordinator_update()
    assert written == [1]


def test_a_paired_valve_also_renders_for_its_controller(valve_model):
    """One valve and one controller are taken to be one shower; the valve reads its warm-up."""
    coordinator = make_coordinator(
        [make_valve(valve_model, [31, 11, 1])], [make_controller(valve_model)]
    )
    entity = _entity(coordinator, "gcs-test0001_status")
    written = _writes(entity)
    coordinator.devices_changed = frozenset({"hub-test0001"})
    entity._handle_coordinator_update()
    assert written == [1]


def test_a_controller_entity_renders_only_for_its_own_controller(valve_model):
    coordinator = make_coordinator(
        [make_valve(valve_model, [31, 11, 1])],
        [
            make_controller(valve_model, device_id="hub-left", name="Anthem Plus Left"),
            make_controller(
                valve_model, device_id="hub-right", name="Anthem Plus Right"
            ),
        ],
    )
    entity = _entity(coordinator, "hub-left_problem")
    written = _writes(entity)
    for changed in (frozenset({"hub-right"}), frozenset({"gcs-test0001"})):
        coordinator.devices_changed = changed
        entity._handle_coordinator_update()
    assert written == []
    coordinator.devices_changed = frozenset({"hub-left"})
    entity._handle_coordinator_update()
    assert written == [1]


def test_the_stream_health_sensor_renders_for_every_message(valve_model):
    """Its `last_message_at` is the stream's, so any device's message moves it."""
    coordinator = make_coordinator(
        [make_valve(valve_model, [31, 11, 1])], [make_controller(valve_model)]
    )
    for unique_id in ("gcs-test0001_mqtt_connection", "hub-test0001_mqtt_connection"):
        entity = _entity(coordinator, unique_id)
        written = _writes(entity)
        coordinator.devices_changed = frozenset({"gcs-elsewhere"})
        entity._handle_coordinator_update()
        assert written == [1], unique_id


# --------------------------------------------------------------------------- #
# Nothing starts once the coordinator is closing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_track_starts_nothing_once_the_coordinator_is_closing():
    """Paho can deliver while the stream stops; what it would start must not run."""
    from custom_components.kohler_konnect.coordinator import Valve

    started: list[str] = []

    async def restore() -> None:
        started.append("restore")

    holder = SimpleNamespace(
        coordinator=SimpleNamespace(closed=True),
        hass=SimpleNamespace(loop=asyncio.get_running_loop()),
        _background_tasks=set(),
    )
    coro = restore()
    future = Valve._track(holder, coro)
    await asyncio.sleep(0)

    assert future.done() and future.cancelled()
    # Closed, so Python does not warn that it was never awaited — and it never ran.
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED
    assert started == []
    assert holder._background_tasks == set()


# --------------------------------------------------------------------------- #
# The valve's fault log
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("entry", "active"),
    [
        # The owner's, from Shower Left's log.
        ({"area": "Algorithm", "errorCode": "37", "isActive": True}, True),
        ({"errorCode": 37, "isActive": "true"}, True),
        ({"errorCode": "37", "isActive": False}, False),
        ({"errorCode": "37"}, False),
        # The app's own filter: no code, or code "0", is not a fault.
        ({"errorCode": "0", "isActive": True}, False),
        ({"errorCode": "", "isActive": True}, False),
        ({"isActive": True}, False),
        ("not an object", False),
    ],
)
def test_which_fault_log_entries_count_as_active(entry, active):
    from custom_components.kohler_konnect.coordinator import _fault_is_active

    assert _fault_is_active(entry) is active


@pytest.mark.parametrize("answer", [{}, {"errorDetails": "nonsense"}, None])
@pytest.mark.asyncio
async def test_a_fault_log_read_with_no_list_keeps_what_was_known(answer):
    """`{}` is the client's answer to a read it could not make, not "no faults"."""
    from custom_components.kohler_konnect.coordinator import Valve

    async def _read(_device_id):
        return answer

    known = [{"code": "37", "area": "Algorithm", "valve_id": "Valve 1"}]
    valve = object.__new__(Valve)
    valve.name = "Shower Left"
    valve.gcs_device = SimpleNamespace(device_id="gcs-left")
    valve.coordinator = SimpleNamespace(
        client=SimpleNamespace(async_get_gcs_diagnostics=_read)
    )
    valve.active_faults = list(known)

    await valve.async_refresh_faults()
    assert valve.active_faults == known


# --------------------------------------------------------------------------- #
# The diagnostics report's value checks
# --------------------------------------------------------------------------- #
def test_version_blocks_describe_identity_rather_than_copy_it():
    from custom_components.kohler_konnect.diagnostics import _guarded_block

    block = {
        "firmwareType": "Application",
        "updatedVersion": "2.20",
        "deviceId": "gcs-sio32343h7",
        "url": "https://fw.example.net/image.bin?sv=1&sig=abc",
        "history": [{"version": 10, "by": "0123456789abcdef0123"}],
    }
    assert _guarded_block(block) == {
        "firmwareType": "Application",
        "updatedVersion": "2.20",
        "deviceId": "<str, 14 chars>",
        "url": "<str, 45 chars>",
        "history": [{"version": 10, "by": "<str, 20 chars>"}],
    }
    assert _guarded_block("00.74") == "00.74"


def test_light_groups_keep_kohlers_letters_and_label_the_owners_names():
    from custom_components.kohler_konnect.diagnostics import _light_groups_report

    # `lightgroupA` reaches the state as `a`; a REST display name, as itself.
    assert _light_groups_report({"a": True, "vanitylights": False}) == {
        "a": True,
        "group_1": False,
    }
    # Numbered in a stable order, whatever order the groups were heard in.
    assert _light_groups_report({"vanitylights": False, "bathtub": True}) == {
        "group_1": True,
        "group_2": False,
    }
