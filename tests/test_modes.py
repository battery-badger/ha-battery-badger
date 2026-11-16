import json
from pathlib import Path

import pytest

import modes
from modes import (
    CONTROL_OFF,
    control_off_option,
    default_mode_map,
    legacy_mode_map,
    mode_map_from_form,
    resolve_mode_map,
    supported_actions,
    target_for,
)

SIGEN = ["AUTO", "CHARGE", "SOLAR_CHARGE", "HOLD", "IDLE", "DISCHARGE", "EXPORT"]


# --- default_mode_map -------------------------------------------------------

def test_defaults_exact_names():
    m = default_mode_map(SIGEN)
    assert m == {
        "CHARGE": "CHARGE",
        "HOLD": "HOLD",
        "DISCHARGE": "DISCHARGE",
        "EXPORT": "EXPORT",
        CONTROL_OFF: "AUTO",
    }


def test_defaults_case_insensitive_keeps_entity_spelling():
    m = default_mode_map(["Auto", "charge", "Hold", "Discharge", "Export"])
    assert m == {
        "CHARGE": "charge",
        "HOLD": "Hold",
        "DISCHARGE": "Discharge",
        "EXPORT": "Export",
        CONTROL_OFF: "Auto",
    }


def test_defaults_no_export_means_unsupported():
    m = default_mode_map(["CHARGE", "HOLD", "DISCHARGE", "AUTO"])
    assert m["EXPORT"] is None
    assert m[CONTROL_OFF] == "AUTO"


def test_defaults_no_auto_means_do_nothing():
    m = default_mode_map(["CHARGE", "HOLD", "DISCHARGE", "EXPORT"])
    assert m[CONTROL_OFF] is None
    assert m["EXPORT"] == "EXPORT"


def test_defaults_unmatched_core_actions_fall_back_to_own_name():
    m = default_mode_map(["Eco", "Backup"])
    assert (m["CHARGE"], m["HOLD"], m["DISCHARGE"]) == ("CHARGE", "HOLD", "DISCHARGE")
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None


@pytest.mark.parametrize("options", [None, [], ()])
def test_defaults_empty_or_none_options(options):
    assert default_mode_map(options) == legacy_mode_map()


def test_defaults_ignore_non_string_options():
    m = default_mode_map(["CHARGE", None, 3, "AUTO"])
    assert m["CHARGE"] == "CHARGE" and m[CONTROL_OFF] == "AUTO"


# --- resolve_mode_map -------------------------------------------------------

def test_resolve_nothing_stored_uses_defaults_for_options():
    assert resolve_mode_map(None, SIGEN) == default_mode_map(SIGEN)
    assert resolve_mode_map({}, SIGEN) == default_mode_map(SIGEN)


@pytest.mark.parametrize("options", [None, []])
def test_resolve_nothing_stored_no_options_is_legacy(options):
    assert resolve_mode_map(None, options) == legacy_mode_map()


def test_resolve_stored_wins_over_defaults():
    stored = {
        "CHARGE": "SOLAR_CHARGE",
        "HOLD": "IDLE",
        "DISCHARGE": "DISCHARGE",
        "EXPORT": None,
        CONTROL_OFF: None,
    }
    m = resolve_mode_map(stored, SIGEN)
    assert m == stored


def test_resolve_stored_matches_case_insensitively_to_entity_spelling():
    m = resolve_mode_map({"EXPORT": "export"}, ["Export", "CHARGE"])
    assert m["EXPORT"] == "Export"


def test_resolve_stale_stored_values_are_redefaulted():
    stored = {
        "CHARGE": "Force charge",   # not on the new entity
        "HOLD": "HOLD",
        "DISCHARGE": "Self use",    # not on the new entity
        "EXPORT": "Feed in",        # not on the new entity -> default (EXPORT)
        CONTROL_OFF: "Old auto",    # not on the new entity -> default (AUTO)
    }
    m = resolve_mode_map(stored, SIGEN)
    assert m == default_mode_map(SIGEN)


def test_resolve_stale_export_becomes_unsupported_when_default_has_none():
    m = resolve_mode_map({"EXPORT": "Feed in"}, ["CHARGE", "HOLD", "DISCHARGE"])
    assert m["EXPORT"] is None


def test_resolve_stored_none_for_optional_slots_is_respected():
    m = resolve_mode_map({"EXPORT": None, CONTROL_OFF: None}, SIGEN)
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None


def test_resolve_required_actions_never_none():
    m = resolve_mode_map({"CHARGE": None, "HOLD": "", "DISCHARGE": None}, SIGEN)
    assert (m["CHARGE"], m["HOLD"], m["DISCHARGE"]) == ("CHARGE", "HOLD", "DISCHARGE")


