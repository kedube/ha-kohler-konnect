"""An Anthem valve's touch-panel records, offline: how they read, and the Control Panel's
write, field for field (`docs/protocol/gcs_valve.md` §6)."""

from __future__ import annotations

import pytest

from custom_components.kohler_konnect.konnect.ui_config import (
    UnwritableRecords,
    control_panel_writes,
    flow_control,
    merge_push,
    panel_language,
    panel_units,
    read_back,
    switch_on,
    wake_screen,
)


def record(**fields: object) -> dict[str, object]:
    return {
        "ui": "UI1",
        "temperatureUnits": "F",
        "flowUnits": "Gallons",
        "standbyLighting": "Disabled",
        "timeFormat": "12",
        "demoMode": "Disabled",
        "bathFillPresetId": "0",
        "delayStart": "Disabled",
        "toggleOutlets": "Disabled",
        "waterSavingMode": "Enabled",
        "accessibilityMode": "Disabled",
        "backLight": "5",
        "hapticFeedback": "Enabled",
        "sounderVolume": "Disabled",
        "defaultHomeScreen": "1",
        "proximitySensorMode": "1",
        "proximitySensorDistance": "",
        "notification": "5",
        "defaultMemory": "1",
        "language": "en",
        "languageRegion": "US",
        "serialNo": "UI-SECRET",
        "firmware": "2.2",
        **fields,
    }


def test_a_record_reads_as_the_app_reads_it() -> None:
    panel = record()
    assert (panel_language(panel), panel_units(panel)) == ("en_us", "us")
    assert (
        switch_on(panel, "hapticFeedback"),
        switch_on(panel, "waterSavingMode"),
    ) == (
        True,
        True,
    )
    assert switch_on(record(hapticFeedback="1"), "hapticFeedback") is None
    # The wake screen as the app maps it: anything unknown is the outlets.
    for value, screen in (
        ("0", "home"),
        ("192", "home"),
        ("194", "favorites"),
        ("255", "outlets"),
        ("x", "outlets"),
    ):
        assert wake_screen(record(defaultHomeScreen=value)) == screen
    assert flow_control(panel) is True  # 5 has bit 2 set
    assert flow_control(record(notification="1")) is False


def test_the_control_panel_writes_every_record_whole() -> None:
    first, second = record(), record(ui="UI2", backLight="9", hapticFeedback="Disabled")
    writes = control_panel_writes(
        [first, second], {"backLight": "7"}, second_valve=True
    )
    assert writes[0] == {
        "ui": "UI1",
        "standbyLighting": "Disabled",
        "timeFormat": "12",
        "demoMode": "Disabled",
        "bathFillPresetId": "0",
        "delayStart": "Disabled",
        "toggleOutlets": "Disabled",
        "sounderVolume": "Disabled",
        "proximitySensorMode": "1",
        "proximitySensorDistance": "",
        "notification": "5",
        "defaultMemory": "1",
        # Read `Enabled`, written `1`; the shared settings from the first panel.
        "waterSavingMode": "1",
        "temperatureUnits": "F",
        "flowUnits": "Gallons",
        "accessibilityMode": "0",
        "hapticFeedback": "1",
        "backLight": "7",
        # The outlets, with the second valve's flag.
        "defaultHomeScreen": "193",
        "language": "en",
        "languageRegion": "US",
        # Sent empty, as the app sends them.
        "standByLighting": "",
        "proximitySensorTime": "",
        "status": "",
        "firmware": "",
        "bootloder": "",
        "installedDate": "",
        "model": "",
        "serialNo": "",
    }
    # The second panel keeps its own name; the shared settings are the first's.
    assert (writes[1]["ui"], writes[1]["hapticFeedback"], writes[1]["backLight"]) == (
        "UI2",
        "1",
        "7",
    )
    on = control_panel_writes([first], {}, second_valve=False, flow_control_on=False)
    assert (on[0]["notification"], on[0]["defaultHomeScreen"]) == ("1", "1")


@pytest.mark.parametrize(
    "panel",
    [
        record(hapticFeedback="1"),
        record(waterSavingMode="enabled"),
        record(defaultMemory="255"),
        record(ui=None),
        record(temperatureUnits="C"),
        record(language=None),
    ],
)
def test_a_record_the_app_wouldn_t_write_from_is_refused(panel: dict) -> None:
    with pytest.raises(UnwritableRecords):
        control_panel_writes([panel], {"backLight": "6"}, second_valve=False)
    with pytest.raises(UnwritableRecords):
        control_panel_writes([], {"backLight": "6"}, second_valve=False)


def test_a_push_merges_into_the_record_it_names_and_reads_back() -> None:
    held = [record(), record(ui="UI2")]
    merged = merge_push(held, {"UI": "UI2", "backLight": "3"})
    assert merged is not None and merged[1]["backLight"] == "3"
    assert merge_push(held, {"UI": "UI9", "backLight": "3"}) is None
    written = control_panel_writes(
        [record()], {"hapticFeedback": "0"}, second_valve=False
    )[0]
    shown = read_back(record(), written)
    assert (shown["hapticFeedback"], shown["serialNo"]) == ("Disabled", "UI-SECRET")
