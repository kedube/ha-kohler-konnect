"""Decoding what the cloud sends into state, and encoding what goes back.

Everything here is pure — the valve word codec, the state objects fed by MQTT envelopes and
REST seeds, and the reload decision — so it runs against the shipped code with no Home
Assistant at all. The payload shapes are the ones the protocol docs record from captures;
the malformed ones are what a schema change, a truncated body or a firmware quirk would
hand the same code, and the rule throughout is that those are ignored rather than allowed
to raise inside a callback or a setup seed.
"""

from __future__ import annotations

import pytest

from custom_components.kohler_konnect.konnect.const import (
    MSG_GCS_DISPENSED_VOLUME,
    MSG_GCS_OUTLET_CONFIG,
    MSG_GCS_PRESET_STATUS,
    MSG_GCS_SOLO_STATUS,
    MSG_GCS_WARMUP_STATUS,
    MSG_HUB_FAVORITE,
    MSG_HUB_FAVORITES_SNAPSHOT,
    MSG_HUB_LIGHT,
    MSG_HUB_MUSIC,
    MSG_HUB_SHOWER_EXPERIENCE,
    MSG_HUB_SHOWER_VALVE,
    MSG_HUB_STEAM,
)
from custom_components.kohler_konnect.konnect.entry_reload import reload_signature
from custom_components.kohler_konnect.konnect.models import get_valve_model
from custom_components.kohler_konnect.konnect.mqtt import Envelope
from custom_components.kohler_konnect.konnect.state import (
    GcsPreset,
    GcsState,
    HubState,
    outlet_limits_from_settings,
)
from custom_components.kohler_konnect.konnect.valve_hex import (
    FAHRENHEIT_TO_TENTHS_C,
    UNUSED_VALVE_WORD,
    VALVE1_PREFIX,
    VALVE2_PREFIX,
    ValveHexError,
    celsius_to_unit,
    decode_word,
    encode_pair,
    encode_preset_word,
    encode_shower,
    encode_word,
    normalize_word,
    outlet_mask,
    pause_pair,
    stop_pair,
    unit_to_celsius,
)

SINGLE = get_valve_model("K-28210")  # 3 outlets, one zone
SPLIT = get_valve_model("K-28211")  # 2 + 2
DOUBLE = get_valve_model("K-28212")  # 3 + 3


def envelope(code: str, *attributes: object, sku: str = "GCS", **raw) -> Envelope:
    """An envelope as the stream hands it on, timestamped 1000.0."""
    return Envelope(
        sku=sku,
        device_id="gcs-test0001" if sku == "GCS" else "hub-test0001",
        code=code,
        attributes=list(attributes),  # type: ignore[arg-type]
        received_at=1000.0,
        raw=raw or {"data": {"code": code}},
    )


def solo(primary: str, secondary: str = UNUSED_VALVE_WORD, **fields) -> Envelope:
    return envelope(
        MSG_GCS_SOLO_STATUS,
        {
            "code": MSG_GCS_SOLO_STATUS,
            "primaryValve1": primary,
            "secondaryValve1": secondary,
            **fields,
        },
    )


# =========================================================================== #
# The valve word
# =========================================================================== #


def test_a_status_word_decodes_setpoints_mask_and_the_live_feedback_half():
    """Bytes 4-7 are what the valve is actually doing; losing them loses the fault code."""
    word = decode_word("0190c80101a08c05")
    assert (word.prefix, word.temperature_celsius, word.flow_percent) == (
        1,
        40.0,
        100.0,
    )
    assert word.outlet_mask == 1 and not word.paused
    assert word.measured_temperature_celsius == 41.6
    assert word.measured_flow_percent == 70.0
    # Byte 6 on the device's own 0-50 scale, as gcs-state would show it.
    assert word.measured_flow_setpoint == 35.0
    assert word.error_code == 5
    # The wire word is kept as it arrived, uppercased, for display.
    assert word.raw == "0190C80101A08C05"


def test_an_eight_character_word_has_no_measurements_rather_than_zeroes():
    """A zero would read as a confident 0 °C; absent is the honest answer."""
    word = decode_word("0184C801")
    assert word.measured_temperature_celsius is None
    assert word.measured_flow_setpoint is None
    assert word.error_code is None


def test_status_bits_in_byte0_and_byte3_are_read_but_never_mistaken_for_the_mask():
    """atFlow/atTemp share byte 0 with the valve index; pause and error share byte 3.

    0x41 is "paused, outlet 1 still assigned" — treating 0x40 as a whole-mask sentinel
    made that unrepresentable and misread a paused shower as running.
    """
    word = decode_word("0D84C8C1")
    assert word.at_flow and word.at_temperature and word.error_flag
    assert word.paused and word.outlet_mask == 0x01
    assert word.temperature_celsius == 38.8
    assert not word.stopped
    assert word.outlet(0) and not word.outlet(1) and not word.outlet(2)


