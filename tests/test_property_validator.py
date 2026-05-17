"""Unit tests for backend/app/services/property_validator.py (EPIC-XSTACK-2026-007-S2)."""

from __future__ import annotations

import pytest

from app.services.property_validator import (
    PROPERTY_BOUNDS,
    parse_property_text,
    validate_fluid_properties,
)


# ---------------------------------------------------------------------------
# parse_property_text
# ---------------------------------------------------------------------------

class TestParsePropertyText:

    def test_equals_delimiter(self):
        result = parse_property_text("density=920, viscosity=0.00048")
        assert result["density_kg_m3"] == pytest.approx(920.0)
        assert result["viscosity_Pa_s"] == pytest.approx(0.00048)

    def test_colon_delimiter(self):
        result = parse_property_text("density: 850; cp: 2100")
        assert result["density_kg_m3"] == pytest.approx(850.0)
        assert result["cp_J_kgK"] == pytest.approx(2100.0)

    def test_aliases_rho_mu(self):
        result = parse_property_text("rho=860, mu=0.003")
        assert "density_kg_m3" in result
        assert "viscosity_Pa_s" in result

    def test_alias_k_conductivity(self):
        result = parse_property_text("conductivity=0.14")
        assert result["k_W_mK"] == pytest.approx(0.14)

    def test_newline_separated(self):
        result = parse_property_text("density=870\nviscosity=0.002\ncp=2000\nk=0.13")
        assert len(result) == 4

    def test_unrecognised_key_silently_dropped(self):
        result = parse_property_text("pressure=101325, density=900")
        assert "pressure" not in result
        assert result["density_kg_m3"] == pytest.approx(900.0)

    def test_empty_string_returns_empty(self):
        assert parse_property_text("") == {}

    def test_all_unrecognised_returns_empty(self):
        assert parse_property_text("foo=1, bar=2") == {}


# ---------------------------------------------------------------------------
# validate_fluid_properties
# ---------------------------------------------------------------------------

class TestValidateFluidProperties:

    def test_single_valid_density(self):
        valid, errors, cleaned = validate_fluid_properties({"density_kg_m3": 850.0})
        assert valid is True
        assert errors == []
        assert cleaned["density_kg_m3"] == pytest.approx(850.0)

    def test_all_four_valid_properties(self):
        props = {
            "density_kg_m3": 860.0,
            "viscosity_Pa_s": 0.003,
            "cp_J_kgK": 2100.0,
            "k_W_mK": 0.135,
        }
        valid, errors, cleaned = validate_fluid_properties(props)
        assert valid is True
        assert errors == []
        assert len(cleaned) == 4

    def test_empty_dict_returns_error(self):
        valid, errors, cleaned = validate_fluid_properties({})
        assert valid is False
        assert any("At least one property" in e for e in errors)
        assert cleaned == {}

    def test_viscosity_above_upper_bound(self):
        valid, errors, cleaned = validate_fluid_properties({"viscosity_Pa_s": 2.0})
        assert valid is False
        assert any("viscosity_Pa_s" in e for e in errors)
        assert "viscosity_Pa_s" not in cleaned

    def test_density_below_lower_bound(self):
        valid, errors, cleaned = validate_fluid_properties({"density_kg_m3": 0.001})
        assert valid is False
        assert any("density_kg_m3" in e for e in errors)

    def test_non_numeric_value_returns_error(self):
        valid, errors, cleaned = validate_fluid_properties({"density_kg_m3": "heavy"})
        assert valid is False
        assert any("density_kg_m3" in e for e in errors)

    def test_partial_set_one_valid_one_out_of_range(self):
        """One in-range + one out-of-range → valid=False but no cleaned for bad field."""
        valid, errors, cleaned = validate_fluid_properties({
            "density_kg_m3": 850.0,
            "cp_J_kgK": 99999999.0,  # far above 100_000 upper bound
        })
        assert valid is False
        assert "density_kg_m3" in cleaned
        assert "cp_J_kgK" not in cleaned

    def test_exact_lower_bound_density_is_valid(self):
        lo = PROPERTY_BOUNDS["density_kg_m3"][0]
        valid, _, cleaned = validate_fluid_properties({"density_kg_m3": lo})
        assert valid is True
        assert cleaned["density_kg_m3"] == pytest.approx(lo)

    def test_exact_upper_bound_viscosity_is_valid(self):
        hi = PROPERTY_BOUNDS["viscosity_Pa_s"][1]
        valid, _, cleaned = validate_fluid_properties({"viscosity_Pa_s": hi})
        assert valid is True
        assert cleaned["viscosity_Pa_s"] == pytest.approx(hi)

    def test_extra_keys_ignored(self):
        valid, errors, cleaned = validate_fluid_properties({
            "density_kg_m3": 870.0,
            "unknown_property": 42.0,
        })
        assert valid is True
        assert "unknown_property" not in cleaned
