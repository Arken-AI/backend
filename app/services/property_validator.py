"""
Fluid property validation service (EPIC-XSTACK-2026-007-S2).

Provides:
    parse_property_text  — parse free-text key=value or key: value input
    validate_fluid_properties — range-check a property dict against physical bounds

Bounds mirror the FluidProperties Pydantic validators in the hx_design_engine
package exactly.  This module has NO dependency on the engine package so it
can be tested and deployed independently.
"""

from __future__ import annotations

import re

# Physical bounds for the four user-supplable properties.
# Format: { key: (min, max, unit_label) }
PROPERTY_BOUNDS: dict[str, tuple[float, float, str]] = {
    "density_kg_m3":   (0.01,  2_000,   "kg/m³"),
    "viscosity_Pa_s":  (1e-7,  1.0,     "Pa·s"),
    "cp_J_kgK":        (100,   100_000, "J/(kg·K)"),
    "k_W_mK":          (0.005, 100,     "W/(m·K)"),
}

# Recognised key aliases (lower-cased) → canonical key
_KEY_ALIASES: dict[str, str] = {
    # density
    "density_kg_m3": "density_kg_m3",
    "density":       "density_kg_m3",
    "rho":           "density_kg_m3",
    # viscosity
    "viscosity_pa_s":  "viscosity_Pa_s",
    "viscosity":       "viscosity_Pa_s",
    "mu":              "viscosity_Pa_s",
    "dynamic_viscosity": "viscosity_Pa_s",
    # specific heat
    "cp_j_kgk": "cp_J_kgK",
    "cp":       "cp_J_kgK",
    "heat_capacity": "cp_J_kgK",
    "specific_heat": "cp_J_kgK",
    # thermal conductivity
    "k_w_mk":              "k_W_mK",
    "k":                   "k_W_mK",
    "thermal_conductivity": "k_W_mK",
    "conductivity":         "k_W_mK",
}

# Strip trailing unit suffixes before float conversion.
# Only match recognised unit strings — NOT a catch-all \S+ that would
# also strip bare numbers like "920" or "0.003".
_UNIT_SUFFIX_RE = re.compile(
    r"\s*(kg/m[³3]|pa[·.]?s|j/\(?kg[·.]?k\)?|w/\(?m[·.]?k\)?)\s*$",
    re.IGNORECASE,
)


def _strip_units(value_str: str) -> str:
    """Remove common unit suffixes so the remaining string can be float()-parsed."""
    return _UNIT_SUFFIX_RE.sub("", value_str.strip()).strip()


def parse_property_text(text: str) -> dict[str, float]:
    """Parse a free-text property string into a {canonical_key: float} dict.

    Accepts formats:
      - ``"density=920, viscosity=0.00048"``
      - ``"density: 920; viscosity: 0.00048"``
      - Mixed delimiters (comma, semicolon, newline) and ``=`` / ``:`` separators.

    Only recognised keys (see ``_KEY_ALIASES``) are returned; unrecognised
    keys are silently dropped.  Conversion errors are also silently dropped
    so the caller can decide how to handle partial results.
    """
    result: dict[str, float] = {}

    # Split on commas, semicolons, or newlines
    entries = re.split(r"[,;\n]+", text or "")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        # Split on first ``=`` or ``:``
        m = re.match(r"^([^=:]+?)\s*[=:]\s*(.+)$", entry)
        if not m:
            continue
        raw_key = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
        raw_val = m.group(2).strip()

        canonical = _KEY_ALIASES.get(raw_key)
        if canonical is None:
            continue

        try:
            float_val = float(_strip_units(raw_val))
        except ValueError:
            continue

        result[canonical] = float_val

    return result


def validate_fluid_properties(
    props: dict,
) -> tuple[bool, list[str], dict[str, float]]:
    """Validate a property dict against physical bounds.

    Parameters
    ----------
    props:
        Dict with any of ``density_kg_m3``, ``viscosity_Pa_s``, ``cp_J_kgK``,
        ``k_W_mK``.  Extra keys are ignored.

    Returns
    -------
    (valid, errors, cleaned_values)
        ``valid`` — True iff all supplied values are within bounds and at
        least one recognised property is present.
        ``errors`` — list of human-readable error strings (empty when valid).
        ``cleaned_values`` — dict containing only validated key→value pairs
        (no Nones for missing keys).
    """
    errors: list[str] = []
    cleaned: dict[str, float] = {}

    for key, (lo, hi, unit) in PROPERTY_BOUNDS.items():
        raw = props.get(key)
        if raw is None:
            continue  # partial sets are accepted

        try:
            val = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{key}={raw!r} is not a valid number.")
            continue

        if val < lo or val > hi:
            errors.append(
                f"{key}={val} is outside the physical range "
                f"[{lo}, {hi}] {unit}."
            )
        else:
            cleaned[key] = val

    if not cleaned and not errors:
        errors.append("At least one property must be provided.")

    valid = len(errors) == 0 and len(cleaned) > 0
    return valid, errors, cleaned