def test_an_idle_word_is_stopped_and_a_paused_empty_word_is_not():
    """A pause holds the session open; only mask 0x00 without the pause bit is a stop."""
    assert decode_word("0184C800").stopped
    assert not decode_word("0184C840").stopped
    assert decode_word("0184C800").flow_setpoint == 50.0


@pytest.mark.parametrize("value", ["0184C8", "0184C8G1", "", None, "zzzzzzzz"])
def test_a_malformed_word_is_refused_rather_than_decoded(value):
    """This input reaches something that opens water valves."""
    with pytest.raises(ValveHexError):
        decode_word(value)
    with pytest.raises(ValveHexError):
        normalize_word(value)


def test_normalising_keeps_only_the_command_half():
    """The second half is sensor feedback; only the first eight characters command."""
    assert normalize_word("0184c80100000001") == "0184C801"


@pytest.mark.parametrize(
    ("prefix", "celsius", "flow", "mask", "paused"),
    [
        (VALVE1_PREFIX, 38.8, 100.0, 0b001, False),
        (VALVE2_PREFIX, 40.0, 50.0, 0b100, False),
        (VALVE1_PREFIX, 25.6, 8.0, 0b011, True),
        (VALVE2_PREFIX, 48.8, 74.5, 0b111, False),
        (VALVE1_PREFIX, 4.0, 100.0, 0b000, True),
    ],
)
def test_encoding_then_decoding_gives_back_what_was_asked(
    prefix, celsius, flow, mask, paused
):
    """The codec's two halves must agree, or every write reads back as something else."""
    word = decode_word(encode_word(prefix, celsius, flow, mask, paused=paused))
    assert word.prefix & 0xF0 == prefix & 0xF0
    assert word.temperature_celsius == celsius
    assert word.flow_percent == flow
    assert word.outlet_mask == mask
    assert word.paused is paused
    # Status the device reports is never something a write asserts.
    assert not (word.at_temperature or word.at_flow or word.error_flag)


def test_out_of_range_setpoints_are_clamped_to_what_the_device_accepts():
    """48.8 °C is the app's own ceiling, and flow bottoms out at byte 16, not 0."""
    assert encode_word(VALVE1_PREFIX, 60.0, 0, 0) == "01E81000"
    # Below zero is full cold, still addressed to valve 1.
    assert encode_word(VALVE1_PREFIX, -5.0, 100, 0) == "0000C800"


def test_pause_and_skip_warmup_are_flags_beside_the_mask():
    """Bath fill sets 0x80 on top of the outlets, so both flags must sit beside the mask."""
    word = encode_word(VALVE1_PREFIX, 38.8, 100, 0x01, paused=True, skip_warmup=True)
    assert word == "0184C8C1"


def test_folding_the_pause_bit_into_the_mask_is_refused():
    """A caller passing 0x40 as a mask has a bug worth surfacing, not reinterpreting."""
    with pytest.raises(ValveHexError, match="paused=True"):
        encode_word(VALVE1_PREFIX, 38.8, 100, 0x40)


def test_preset_words_put_outlets_where_a_preset_keeps_them():
    """A preset's outlets sit at 0x04/0x08/0x10 of byte 0, not in a byte 3."""
    assert encode_preset_word(38.8, 78, 0b001) == "05849C"
    assert encode_preset_word(40.0, 100, 0b100) == "1190C8"
    with pytest.raises(ValveHexError):
        encode_preset_word(38.8, 100, 0b1000)


def test_more_than_three_outlets_on_one_valve_is_refused():
    """A valve has three outlet bits; a fourth flag would be a bit no outlet answers to."""
    assert outlet_mask(True, False, True) == 0b101
    with pytest.raises(ValveHexError):
        outlet_mask(True, True, True, True)


def test_the_pair_follows_the_models_split_not_a_fixed_three_and_three():
    """On a 2+2 K-28211, outlet 3 is valve 2's first — commanding bit 2 of valve 1 is wrong."""
    assert encode_pair(SPLIT, 38.8, 100, [False, True, True, False]) == (
        "0184C802",
        "1184C801",
    )


def test_a_single_valve_model_sends_the_ignore_word_for_valve_two():
    """Only a valve that does not exist gets the all-zero word, which addresses none."""
    assert encode_pair(SINGLE, 38.8, 100, [True, False, False]) == (
        "0184C801",
        UNUSED_VALVE_WORD,
    )
    assert stop_pair(SINGLE) == ("017CC800", UNUSED_VALVE_WORD)


