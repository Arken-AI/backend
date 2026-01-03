# Key Naming Conventions

**Critical Reference**: Always check this document before writing tests or integrating components!

## 🔑 Run Identifiers

### `calc_run_id` (PUBLIC KEY - User-Facing)
**Usage**: When returning simulation results to users/LLM
- **MCP Tool Response**: `result["calc_run_id"]` - returned by `simulate_process` and `simulate_equipment`
- **get_run Tool**: `arguments.get("calc_run_id")` - retrieval parameter
- **Format**: `run_YYYYMMDD_HHMMSS_uuid` (e.g., `run_20260103_171253_99bb1580`)
- **Where**: MCP server responses, tool schemas, API responses

### `run_id` (INTERNAL KEY - Database)
**Usage**: Internal storage in MongoDB
- **MongoDB Field**: `run_doc.run_id` - document identifier
- **Variable Name**: `run_id = run_store.store_run(result)` - internal variable
- **Where**: MongoDB documents, internal processing, database queries

**Rule**: MCP Server converts `run_id` → `calc_run_id` before returning to client

---

## 🏭 Process Identifiers

### `process_id`
**Standard**: Always use `process_id` (NOT `industry_id` + `process_id`)
- ✅ **Correct**: `{"process_id": "sugar_factory"}`
- ❌ **Wrong**: `{"industry_id": "sugar", "process_id": "sugar_mill"}`
- **Tools**: `validate_process_inputs`, `simulate_process`, `get_process`
- **Reason**: Process IDs are globally unique (already include industry context)

---

## 🔧 Equipment Identifiers

### `equipment_type`
**Standard**: Generic equipment class
- **Example**: `"mill"`, `"heater"`, `"crystallizer"`
- **Tools**: `get_equipment_schema`, `simulate_equipment`, `validate_equipment_inputs`

### `node_id`
**Standard**: Specific equipment instance in a process
- **Example**: `"mill_1"`, `"heater_2"`, `"crystallizer_5"`
- **Usage**: Keys in `node_params` dictionary
- **Format**: `{equipment_type}_{sequence_number}`

---

## 📊 Parameter Structures

### `feeds` (Process Inlet Streams)
**Standard Structure**:
```json
{
  "feeds": {
    "cane_inlet": {
      "flow_kg_hr": 100000,
      "brix": 15.0,
      "fiber_pct": 13.0
    }
  }
}
```
- **Key Pattern**: Stream name (e.g., `cane_inlet`, `juice_inlet`)
- **Properties**: Stream-specific (check stream schema)

### `node_params` (Equipment Operating Parameters)
**Standard Structure**:
```json
{
  "node_params": {
    "mill_1": {
      "num_mills": 4,
      "imbibition_pct": 25.0
    },
    "heater_2": {
      "target_temperature": 105
    }
  }
}
```
- **Key Pattern**: `node_id` (e.g., `mill_1`, `evaporator_4`)
- **Properties**: Equipment-specific (check equipment schema)
- **Optional**: Can be empty `{}` to use defaults

### `inlet_stream` (Standalone Equipment Input)
**Standard Structure**:
```json
{
  "inlet_stream": {
    "flow_kg_hr": 100000,
    "brix": 15.0,
    "temperature": 30
  }
}
```
- **Usage**: For `simulate_equipment` and `validate_equipment_inputs`
- **Properties**: Stream-specific properties

### `operating_params` (Standalone Equipment Settings)
**Standard Structure**:
```json
{
  "operating_params": {
    "num_mills": 4,
    "imbibition_pct": 25.0,
    "rotation_speed": 5.0
  }
}
```
- **Usage**: For `simulate_equipment` and `validate_equipment_inputs`
- **Properties**: Equipment-specific parameters

---

## 🛠️ Tool Input Schemas

### Discovery Tools
| Tool | Required Parameters | Optional |
|------|-------------------|----------|
| `list_industries` | - | - |
| `list_processes` | `industry_id` | - |
| `get_process` | `process_id` | `version` |
| `get_equipment_types` | - | `industry_id` |
| `get_equipment_schema` | `equipment_type` | `version` |
| `get_stream_schema` | `stream_type` | `version` |

