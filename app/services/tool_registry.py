"""
Tool Registry — reads engines.yaml and exposes HX tools to Claude.

Responsibilities:
- Load tool definitions from engines.yaml at startup
- Build the tools list for Claude's API call (Anthropic tool_use format)
- Map tool_name → HTTP endpoint for the orchestration service

Only tools with `enabled: true` on their engine are included.
"""

import os
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Path relative to this file: backend/engines.yaml
_ENGINES_YAML = Path(__file__).parent.parent.parent / "engines.yaml"


def _load_engines_yaml() -> dict:
    path = Path(os.environ.get("ENGINES_YAML_PATH", str(_ENGINES_YAML)))
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Tool schema builders
# ---------------------------------------------------------------------------

# Map YAML field name → Anthropic JSON Schema type
_FIELD_TYPES: dict[str, str] = {
    "hot_fluid_name":    "string",
    "cold_fluid_name":   "string",
    "hot_phase":         "string",
    "cold_phase":        "string",
    "tema_preference":   "string",
    "raw_request":       "string",
    "token":             "string",
    "T_hot_in_C":        "number",
    "T_cold_in_C":       "number",
    "T_hot_out_C":       "number",
    "T_cold_out_C":      "number",
    "m_dot_hot_kg_s":    "number",
    "m_dot_cold_kg_s":   "number",
    "P_hot_Pa":          "number",
    "P_cold_Pa":         "number",
}

_FIELD_DESCRIPTIONS: dict[str, str] = {
    "hot_fluid_name":    "Name of the hot-side fluid (e.g. 'steam', 'water', 'oil')",
    "cold_fluid_name":   "Name of the cold-side fluid (e.g. 'water', 'glycol')",
    "hot_phase":         (
        "Phase state of the hot-side fluid. "
        "Set to 'vapor', 'gas', or 'condensing' when the hot stream enters as a vapor "
        "(e.g. steam, solvent vapor) and will condense; "
        "set to 'liquid' for a sensible-heat liquid stream. "
        "Omit when not known — the engine will infer it from the saturation temperature."
    ),
    "cold_phase":        (
        "Phase state of the cold-side fluid. "
        "Set to 'vapor'/'gas' for a gaseous stream, 'evaporating' for a boiling stream, "
        "or 'liquid' for a sensible-heat liquid stream. "
        "Omit when not known."
    ),
    "T_hot_in_C":        "Hot-side inlet temperature (°C)",
    "T_cold_in_C":       "Cold-side inlet temperature (°C)",
    "T_hot_out_C":       "Hot-side outlet temperature (°C) — optional target",
    "T_cold_out_C":      "Cold-side outlet temperature (°C) — optional target",
    "m_dot_hot_kg_s":    "Hot-side mass flow rate (kg/s)",
    "m_dot_cold_kg_s":   "Cold-side mass flow rate (kg/s) — optional",
    "P_hot_Pa":          "Hot-side operating pressure (Pa) — optional",
    "P_cold_Pa":         "Cold-side operating pressure (Pa) — optional",
    "tema_preference":   "Preferred TEMA type (e.g. 'BEM', 'AES') — optional",
    "raw_request":       "Original user request text — optional, for traceability",
    "token":             "Validation token from hx_validate_requirements",
}


def _build_tool_schema(tool_def: dict) -> dict:
    """Convert a single tool entry from engines.yaml into Anthropic tool format."""
    required_fields = tool_def.get("required_fields", [])
    optional_fields = tool_def.get("optional_fields", [])
    all_fields = required_fields + optional_fields

    properties: dict[str, Any] = {}
    for field in all_fields:
        properties[field] = {
            "type": _FIELD_TYPES.get(field, "string"),
            "description": _FIELD_DESCRIPTIONS.get(field, field),
        }

    return {
        "name": tool_def["name"],
        "description": tool_def["description"].strip(),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required_fields,
        },
    }


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Reads engines.yaml and exposes tools for Claude and the orchestration service.

    Usage:
        registry = ToolRegistry()
        tools = registry.get_tools_for_claude()   # pass to Claude API
        endpoint = registry.get_tool_endpoint("hx_validate_requirements")
    """

    def __init__(self, engines_yaml_path: str | None = None):
        if engines_yaml_path:
            config = yaml.safe_load(Path(engines_yaml_path).read_text())
        else:
            config = _load_engines_yaml()

        self._tools: list[dict] = []              # Anthropic-format tool defs
        self._endpoints: dict[str, str] = {}      # tool_name → relative endpoint path
        self._base_urls: dict[str, str] = {}      # tool_name → engine base_url

        for engine_name, engine in config.get("engines", {}).items():
            if not engine.get("enabled", True):
                continue
            base_url = engine.get("base_url", "")
            # Expand env var syntax: ${VAR:-default}
            base_url = self._expand_env(base_url)

            for tool_def in engine.get("tools", []):
                schema = _build_tool_schema(tool_def)
                self._tools.append(schema)
                self._endpoints[tool_def["name"]] = tool_def["endpoint"]
                self._base_urls[tool_def["name"]] = base_url

        logger.info("ToolRegistry loaded %d tools: %s", len(self._tools), list(self._endpoints))

    @staticmethod
    def _expand_env(value: str) -> str:
        """Expand ${VAR:-default} syntax using os.environ."""
        import re
        def replacer(m):
            var, _, default = m.group(1).partition(":-")
            return os.environ.get(var, default)
        return re.sub(r"\$\{([^}]+)\}", replacer, value)

    def get_tools_for_claude(self) -> list[dict]:
        """Return tool definitions in Anthropic API format."""
        return list(self._tools)

    def get_tool_endpoint(self, tool_name: str) -> str | None:
        """Return the relative endpoint path for a tool (e.g. '/api/v1/hx/requirements')."""
        return self._endpoints.get(tool_name)

    def get_tool_base_url(self, tool_name: str) -> str | None:
        """Return the engine base URL for a tool."""
        return self._base_urls.get(tool_name)