def test_resolve_partial_stored_fills_gaps_from_defaults():
    m = resolve_mode_map({"CHARGE": "SOLAR_CHARGE"}, SIGEN)
    assert m["CHARGE"] == "SOLAR_CHARGE"
    assert m["EXPORT"] == "EXPORT" and m[CONTROL_OFF] == "AUTO"


def test_resolve_options_unavailable_trusts_stored_and_fills_from_legacy():
    stored = {"CHARGE": "Force charge", "EXPORT": "Feed in"}
    m = resolve_mode_map(stored, None)
    assert m["CHARGE"] == "Force charge"
    assert m["EXPORT"] == "Feed in"
    assert m["HOLD"] == "HOLD" and m[CONTROL_OFF] is None


def test_resolve_ignores_junk_stored_values():
    m = resolve_mode_map({"CHARGE": 5, "EXPORT": ["x"]}, SIGEN)
    assert m == default_mode_map(SIGEN)


def test_resolve_does_not_mutate_inputs():
    stored = {"CHARGE": "charge"}
    opts = ["CHARGE"]
    resolve_mode_map(stored, opts)
    assert stored == {"CHARGE": "charge"} and opts == ["CHARGE"]


# --- supported_actions ------------------------------------------------------

def test_supported_actions_order_and_export():
    assert supported_actions(default_mode_map(SIGEN)) == [
        "CHARGE", "HOLD", "DISCHARGE", "EXPORT",
    ]


def test_supported_actions_without_export():
    assert supported_actions(legacy_mode_map()) == ["CHARGE", "HOLD", "DISCHARGE"]


def test_supported_actions_core_always_present():
    assert supported_actions({"EXPORT": "X"}) == [
        "CHARGE", "HOLD", "DISCHARGE", "EXPORT",
    ]
    assert supported_actions({}) == ["CHARGE", "HOLD", "DISCHARGE"]


# --- target_for -------------------------------------------------------------

def test_target_for_mapped_actions():
    m = {"CHARGE": "Force charge", "HOLD": "Idle", "DISCHARGE": "Self use", "EXPORT": "Feed in"}
    assert [target_for(a, m) for a in modes.ACTIONS] == [
        "Force charge", "Idle", "Self use", "Feed in",
    ]


def test_target_for_unsupported_export_falls_back_to_discharge():
    m = legacy_mode_map()
    m["DISCHARGE"] = "Self use"
    assert target_for("EXPORT", m) == "Self use"


def test_target_for_unknown_action_falls_back_to_hold():
    m = legacy_mode_map()
    m["HOLD"] = "Idle"
    assert target_for("SOMETHING_NEW", m) == "Idle"


def test_target_for_control_off_key_is_not_a_schedule_action():
    m = default_mode_map(SIGEN)
    assert target_for(CONTROL_OFF, m) == m["HOLD"]


def test_target_for_missing_keys_fall_back_to_names():
    assert target_for("EXPORT", {}) == "DISCHARGE"
    assert target_for("???", {}) == "HOLD"


# --- control off / form conversion ------------------------------------------

def test_control_off_option():
    assert control_off_option(default_mode_map(SIGEN)) == "AUTO"
    assert control_off_option(legacy_mode_map()) is None


def test_mode_map_from_form_sentinels_and_blanks():
    form = {
        "charge": "CHARGE", "hold": " IDLE ", "discharge": "",
        "export": "__ns__", "control_off": "__dn__",
    }
    m = mode_map_from_form(form, not_supported="__ns__", do_nothing="__dn__")
    assert m == {
        "CHARGE": "CHARGE", "HOLD": "IDLE", "DISCHARGE": "DISCHARGE",
        "EXPORT": None, CONTROL_OFF: None,
    }


def test_mode_map_from_form_values():
    form = {"charge": "a", "hold": "b", "discharge": "c", "export": "d", "control_off": "AUTO"}
    m = mode_map_from_form(form, not_supported="__ns__", do_nothing="__dn__")
    assert m == {"CHARGE": "a", "HOLD": "b", "DISCHARGE": "c", "EXPORT": "d", CONTROL_OFF: "AUTO"}


def test_mode_map_from_form_blank_text_fields():
    m = mode_map_from_form({"export": "", "control_off": ""}, not_supported="x", do_nothing="y")
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None


# --- translations -----------------------------------------------------------

def _keys(d, prefix=""):
    out = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        out.add(path)
        if isinstance(v, dict):
            out |= _keys(v, path)
    return out


def test_strings_and_en_translation_match():
    base = Path(__file__).resolve().parent.parent / "custom_components" / "battery_badger"
    strings = json.loads((base / "strings.json").read_text())
    en = json.loads((base / "translations" / "en.json").read_text())
    assert _keys(strings) == _keys(en)


