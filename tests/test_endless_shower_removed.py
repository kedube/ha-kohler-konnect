"""Endless Shower was removed on 2026-10-08. These pin the removal.

The upgrade cleanup that went with it — config keys, registry rows, Repairs cards — went in
turn when the integration became `kohler_konnect`: every install starts from a new entry.

What stays: the zone clock behind the `flowing_for_seconds` / `seconds_remaining`
attributes, and `Valve.outlet_run_times`, now read from the outlet records rather than a
learned copy.
"""

from __future__ import annotations

import importlib
import json
import pathlib
from types import SimpleNamespace

import pytest

from .conftest import make_coordinator, make_valve
from .test_entities import collect


def _model(sku: str = "K-28210"):
    from custom_components.kohler_konnect.konnect.models import get_valve_model

    return get_valve_model(sku)


def test_no_endless_shower_switch_is_created():
    valve = make_valve(_model(), [31, 11, 1])
    switches = collect("switch", make_coordinator([valve]))
    assert "Endless Shower" not in {e.name for e in switches}
    assert not any(e.unique_id.endswith("_keep_water_running") for e in switches)


def test_the_detector_module_and_its_constants_are_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "custom_components.kohler_konnect.konnect.runtime_cutoff"
        )
    from custom_components.kohler_konnect import const

    for name in (
        "CONF_RESTART_ON_RUNTIME_CUTOFF",
        "CONF_OUTLET_RUN_TIMES",
        "ISSUE_NOT_SET_UP",
        "ISSUE_DURATION_MISMATCH",
        "ENABLE_CUTOFF_DEBUG_LOG",
    ):
        assert not hasattr(const, name), name


def test_the_retired_repairs_have_no_text_left():
    root = pathlib.Path("custom_components/kohler_konnect")
    for path in [
        root / "strings.json",
        *sorted((root / "translations").glob("*.json")),
    ]:
        issues = json.loads(path.read_text(encoding="utf-8")).get("issues", {})
        assert "endless_shower_not_set_up" not in issues, path
        assert "durations_differ" not in issues, path


# --------------------------------------------------------------------------- #
# What stays
# --------------------------------------------------------------------------- #
def test_the_zone_clock_times_the_zone_not_the_outlet(monkeypatch):
    from custom_components.kohler_konnect.konnect import zone_clock

    now = [100.0]
    monkeypatch.setattr(zone_clock.time, "monotonic", lambda: now[0])
    clock = zone_clock.ZoneClock()

    clock.update({1: True, 2: False})
    now[0] = 160.0
    # Same zone, still running — an outlet change does not restart the valve's timer.
    clock.update({1: True, 2: False})
    assert clock.flowing_for(1) == 60.0
    assert clock.flowing_for(2) is None

    clock.update({1: False})
    assert clock.flowing_for(1) is None

    clock.update({1: True})
    clock.forget()
    assert clock.flowing_for(1) is None


def test_a_paused_zone_is_not_running():
    from custom_components.kohler_konnect.coordinator import Valve
    from custom_components.kohler_konnect.konnect.zone_clock import ZoneClock

    words = {
        1: SimpleNamespace(outlet_mask=0x01, paused=True),
        2: SimpleNamespace(outlet_mask=0x02, paused=False),
    }
    holder = SimpleNamespace(
        gcs_state=SimpleNamespace(zone_word=words.get),
        model=SimpleNamespace(zones=(1, 2)),
        _zone_clock=ZoneClock(),
    )
    Valve._update_zone_clock(holder)
    assert holder._zone_clock.flowing_for(1) is None
    assert holder._zone_clock.flowing_for(2) is not None


def test_run_times_come_from_the_outlet_records():
    from custom_components.kohler_konnect.coordinator import Valve
    from custom_components.kohler_konnect.konnect.state import GcsState, OutletLimits

    model = _model()
    state = GcsState(model=model)
    state.outlet_limits[0] = OutletLimits(0, 16, 200, 900)
    state.outlet_limits[1] = OutletLimits(1, 16, 200, None)
    holder = SimpleNamespace(model=model, gcs_state=state)
    # Outlet 2 has a record but no run time yet; outlet 3 has no record at all.
    assert Valve.outlet_run_times.fget(holder) == {1: 900}
