"""Inverter mode mapping — pure logic, no Home Assistant imports.

The server schedules one of four actions per half hour. Inverters name their
modes differently, so each installation keeps a mapping from action to the
option to select on the user's control entity. This module holds the action
definitions and every decision about that mapping so it can be unit-tested
with plain pytest.

A mapping is a dict with one key per action plus ``CONTROL_OFF``. Values are
the option to select, or ``None``: "not supported" for EXPORT, "do nothing"
for CONTROL_OFF. CHARGE / HOLD / DISCHARGE are never ``None``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

ACTIONS: Final = ("CHARGE", "HOLD", "DISCHARGE", "EXPORT")
CONTROL_OFF: Final = "CONTROL_OFF"
# Actions every inverter must be able to perform.
REQUIRED_ACTIONS: Final = ("CHARGE", "HOLD", "DISCHARGE")

ACTION_LABELS: Final = {
    "CHARGE": "Charge from grid",
    "HOLD": "Hold (keep solar)",
    "DISCHARGE": "Power the house",
    "EXPORT": "Export to grid",
}

ACTION_DESCRIPTIONS: Final = {
    "CHARGE": "Battery charges from the grid at full power.",
    "HOLD": (
        "Battery neither discharges nor charges from the grid; surplus solar "
        "is stored, then exported once full."
    ),
    "DISCHARGE": (
        "Battery covers the household load; surplus solar is stored, then "
        "exported once full."
    ),
    "EXPORT": "Battery discharges at full power to the house and the grid.",
}

CONTROL_OFF_LABEL: Final = "Control off"
CONTROL_OFF_DESCRIPTION: Final = (
    "The option to select when Battery Badger control is switched off, "
    "handing the battery back to the inverter's own controller "
    "(typically an option called AUTO)."
)

# Option names the plugin does not send today but documents as reserved.
RESERVED_NAMES: Final = {
    "IDLE": "Battery does nothing; surplus solar is exported.",
    "SOLAR_CHARGE": "All solar goes into the battery; the house runs from the grid.",
    "AUTO": "The inverter's own controller.",
}

# Where an unsupported action is redirected. Unknown actions go to HOLD.
_FALLBACKS: Final = {"EXPORT": "DISCHARGE"}
_UNKNOWN_ACTION_FALLBACK: Final = "HOLD"

ModeMap = dict[str, "str | None"]

# Placeholder choice shown in a required dropdown that has no same-named
# option. The browser always pre-selects the first dropdown entry, so a real
# choice must be forced by making this the first entry; it is never stored.
CHOOSE: Final = "__choose__"


def _find_option(options: Iterable[str], name: str) -> str | None:
    """Return the entity's own spelling of ``name`` (case-insensitive), if present."""
    wanted = name.casefold()
    for opt in options:
        if isinstance(opt, str) and opt.casefold() == wanted:
            return opt
    return None


def clean_options(options: Any) -> list[str] | None:
    """Normalise an entity's ``options`` attribute; ``None`` if unusable/empty."""
    if not isinstance(options, (list, tuple)):
        return None
    cleaned = [o for o in options if isinstance(o, str) and o]
    return cleaned or None


def legacy_mode_map() -> ModeMap:
    """The pre-0.2 behaviour: three same-name modes, no EXPORT, control off = nothing."""
    return {
        "CHARGE": "CHARGE",
        "HOLD": "HOLD",
        "DISCHARGE": "DISCHARGE",
        "EXPORT": None,
        CONTROL_OFF: None,
    }


def matched_defaults(options: Iterable[str] | None) -> ModeMap:
    """Defaults that are real matches against the entity's options.

    Same-name (case-insensitive) match keeps the entity's spelling; control off
    matches ``AUTO``. Anything without a match is ``None`` -- including
    CHARGE/HOLD/DISCHARGE, which ``default_mode_map`` fills with their own
    names. The config form uses this to avoid offering options the entity
    does not have.
    """
    opts = clean_options(list(options) if options is not None else None) or []
    result: ModeMap = {a: _find_option(opts, a) for a in ACTIONS}
    result[CONTROL_OFF] = _find_option(opts, "AUTO")
    return result


def default_mode_map(options: Iterable[str] | None) -> ModeMap:
    """Defaults for an entity's option list.

    Same-name (case-insensitive) match wins and keeps the entity's spelling.
    CHARGE/HOLD/DISCHARGE without a match fall back to the action's own name
    (the old behaviour, so an unknown option list never regresses); EXPORT
    without a match is unsupported; control off maps to ``AUTO`` if present.
    """
    result = matched_defaults(options)
    for action in REQUIRED_ACTIONS:
        if result[action] is None:
            result[action] = action
    return result