def test_a_custom_shower_takes_per_zone_flags_and_temperatures():
    """Zone-local flags cannot be mis-mapped by the model-dependent global numbering."""
    words = encode_shower(DOUBLE, {1: 38.8, 2: 40.0}, 100, {1: [True], 2: [0, 0, 1]})
    assert words == ("0184C801", "1190C804")


def test_a_zone_left_out_of_a_custom_shower_is_closed_at_zone_ones_temperature():
    """Never "left as it was": that is exactly the report that lags a write."""
    assert encode_shower(DOUBLE, {1: 38.8}, 100, {1: [True]}) == (
        "0184C801",
        "1184C800",
    )


@pytest.mark.parametrize(
    ("model", "flags"),
    [
        # A zone 2 flag on a single-zone valve is an error, not silently dropped.
        (SINGLE, {1: [True], 2: [True]}),
        # Outlet 3 of a two-outlet zone would land on another outlet if shifted.
        (SPLIT, {1: [False, False, True]}),
    ],
)
def test_a_custom_shower_naming_an_outlet_the_zone_lacks_is_refused(model, flags):
    """Dropping or shifting the flag would ignore, or open, a different outlet."""
    with pytest.raises(ValveHexError, match="beyond was requested"):
        encode_shower(model, {1: 38.8}, 100, flags)


def test_a_custom_shower_on_a_single_zone_valve_sends_the_ignore_word():
    """There is no second valve to close, so it gets the word that addresses none."""
    assert encode_shower(SINGLE, {1: 38.8}, 100, {1: [False, True]}) == (
        "0184C802",
        UNUSED_VALVE_WORD,
    )


def test_stop_clears_the_outlets_and_pause_keeps_the_assignment():
    """Stop is mask 0x00; pause is 0x40 with the outlets it will resume to."""
    assert stop_pair(DOUBLE) == ("017CC800", "117CC800")
    assert pause_pair(DOUBLE, outlet_mask=0x01) == ("017CC841", "117CC841")


def test_fahrenheit_goes_through_kohlers_table_and_round_trips_on_every_entry():
    """The table's low bias is what makes the app's own display round trip idempotent."""
    assert unit_to_celsius(102, "Fahrenheit") == 38.8  # arithmetic would give 38.9
    for fahrenheit, tenths in FAHRENHEIT_TO_TENTHS_C.items():
        shown = round(celsius_to_unit(tenths / 10, "Fahrenheit"))
        assert unit_to_celsius(shown, "Fahrenheit") == tenths / 10, fahrenheit


def test_temperatures_the_table_lacks_fall_back_to_arithmetic_never_to_zero():
    """Kohler's own table returns 0 there, which for a water valve means full cold."""
    assert unit_to_celsius(102.5, "F") == pytest.approx(39.1667, abs=1e-4)
    assert unit_to_celsius(130, "F") == pytest.approx(54.444, abs=1e-3)
    # Celsius is passed straight through.
    assert unit_to_celsius(38.5, "Celsius") == 38.5
    assert celsius_to_unit(38.84, "Celsius") == 38.8


# =========================================================================== #
# The valve's state, from MQTT
# =========================================================================== #


def test_a_solo_status_decodes_both_zones_and_the_session_fields():
    """The valve's main push: everything the shower entities show comes from it."""
    state = GcsState(DOUBLE, "Fahrenheit")
    assert state.apply_envelope(
        solo(
            "0184C80100000001",
            "1190C84400000003",
            warmUpStatus="warmUpNotInProgress",
            currentSystemState="showerInProgress",
            presetOrExperienceId="3",
            totalVolume="537557808",
            totalFlow="2056.0",
        )
    )
    assert state.last_update == 1000.0
    # Zone 2 is paused holding outlet 3: assigned, but no water from it.
    assert state.outlets == [True, False, False, False, False, False]
    assert state.assigned_outlets == [True, False, False, False, False, True]
    assert state.zone_outlets(2) == [False, False, False]
    assert state.zone_outlets(2, flowing=False) == [False, False, True]
    # Water flows somewhere, so the system is running, not paused.
    assert state.is_running and not state.is_paused
    assert state.error_codes == {"zone1": 1, "zone2": 3}
    assert state.system_state == "showerInProgress"
    # "NotInProgress" ends with "InProgress"; a suffix test pinned warm-up on for ever.
    assert state.warmup_in_progress is False
    assert state.active_preset_id == 3
    assert state.total_flow == 2056.0


def test_every_zone_paused_reads_as_paused_and_nothing_flowing():
    """A paused session must not read as running, and its idle flow byte is no setting."""
    state = GcsState(DOUBLE)
    state.apply_envelope(solo("0184C841", "1184C840"))
    assert state.is_paused and not state.is_running
    assert not state.flow_is_live


