"""
Unit tests for ToolRegistry.

Tests YAML loading, Anthropic-format schema generation, env-var expansion,
and endpoint/base_url lookup.
"""

import os
import textwrap
import pytest
from pathlib import Path

from app.services.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_YAML = textwrap.dedent("""\
    engines:
      hx_engine:
        name: "Test Engine"
        base_url: "http://localhost:8100"
        enabled: true
        tools:
          - name: hx_validate_requirements
            description: "Validate HX requirements"
            endpoint: "/api/v1/hx/requirements"
            method: POST
            streaming: false
            required_fields:
              - hot_fluid_name
              - cold_fluid_name
              - T_hot_in_C
              - T_cold_in_C
              - m_dot_hot_kg_s
            optional_fields:
              - T_hot_out_C
          - name: hx_design
            description: "Start HX design pipeline"
            endpoint: "/api/v1/hx/design"
            method: POST
            streaming: true
            required_fields:
              - hot_fluid_name
            optional_fields:
              - token
""")

DISABLED_ENGINE_YAML = textwrap.dedent("""\
    engines:
      hx_engine:
        name: "Disabled"
        base_url: "http://localhost:8100"
        enabled: false
        tools:
          - name: hx_validate_requirements
            description: "Validate"
            endpoint: "/api/v1/hx/requirements"
            method: POST
            streaming: false
            required_fields: [hot_fluid_name]
            optional_fields: []
""")


@pytest.fixture
def yaml_file(tmp_path) -> Path:
    f = tmp_path / "engines.yaml"
    f.write_text(MINIMAL_YAML)
    return f


@pytest.fixture
def registry(yaml_file) -> ToolRegistry:
    return ToolRegistry(engines_yaml_path=str(yaml_file))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToolRegistryLoading:
    def test_loads_two_tools(self, registry):
        tools = registry.get_tools_for_claude()
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert "hx_validate_requirements" in names
        assert "hx_design" in names

    def test_disabled_engine_excluded(self, tmp_path):
        f = tmp_path / "engines.yaml"
        f.write_text(DISABLED_ENGINE_YAML)
        r = ToolRegistry(engines_yaml_path=str(f))
        assert r.get_tools_for_claude() == []

    def test_tool_endpoints(self, registry):
        assert registry.get_tool_endpoint("hx_validate_requirements") == "/api/v1/hx/requirements"
        assert registry.get_tool_endpoint("hx_design") == "/api/v1/hx/design"

    def test_unknown_tool_endpoint_returns_none(self, registry):
        assert registry.get_tool_endpoint("nonexistent") is None

    def test_base_url(self, registry):
        assert registry.get_tool_base_url("hx_validate_requirements") == "http://localhost:8100"


class TestToolSchemaFormat:
    def test_anthropic_schema_structure(self, registry):
        tools = registry.get_tools_for_claude()
        validate_tool = next(t for t in tools if t["name"] == "hx_validate_requirements")
        assert "name" in validate_tool
        assert "description" in validate_tool
        assert "input_schema" in validate_tool
        schema = validate_tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_required_fields_in_schema(self, registry):
        tools = registry.get_tools_for_claude()
        validate_tool = next(t for t in tools if t["name"] == "hx_validate_requirements")
        required = validate_tool["input_schema"]["required"]
        assert "hot_fluid_name" in required
        assert "T_hot_in_C" in required
        assert "m_dot_hot_kg_s" in required
        # optional fields must NOT be in required list
        assert "T_hot_out_C" not in required

    def test_optional_fields_in_properties(self, registry):
        tools = registry.get_tools_for_claude()
        validate_tool = next(t for t in tools if t["name"] == "hx_validate_requirements")
        props = validate_tool["input_schema"]["properties"]
        assert "T_hot_out_C" in props
        assert props["T_hot_out_C"]["type"] == "number"

    def test_field_types_numeric(self, registry):
        tools = registry.get_tools_for_claude()
        validate_tool = next(t for t in tools if t["name"] == "hx_validate_requirements")
        props = validate_tool["input_schema"]["properties"]
        assert props["T_hot_in_C"]["type"] == "number"
        assert props["hot_fluid_name"]["type"] == "string"


class TestEnvVarExpansion:
    def test_expands_env_var_with_default(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            engines:
              hx_engine:
                name: "Test"
                base_url: "${HX_ENGINE_URL:-http://hx-engine:8100}"
                enabled: true
                tools:
                  - name: hx_design
                    description: "design"
                    endpoint: "/api/v1/hx/design"
                    method: POST
                    streaming: true
                    required_fields: [hot_fluid_name]
                    optional_fields: []
        """)
        f = tmp_path / "engines.yaml"
        f.write_text(yaml_content)
        r = ToolRegistry(engines_yaml_path=str(f))
        assert r.get_tool_base_url("hx_design") == "http://hx-engine:8100"

    def test_expands_env_var_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HX_ENGINE_URL", "http://custom:9999")
        yaml_content = textwrap.dedent("""\
            engines:
              hx_engine:
                name: "Test"
                base_url: "${HX_ENGINE_URL:-http://default:8100}"
                enabled: true
                tools:
                  - name: hx_design
                    description: "design"
                    endpoint: "/api/v1/hx/design"
                    method: POST
                    streaming: true
                    required_fields: [hot_fluid_name]
                    optional_fields: []
        """)
        f = tmp_path / "engines.yaml"
        f.write_text(yaml_content)
        r = ToolRegistry(engines_yaml_path=str(f))
        assert r.get_tool_base_url("hx_design") == "http://custom:9999"