def form_mode_map(
    stored: Mapping[str, Any] | None, options: Iterable[str] | None
) -> ModeMap:
    """The mapping to pre-fill the mode form with.

    Like ``resolve_mode_map`` but, when the options are readable, a required
    action whose value is not one of them (an own-name fallback the entity
    lacks) is ``None``: the form then has no default and the user must pick
    one of the entity's real options. Without readable options the resolved
    mapping is returned unchanged (free-text form).
    """
    opts = clean_options(list(options) if options is not None else None)
    result = resolve_mode_map(stored, opts)
    if opts is not None:
        for action in REQUIRED_ACTIONS:
            value = result.get(action)
            if value is None or _find_option(opts, value) is None:
                result[action] = None
    return result


def resolve_mode_map(
    stored: Mapping[str, Any] | None, options: Iterable[str] | None
) -> ModeMap:
    """The effective mapping for an installation.

    * Options known: the stored mapping where present, with any stored value
      that is not one of the options (stale: control entity changed, or the
      inverter renamed a mode) re-defaulted; defaults for everything else.
    * Options unknown (entity missing/unavailable): the stored mapping is
      trusted as-is, gaps filled from the legacy map. With nothing stored this
      is exactly the legacy map, until the options become available.

    CHARGE/HOLD/DISCHARGE can never be ``None``.
    """
    opts = clean_options(list(options) if options is not None else None)
    result = default_mode_map(opts) if opts is not None else legacy_mode_map()
    if not stored:
        return result
    for key in (*ACTIONS, CONTROL_OFF):
        if key not in stored:
            continue
        value = stored[key]
        if isinstance(value, str):
            value = value.strip() or None
        elif value is not None:
            continue  # junk — keep the default
        if key in REQUIRED_ACTIONS and value is None:
            continue
        if value is not None and opts is not None:
            match = _find_option(opts, value)
            if match is None:
                continue  # stale — keep the default
            value = match
        result[key] = value
    return result


def supported_actions(mode_map: Mapping[str, str | None]) -> list[str]:
    """Actions with an option mapped, in schedule order."""
    return [a for a in ACTIONS if a in REQUIRED_ACTIONS or mode_map.get(a)]


def target_for(action: str, mode_map: Mapping[str, str | None]) -> str:
    """The option to select for a scheduled action.

    An unsupported action (an older server still sends EXPORT) falls back
    EXPORT -> DISCHARGE; any unknown action falls back to HOLD.
    """
    if action in ACTIONS:
        value = mode_map.get(action)
        if value:
            return value
    fallback = _FALLBACKS.get(action, _UNKNOWN_ACTION_FALLBACK)
    return mode_map.get(fallback) or fallback


def control_off_option(mode_map: Mapping[str, str | None]) -> str | None:
    """The option to select when control is switched off, or ``None``."""
    return mode_map.get(CONTROL_OFF) or None


def unchosen_actions(form: Mapping[str, Any], *, choose: str = CHOOSE) -> list[str]:
    """Required actions whose form value is still the placeholder (or missing).

    Returns mapping keys (``CHARGE`` ...), in schedule order. Only meaningful
    for the dropdown form; the free-text form has no placeholder.
    """
    out = []
    for action in REQUIRED_ACTIONS:
        raw = form.get(action.lower())
        if isinstance(raw, str):
            raw = raw.strip()
        if not raw or raw == choose:
            out.append(action)
    return out


def mode_map_from_form(
    form: Mapping[str, Any],
    *,
    not_supported: str,
    do_nothing: str,
    choose: str = CHOOSE,
) -> ModeMap:
    """Convert options-flow input (lowercase keys, sentinels) to a stored mapping.

    Blank values and the two sentinel strings mean ``None``; blank
    CHARGE/HOLD/DISCHARGE fall back to the action's own name. The ``choose``
    placeholder is never stored: it counts as blank.
    """

    def _clean(raw: Any) -> Any:
        return raw.strip() if isinstance(raw, str) else raw

    result: ModeMap = {}
    for action in ACTIONS:
        raw = _clean(form.get(action.lower()))
        if not raw or raw in (not_supported, choose):
            result[action] = action if action in REQUIRED_ACTIONS else None
        else:
            result[action] = raw
    raw = _clean(form.get(CONTROL_OFF.lower()))
    result[CONTROL_OFF] = None if (not raw or raw in (do_nothing, choose)) else raw
    return result