def test_warmup_in_progress_and_no_preset_are_read_from_their_exact_values():
    """ "0" is no preset, and only the exact warm-up string means one is running."""
    state = GcsState(SINGLE)
    state.apply_envelope(
        solo("0184C801", warmUpStatus="warmUpInProgress", presetOrExperienceId="0")
    )
    assert state.warmup_in_progress is True
    # "0" means no preset, not preset zero.
    assert state.active_preset_id is None


def test_an_undecodable_valve_word_keeps_the_last_good_state_but_proves_life():
    """A garbled word must not blank the zone; the message still says the valve talks."""
    state = GcsState(DOUBLE)
    state.apply_envelope(solo("0184C801", "1184C802"))
    before = (state.valve1, state.valve2)

    late = Envelope(
        "GCS",
        "gcs-test0001",
        MSG_GCS_SOLO_STATUS,
        [{"primaryValve1": "nonsense"}],
        2000,
    )
    assert state.apply_envelope(late)
    assert (state.valve1, state.valve2) == before
    assert state.last_update == 2000


def test_an_undecodable_second_word_keeps_zone_two_as_it_was():
    """One garbled word must not close a zone that is still running."""
    state = GcsState(DOUBLE)
    state.apply_envelope(solo("0184C800", "1184C802"))
    state.apply_envelope(solo("0184C801", "garbage!"))
    assert state.valve1.outlet_mask == 1
    assert state.valve2.outlet_mask == 2


def test_a_single_zone_valve_ignores_whatever_rides_in_the_second_word():
    """A zone 2 it does not have would add outlets no entity can address."""
    state = GcsState(SINGLE)
    state.apply_envelope(solo("0184C801", "1184C802"))
    assert state.valve2 is None
    assert state.zone_word(2) is None


def test_a_valve_not_yet_heard_from_answers_unknown_or_closed_never_a_guess():
    """Before the seed lands every entity reads this; none of it may raise or invent."""
    state = GcsState(DOUBLE)
    assert state.zone_outlets(1) == [False, False, False]
    assert state.flow_percent is None
    assert not state.flow_is_live
    assert not state.is_paused and not state.is_running
    # No announcement yet: the device's documented range stands in.
    assert state.zone_flow_limits(2) == (16, 200)


def test_an_unreadable_active_preset_reads_as_none():
    """Unreadable is not a preset, and must not leave the last one latched either."""
    state = GcsState(SINGLE, active_preset_id=5)
    state.apply_envelope(solo("0184C801", presetOrExperienceId="five"))
    assert state.active_preset_id is None


def test_a_solo_message_with_no_attributes_changes_nothing():
    """Nothing to decode means nothing changes — and no exception in the callback."""
    state = GcsState(SINGLE)
    state.apply_envelope(envelope(MSG_GCS_SOLO_STATUS))
    assert state.valve1 is None
    assert state.last_update == 1000.0


def test_a_solo_attribute_without_its_code_is_still_read():
    """Firmware that leaves `code` off the attribute still carries the words."""
    state = GcsState(SINGLE)
    state.apply_envelope(envelope(MSG_GCS_SOLO_STATUS, {"primaryValve1": "0184C802"}))
    assert state.outlets == [False, True, False]


def test_messages_for_other_products_are_not_the_valves():
    """One stream carries every product; another's message must not move the valve's clock."""
    state = GcsState(SINGLE)
    assert state.apply_envelope(solo("0184C801")) is True
    hub = Envelope("HUB", "hub-1", MSG_GCS_SOLO_STATUS, [{"primaryValve1": "0"}], 5)
    assert state.apply_envelope(hub) is False
    assert state.last_update == 1000.0


def test_an_unknown_valve_message_still_counts_as_hearing_from_it():
    """`last_update` is liveness, not a change feed — see `apply_envelope`."""
    state = GcsState(SINGLE)
    assert state.apply_envelope(envelope("DEVICE_REBOOT_STS", {"code": "x"}))
    assert state.last_update == 1000.0
    assert state.valve1 is None


@pytest.mark.parametrize("key", ["warmup", "warmUp", "warmUpMode"])
def test_the_warmup_message_is_read_whichever_spelling_carries_the_mode(key):
    """MQTT spells it `warmup`; matching only the REST spelling made the handler a no-op."""
    state = GcsState(SINGLE)
    state.apply_envelope(envelope(MSG_GCS_WARMUP_STATUS, {key: "warmUpAllOutlets"}))
    assert state.warmup_mode == "warmUpAllOutlets"


def test_a_warmup_message_without_a_mode_leaves_the_mode_alone():
    """Absent is not "disabled"; blanking the mode would look like the valve losing it."""
    state = GcsState(SINGLE, warmup_mode="warmUpDisabled")
    state.apply_envelope(envelope(MSG_GCS_WARMUP_STATUS, {"code": "GCS_WARM_STS"}))
    state.apply_envelope(envelope(MSG_GCS_WARMUP_STATUS))
    assert state.warmup_mode == "warmUpDisabled"