def test_modes_step_has_a_field_and_description_for_every_slot():
    base = Path(__file__).resolve().parent.parent / "custom_components" / "battery_badger"
    strings = json.loads((base / "strings.json").read_text())
    slots = {a.lower() for a in modes.ACTIONS} | {"control_off"}
    for flow in ("config", "options"):
        step = strings[flow]["step"]["modes"]
        assert set(step["data"]) == slots
        assert set(step["data_description"]) == slots


# --- matched_defaults / form_mode_map / missing form keys ---------------------

FOREIGN = ["Self use", "Force charge", "Standby", "Feed in"]


def test_matched_defaults_real_matches_only():
    m = modes.matched_defaults(SIGEN)
    assert m["CHARGE"] == "CHARGE" and m["EXPORT"] == "EXPORT" and m[CONTROL_OFF] == "AUTO"


def test_matched_defaults_unmatched_are_none_not_own_name():
    m = modes.matched_defaults(FOREIGN)
    assert m == {"CHARGE": None, "HOLD": None, "DISCHARGE": None, "EXPORT": None, CONTROL_OFF: None}
    # runtime defaults still fall back to the own names
    d = default_mode_map(FOREIGN)
    assert (d["CHARGE"], d["HOLD"], d["DISCHARGE"]) == ("CHARGE", "HOLD", "DISCHARGE")


def test_matched_defaults_keeps_entity_spelling_and_handles_none():
    assert modes.matched_defaults(["charge"])["CHARGE"] == "charge"
    assert all(v is None for v in modes.matched_defaults(None).values())


def test_form_mode_map_has_no_default_for_unmatched_required_actions():
    m = modes.form_mode_map(None, FOREIGN)
    assert m["CHARGE"] is None and m["HOLD"] is None and m["DISCHARGE"] is None
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None


def test_form_mode_map_keeps_real_matches_and_stored_choices():
    stored = {"CHARGE": "Force charge", "HOLD": "Standby", "DISCHARGE": "Self use", "EXPORT": "Feed in"}
    m = modes.form_mode_map(stored, FOREIGN)
    assert m["CHARGE"] == "Force charge" and m["EXPORT"] == "Feed in"
    assert modes.form_mode_map(None, SIGEN) == default_mode_map(SIGEN)


def test_form_mode_map_unreadable_options_is_resolved_map():
    assert modes.form_mode_map(None, None) == legacy_mode_map()


def test_stored_none_survives_for_unchanged_entity():
    stored = {"EXPORT": None, CONTROL_OFF: None}
    m = resolve_mode_map(stored, SIGEN)
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None
    # a changed entity is shown fresh defaults (options flow passes no stored map)
    fresh = modes.form_mode_map(None, SIGEN)
    assert fresh["EXPORT"] == "EXPORT" and fresh[CONTROL_OFF] == "AUTO"


def test_mode_map_from_form_missing_keys_mean_unsupported_and_do_nothing():
    form = {"charge": "a", "hold": "b", "discharge": "c"}  # export / control_off omitted
    m = mode_map_from_form(form, not_supported="__ns__", do_nothing="__dn__")
    assert m["EXPORT"] is None and m[CONTROL_OFF] is None


def test_strings_files_are_identical_and_modes_description_uses_note():
    base = Path(__file__).resolve().parent.parent / "custom_components" / "battery_badger"
    a = (base / "strings.json").read_text()
    assert a == (base / "translations" / "en.json").read_text()
    strings = json.loads(a)
    for flow in ("config", "options"):
        desc = strings[flow]["step"]["modes"]["description"]
        assert "{note}" in desc and "Not supported" not in desc


# --- choose placeholder -------------------------------------------------------

def test_unchosen_actions_lists_required_placeholders_and_blanks():
    form = {"charge": modes.CHOOSE, "hold": "Standby", "discharge": "", "export": "__ns__"}
    assert modes.unchosen_actions(form) == ["CHARGE", "DISCHARGE"]
    assert modes.unchosen_actions({}) == ["CHARGE", "HOLD", "DISCHARGE"]


def test_unchosen_actions_empty_when_all_chosen():
    form = {"charge": "a", "hold": "b", "discharge": "c", "export": modes.CHOOSE}
    assert modes.unchosen_actions(form) == []  # export/control_off are never "unchosen"


def test_mode_map_from_form_never_stores_the_placeholder():
    form = {
        "charge": modes.CHOOSE, "hold": "b", "discharge": "c",
        "export": modes.CHOOSE, "control_off": modes.CHOOSE,
    }
    m = mode_map_from_form(form, not_supported="__ns__", do_nothing="__dn__")
    assert modes.CHOOSE not in m.values()
    assert m["CHARGE"] == "CHARGE" and m["EXPORT"] is None and m[CONTROL_OFF] is None