### Validation Tools
| Tool | Required Parameters |
|------|-------------------|
| `validate_process_inputs` | `process_id`, `feeds`, `node_params` |
| `validate_connections` | `process_id` |
| `validate_equipment_inputs` | `equipment_type`, `inlet_stream`, `operating_params` |

### Simulation Tools
| Tool | Required Parameters | Optional |
|------|-------------------|----------|
| `simulate_process` | `process_id`, `feeds`, `node_params` | `solver_settings` |
| `simulate_equipment` | `equipment_type`, `inlet_stream`, `operating_params` | - |

### Run Tools
| Tool | Required Parameters |
|------|-------------------|
| `get_run` | `calc_run_id` |
| `compare_runs` | `run_id_a`, `run_id_b` |

---

## 📝 Response Formats

### Simulation Success Response
```json
{
  "status": "success",
  "calc_run_id": "run_20260103_171253_99bb1580",
  "outputs": {
    "mill_1": { ... },
    "heater_2": { ... }
  },
  "execution_time_ms": 32450
}
```

### Validation Success Response
```json
{
  "status": "success",
  "message": "All inputs validated successfully",
  "mode": "plant",
  "validated_inputs": { ... }
}
```

### Error Response
```json
{
  "status": "error",
  "error": "Error message here"
}
```

---

## ⚠️ Common Mistakes to Avoid

### ❌ Don't Use Both Industry + Process
```python
# WRONG - Don't do this
result = await client.call_tool("get_process", {
    "industry_id": "sugar",
    "process_id": "sugar_factory"
})
```

```python
# CORRECT - Process ID is enough
result = await client.call_tool("get_process", {
    "process_id": "sugar_factory"
})
```

### ❌ Don't Mix run_id and calc_run_id
```python
# WRONG - These are different
sim_result["run_id"]  # This doesn't exist in response

# CORRECT - Use calc_run_id
sim_result["calc_run_id"]  # This is the public key
```

### ❌ Don't Confuse node_params with operating_params
```python
# WRONG - operating_params is for standalone equipment only
validate_process_inputs({
    "operating_params": {...}  # This won't work for processes
})

# CORRECT - Use node_params for processes
validate_process_inputs({
    "node_params": {
        "mill_1": {...},
        "heater_2": {...}
    }
})
```

---

## 🔍 Where to Check Schemas

**Before writing tests or making tool calls:**

1. **Tool Definitions**: `/mcp_process_server/tools.py`
   - Check `inputSchema` for each tool
   - Review tool descriptions and examples

2. **Pydantic Models**: `/mcp_process_server/schemas.py`
   - Check `Field(...)` definitions
   - Review `examples=[...]` for correct format
   - Check `required` fields vs optional

3. **MCP Server Handlers**: `/mcp_process_server/server.py`
   - Check `arguments.get(...)` calls
   - Review what keys are being accessed

4. **This Document**: `KEY_NAMING_CONVENTIONS.md`
   - Always reference when unsure!

---

## 📚 Quick Reference

```python
# Process Simulation
await mcp_client.call_tool("simulate_process", {
    "process_id": "sugar_factory",
    "feeds": {"cane_inlet": {...}},
    "node_params": {"mill_1": {...}, "heater_2": {...}}
})
# Returns: {"calc_run_id": "run_...", "status": "success", ...}

# Equipment Simulation
await mcp_client.call_tool("simulate_equipment", {
    "equipment_type": "mill",
    "inlet_stream": {...},
    "operating_params": {...}
})
# Returns: {"calc_run_id": "run_...", "status": "success", ...}

# Get Run
await mcp_client.call_tool("get_run", {
    "calc_run_id": "run_20260103_171253_99bb1580"
})
# Returns: {...run data...}

# Get Process
await mcp_client.call_tool("get_process", {
    "process_id": "sugar_factory"
})
# Returns: {"display_name": "...", "sequence": [...], ...}
```

---

**Last Updated**: 2026-01-03  
**Maintainer**: Keep this updated when adding new tools or changing schemas!