def test_preset_pushes_create_rename_and_delete_slots():
    """A delete arrives as the same slot with an empty name."""
    state = GcsState(SINGLE)
    state.apply_preset_list(
        {
            "gcsPresetExperienceDetails": [
                {"presetId": "20", "title": "Cool Down", "isExperience": "True"}
            ]
        }
    )
    state.apply_envelope(
        envelope(
            MSG_GCS_PRESET_STATUS,
            {"presetId": "2", "name": " Morning "},
            {"presetId": "20", "name": "Cool Down Plus"},
            {"presetId": "not a number", "name": "x"},
            "not an object",
        )
    )
    assert state.presets[2] == GcsPreset(2, "Morning")
    # The push carries no experience flag; the seeded one survives a rename.
    assert state.presets[20].is_experience
    assert state.experience_by_name("cool down PLUS").preset_id == 20

    state.apply_envelope(envelope(MSG_GCS_PRESET_STATUS, {"presetId": "2", "name": ""}))
    assert state.presets[2].is_empty
    assert state.preset_by_name("morning") is None


def test_outlet_announcements_with_unreadable_bounds_are_skipped():
    """A guessed bound would widen a flow slider past what the outlet accepts."""
    state = GcsState(SINGLE)
    state.apply_envelope(
        envelope(
            MSG_GCS_OUTLET_CONFIG,
            {"outLetId": "x", "minimumFlowRate": "16", "maximumFlowRate": "200"},
            {"outLetId": "1", "minimumFlowRate": None, "maximumFlowRate": "200"},
            "not an object",
            {
                "outLetId": "0",
                "minimumFlowRate": "16",
                "maximumFlowRate": "180",
                "maximumRunTime": "soon",
                "defaultFlowRate": "",
                "outLetType": "?",
                "maximumOutletTemperature": "hot",
            },
        )
    )
    assert list(state.outlet_limits) == [0]
    limits = state.outlet_limits[0]
    assert (limits.minimum_flow_byte, limits.maximum_flow_byte) == (16, 180)
    # Unreadable optional fields are "not learned", never a made-up number.
    assert limits.maximum_run_time is None
    assert limits.outlet_type is None
    assert limits.maximum_temperature_tenths is None
    assert state.zone_flow_limits(1) == (16, 180)


def test_a_dispensed_volume_message_without_a_volume_changes_nothing():
    """A reading that is not there is not a zero."""
    state = GcsState(SINGLE)
    state.apply_envelope(envelope(MSG_GCS_DISPENSED_VOLUME, {"code": "x"}))
    assert state.dispensed_volume is None


def test_the_temperature_is_shown_in_the_accounts_unit():
    """The valve speaks Celsius whatever the account says; conversion is at the edge."""
    state = GcsState(SINGLE, "Fahrenheit")
    assert state.temperature is None
    state.apply_envelope(solo("0184C801"))
    assert state.temperature == 101.8
    assert GcsState(SINGLE, "Celsius", valve1=state.valve1).temperature == 38.8


def test_system_level_flags_come_from_the_primary_word():
    """The secondary valve never asserts at-temperature; zone 1 speaks for the system."""
    state = GcsState(DOUBLE)
    assert state.at_temperature is None and state.at_flow is None
    assert state.has_fault is None
    state.apply_envelope(solo("0184C801", "1D84C881"))
    assert state.at_temperature is False and state.at_flow is False
    # An error flag on either word is a fault.
    assert state.has_fault is True


# =========================================================================== #
# The valve's state, from REST
# =========================================================================== #


def test_the_rest_seed_builds_both_zones_from_the_flags_and_setpoints():
    """Cold start: until the shower next changes, this read is all the entities have."""
    state = GcsState(DOUBLE)
    state.apply_rest_state(
        {
            "state": {
                "valve1": {
                    "out1": "1",
                    "out3": "1",
                    "temperatureSetpoint": "40.5",
                    "flowSetpoint": "25",
                },
                "valve2": {"out2": 1, "pauseFlag": "1", "temperatureSetpoint": None},
                "totalFlow": "6283.25",
                "currentSystemState": "FirmwareUpdate",
                "presetOrExperienceId": "4",
            }
        }
    )
    assert state.valve1.outlet_mask == 0b101
    assert state.valve1.temperature_celsius == 40.5
    # gcs-state reports flow on the device's 0-50 scale.
    assert state.valve1.flow_percent == 50.0
    # Kohler sends these as strings, but an int 1 means the same.
    assert state.valve2.outlet_mask == 0b010
    assert state.valve2.paused
    # An absent setpoint defaults rather than leaving the word unbuildable.
    assert state.valve2.temperature_celsius == 38.0
    assert state.valve2.flow_percent == 0.0
    assert state.total_flow == 6283.25
    assert state.firmware_updating
    assert state.active_preset_id == 4
    assert state.last_update is not None


def test_unreadable_rest_setpoints_fall_back_instead_of_failing_the_seed():
    """A schema change must not fail setup; MQTT corrects whatever the seed gets wrong."""
    state = GcsState(SINGLE)
    state.apply_rest_state(
        {
            "state": {
                "valve1": {"temperatureSetpoint": "warm", "flowSetpoint": [50]},
                "valve2": {"out1": "1"},
                "warmUpState": None,
            }
        }
    )
    assert state.valve1.temperature_celsius == 38.0
    assert state.valve1.flow_percent == 0.0
    # A single-zone valve has no zone 2 to seed.
    assert state.valve2 is None


def test_a_rest_seed_with_a_non_object_zone_skips_only_that_zone():
    """One malformed zone must not cost the other."""
    state = GcsState(DOUBLE)
    state.apply_rest_state({"state": {"valve1": "1", "valve2": {"out1": "1"}}})
    assert state.valve1 is None
    assert state.valve2.outlet_mask == 1


def test_the_preset_list_replaces_every_slot_and_reads_its_string_flags():
    """Deleted while nobody was listening must disappear, not linger."""
    state = GcsState(SINGLE)
    state.apply_envelope(
        envelope(MSG_GCS_PRESET_STATUS, {"presetId": "7", "name": "x"})
    )
    payload = {
        "gcsPresetExperienceDetails": [
            {"presetId": "1", "title": "Default"},
            {"presetId": "2", "logicalName": "Evening"},
            # "False" is truthy; only the text decides.
            {"presetId": "3", "title": "Steam", "isExperience": "False"},
            {"presetId": "17", "title": "Cool Down", "isExperience": "True"},
            {"presetId": "?", "title": "broken"},
            "not an object",
        ]
    }
    assert state.apply_preset_list(payload) is True
    assert sorted(state.presets) == [1, 2, 3, 17]
    assert state.apply_preset_list(payload) is False
    # Preset 1 is the hidden default; the app does not list it either.
    assert [p.name for p in state.selectable_presets(hidden=(1,))] == [
        "Evening",
        "Steam",
    ]
    assert [p.name for p in state.experiences()] == ["Cool Down"]
    assert state.preset_by_name("default", hidden=(1,)) is None
    assert state.preset_by_name("DEFAULT").preset_id == 1
    assert state.experience_by_name("nothing") is None


@pytest.mark.parametrize("payload", [None, [], {"gcsPresetExperienceDetails": {}}])
def test_an_unreadable_preset_list_leaves_the_slots_alone(payload):
    """Replacing the slots with nothing would delete every preset from the select."""
    state = GcsState(SINGLE, presets={1: GcsPreset(1, "Default")})
    assert state.apply_preset_list(payload) is False
    assert list(state.presets) == [1]


def test_outlet_limits_skip_records_they_cannot_read():
    """Display units in, byte scale out — and a record without bounds is no record."""
    limits = outlet_limits_from_settings(
        {
            "valveSettings": [
                "not a valve",
                {
                    "outletConfigurations": [
                        "not an outlet",
                        {"outLetId": None, "minimumFlowrate": "4"},
                        {"outLetId": "1", "minimumFlowrate": "4"},
                        {
                            "outLetId": "2",
                            "minimumFlowrate": "4",
                            "maximumFlowrate": "50",
                            "maximumRuntime": "?",
                        },
                    ]
                },
            ]
        }
    )
    assert list(limits) == [2]
    assert (limits[2].minimum_flow_byte, limits[2].maximum_flow_byte) == (16, 200)
    assert limits[2].maximum_run_time is None


@pytest.mark.parametrize("payload", [None, "x", {"setting": "x"}, {"setting": None}])
def test_outlet_limits_from_an_unreadable_settings_read_are_empty(payload):
    """Empty means "not learned", which every reader already handles."""
    assert outlet_limits_from_settings(payload) == {}


# =========================================================================== #
# The controller's state
# =========================================================================== #


def hub(code: str, *attributes: object, **data) -> Envelope:
    return envelope(
        code, *attributes, sku="HUB", data={"code": code, "attributes": [], **data}
    )


def test_a_shower_valve_message_sets_each_zone_and_the_warmup_flag():
    """`showerwarmup` sits under `data`, beside the attributes, not in them."""
    state = HubState(DOUBLE)
    assert state.apply_envelope(
        hub(
            MSG_HUB_SHOWER_VALVE,
            {"zone": "1", "status": "ON", "outlets": [1, 0, 0], "temperature": "100"},
            {"component": "valve2", "status": "ON", "outlets": [0, 1]},
            {"status": "ON", "outlets": [1, 1, 1]},  # names no zone: not a zone
            showerwarmup="1",
        )
    )
    assert state.shower_warmup is True
    assert state.is_running
    # Zone 2's short array is padded; the zone-less entry changed nothing.
    assert state.outlets == [True, False, False, False, True, False]
    assert state.last_update == 1000.0

    # A message that does not carry the flag must not clear a running warm-up.
    state.apply_envelope(hub(MSG_HUB_SHOWER_VALVE, showerwarmup=None))
    assert state.shower_warmup is True
    state.apply_envelope(hub(MSG_HUB_SHOWER_VALVE, showerwarmup="0"))
    assert state.shower_warmup is False


def test_a_controller_with_no_zones_reported_pads_to_closed_outlets():
    """Before the first report the flags are unknown, never a confident fault or light."""
    state = HubState(SINGLE)
    assert state.outlets == [False, False, False]
    assert not state.is_running
    assert state.light_on is None
    assert state.has_fault is None


def test_messages_for_other_products_are_not_the_controllers():
    """The shared stream again: a valve's push is not the controller's proof of life."""
    state = HubState(SINGLE)
    assert state.apply_envelope(solo("0184C801")) is False
    assert state.last_update is None


def test_music_follows_only_the_amplifiers_attribute():
    """A light's status riding in the same message is not the music's."""
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(
            MSG_HUB_MUSIC,
            {"component": "light", "status": "ON"},
            {"component": "amplifier", "status": "OFF", "errorstate": "1"},
        )
    )
    assert state.music_on is False
    assert state.error_components == {"amplifier": True}
    assert state.has_fault is True


def test_steam_records_its_detail_and_skips_blank_fields():
    """A blank start time is "not running", not a time to show."""
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(
            MSG_HUB_STEAM,
            {"status": "ON", "temperature": 110, "starttime": "", "totaltime": "15"},
        )
    )
    assert state.steam_on is True
    assert state.steam_temperature == "110"
    assert state.steam_start_time is None
    assert state.steam_total_time == "15"


def test_light_groups_and_experiences_skip_entries_that_are_not_objects():
    """Each group keeps its own entry, and junk in the list is skipped, not raised on."""
    state = HubState(SINGLE)
    state.apply_envelope(
        hub(MSG_HUB_LIGHT, "x", {"component": "lightgroupB", "status": "on"})
    )
    # A group known only by a display name keeps that name as its key.
    state.apply_envelope(hub(MSG_HUB_LIGHT, {"name": "Vanity", "status": "OFF"}))
    assert state.lights == {"b": True, "vanity": False}
    assert state.light_on is True
    state.apply_envelope(
        hub(MSG_HUB_SHOWER_EXPERIENCE, "x", {"name": "Rain", "status": "ON"})
    )
    assert state.active_experience == "Rain"


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ([{"id": "1", "name": "Hair Wash", "status": "ON"}], ("1", "Hair Wash")),
        # A missing status counts as ON: prefer showing a favorite over hiding one.
        ([{"id": 2, "name": " Steam "}], ("2", "Steam")),
        # The accessory messages' spelling, accepted as a fallback.
        ([{"favoriteid": "3", "status": "ON"}], ("3", None)),
        ([{"id": "1", "name": "Hair Wash", "status": "OFF"}], (None, None)),
        # A trailing attribute cannot resurrect the favorite just turned off.
        ([{"id": "1", "status": "OFF"}, {"id": "2", "status": "ON"}], (None, None)),
        # "0" is nothing driving the system.
        ([{"id": "0", "name": "None", "status": "ON"}], (None, None)),
        (["x", {"name": "no id"}], (None, None)),
    ],
)
def test_the_running_favorite_follows_favorite_sts(attributes, expected):
    """Start and stop carry the same id; only the status says which, or the select latches."""
    state = HubState(SINGLE, active_favorite_id="9", active_favorite_name="Old")
    state.apply_envelope(hub(MSG_HUB_FAVORITE, *attributes))
    assert (state.active_favorite_id, state.active_favorite_name) == expected


def test_an_empty_favorites_snapshot_clears_the_list():
    """Sent once the last favorite is deleted; ignoring it kept the deleted one on offer."""
    state = HubState(SINGLE, favorites=[{"id": "1", "name": "Hair Wash"}])
    state.apply_envelope(hub(MSG_HUB_FAVORITES_SNAPSHOT))
    assert state.favorites == []


def test_the_controller_rest_seed_reads_every_block_it_knows():
    """The controller's cold start, from hub-state, fault flags included."""
    state = HubState(DOUBLE)
    state.apply_rest_state(
        {
            "state": {
                "shower": [
                    {"zone": "1", "status": "ON", "outlets": [0, 1, 0], "flowRate": 50},
                    {"status": "OFF"},  # names no zone
                    "not an object",
                ],
                "musicStateModel": {"status": "on"},
                "hubSteamState": {
                    "status": "powerclean",
                    "temperature": "110",
                    "startTime": "",
                    "totalTime": "20",
                },
                "light": [{"name": "groupA", "status": "OFF"}, "x"],
            },
            "errorState": "1",
            "errorComponent": {"steam": "1", "light": "maybe"},
            "showerWarmUp": "1",
        }
    )
    assert state.zones[1].outlets == [False, True, False]
    assert state.zones[1].flowrate == 50
    assert list(state.zones) == [1]
    assert state.music_on is True
    # Self-cleaning is neither steaming nor off.
    assert state.steam_powerclean and state.steam_on is False
    assert state.steam_temperature == "110"
    assert state.steam_start_time is None
    assert state.steam_total_time == "20"
    assert state.lights == {"a": False}
    assert state.error is True
    # Only "0"/"1" style flags count; anything else is unknown, not False.
    assert state.error_components == {"steam": True}
    assert state.shower_warmup is True
    assert state.last_update is not None


@pytest.mark.parametrize("payload", [None, [], {"state": []}])
def test_an_unreadable_controller_seed_changes_nothing(payload):
    """A wrong-shaped reply must not raise inside setup."""
    state = HubState(SINGLE)
    state.apply_rest_state(payload)
    assert state.zones == {} and state.last_update is None


def test_the_off_spellings_of_a_controller_flag_read_as_off():
    """`bool("0")` is True; these flags are read by value, never by truthiness."""
    state = HubState(SINGLE)
    state.apply_rest_state(
        {"state": {}, "errorState": "off", "errorComponent": {"hub": "false"}}
    )
    assert state.error is False
    assert state.error_components == {"hub": False}
    assert state.has_fault is False


# =========================================================================== #
# The envelope
# =========================================================================== #


def test_an_envelope_finds_the_first_attribute_matching_a_code_and_fields():
    """How handlers pick their attribute out of a message that can carry several."""
    message = Envelope(
        "GCS",
        "gcs-1",
        "X",
        [
            "not an object",  # type: ignore[list-item]
            {"code": "A", "zone": "1"},
            {"code": "A", "zone": "2"},
            {"code": "B"},
        ],
        0.0,
    )
    assert message.attribute("A", zone="2") == {"code": "A", "zone": "2"}
    assert message.attribute() == {"code": "A", "zone": "1"}
    assert message.attribute("C") is None
    assert message.attribute(zone="3") is None


# =========================================================================== #
# Which entry changes are worth a reload
# =========================================================================== #


DATA = {"username": "me", "refresh_token": "a", "mobile_device_id": "m", "unit": "F"}
OPTIONS = {"zone_grouping": "numbered", "valves": {"gcs-1": {"auto": True}}}
IGNORE = {
    "ignore_data": ("refresh_token", "mobile_device_id"),
    "ignore_options": ("valves",),
}


def test_the_integrations_own_bookkeeping_is_not_a_reason_to_reload():
    """A rotated token or a remembered warm-up mode must not flap every entity."""
    before = reload_signature(DATA, OPTIONS, **IGNORE)
    after = reload_signature(
        {**DATA, "refresh_token": "b", "mobile_device_id": "n"},
        {**OPTIONS, "valves": {"gcs-1": {"auto": False}}},
        **IGNORE,
    )
    assert before == after


@pytest.mark.parametrize(
    ("data", "options"),
    [
        ({**DATA, "unit": "C"}, OPTIONS),
        ({**DATA, "new_setting": 1}, OPTIONS),
        ({k: v for k, v in DATA.items() if k != "unit"}, OPTIONS),
        (DATA, {**OPTIONS, "zone_grouping": "subdevices"}),
    ],
)
def test_any_other_change_reloads_including_keys_nobody_anticipated(data, options):
    """Suppressing a reload has to be written down; the default is to reload."""
    assert reload_signature(DATA, OPTIONS, **IGNORE) != reload_signature(
        data, options, **IGNORE
    )


def test_a_signature_is_a_snapshot_not_a_view_of_the_entry():
    """The listener this replaced compared the entry with itself and never reloaded."""
    options = {"grouping": {"zones": [1, 2], "tags": {"b", "a"}}}
    before = reload_signature({}, options)
    options["grouping"]["zones"].append(3)
    assert reload_signature({}, options) != before


def test_equal_settings_compare_equal_whatever_their_order_or_container():
    """A write that changes nothing must not reload because it came back reordered."""
    first = reload_signature({"a": 1, "b": {"y": [1, 2], "x": {3, 1}}}, {"o": (1, 2)})
    second = reload_signature(
        {"b": {"x": frozenset({1, 3}), "y": (1, 2)}, "a": 1}, {"o": [1, 2]}
    )
    assert first == second
