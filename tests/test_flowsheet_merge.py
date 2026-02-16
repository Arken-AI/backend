"""
Tests for the Unified Flowsheet Merge Logic (Phase 2)

Tests cover:
1. _merge_flowsheet — single run (standalone, no chain)
2. _merge_flowsheet — two-run chain (flash → column)
3. _merge_flowsheet — branching (flash → column A, flash → column B)
4. _merge_flowsheet — equipment ID collision handling
5. _get_result_inner — nested vs flat result structures
6. GET /runs/{run_id}/flowsheet — endpoint integration tests
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from copy import deepcopy

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.runs import (
    _merge_flowsheet,
    _get_result_inner,
    _get_input,
    router,
)
from app.models.runs import FlowsheetResponse


# =============================================================================
# Test Data Fixtures
# =============================================================================

NOW = datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc)


def make_flash_run_doc(run_id="run_flash_001"):
    """Create a mock flash drum run document (ROOT — no chain_metadata)."""
    return {
        "run_id": run_id,
        "user_id": "user_1",
        "process_id": "single-flash-drum",
        "status": "success",
        "created_at": NOW,
        "execution_time_ms": 500,
        "chain_metadata": None,
        "downstream_runs": [
            {
                "run_id": "run_column_001",
                "source_equipment_id": "flash",
                "source_port": "liquid_outlet",
                "downstream_equipment_id": "column",
            }
        ],
        "result": {
            "status": "success",
            "input": {
                "compounds": ["ethanol", "water"],
                "feed_streams": [
                    {
                        "stream_id": "feed_1",
                        "target_equipment": "flash",
                        "target_port": "feed_inlet",
                        "flow_rate": 100.0,
                        "temperature_K": 350.0,
                        "pressure_Pa": 101325,
                        "composition": {"ethanol": 0.5, "water": 0.5},
                    }
                ],
                "equipment": [
                    {
                        "id": "flash",
                        "type": "flash_drum",
                        "name": "Flash Drum",
                        "parameters": {"pressure_Pa": 101325},
                    }
                ],
                "edges": [],
            },
            "result": {
                "converged": True,
                "execution_order": ["FEED", "flash", "PRODUCT"],
                "node_results": {
                    "flash": {
                        "converged": True,
                        "iterations": 3,
                        "warnings": [],
                        "outlets": {
                            "vapor_outlet": {
                                "stream_id": "flash_vapor_out",
                                "flow_rate": 40.0,
                                "temperature_K": 337.0,
                                "pressure_Pa": 101325,
                                "composition": {"ethanol": 0.7, "water": 0.3},
                                "phase": "vapor",
                            },
                            "liquid_outlet": {
                                "stream_id": "flash_liquid_out",
                                "flow_rate": 60.0,
                                "temperature_K": 337.0,
                                "pressure_Pa": 101325,
                                "composition": {"ethanol": 0.35, "water": 0.65},
                                "phase": "liquid",
                            },
                        },
                    }
                },
                "stream_results": {},
                "equipment_inputs": {
                    "flash": {
                        "equipment_type": "flash_drum",
                        "applied_parameters": {"pressure_Pa": 101325},
                        "parameter_constraints": {},
                        "inlet_ports": ["feed_1"],
                        "outlet_ports": ["vapor_outlet", "liquid_outlet"],
                    }
                },
                "warnings": [],
            },
        },
    }


def make_column_run_doc(
    run_id="run_column_001",
    source_run_id="run_flash_001",
    equipment_id="column",
):
    """Create a mock distillation column run doc (CHAINED from flash)."""
    return {
        "run_id": run_id,
        "user_id": "user_1",
        "process_id": "single-distillation-column",
        "status": "success",
        "created_at": NOW,
        "execution_time_ms": 1200,
        "chain_metadata": {
            "source_run_id": source_run_id,
            "source_equipment_id": "flash",
            "source_port": "liquid_outlet",
            "extracted_stream": {
                "flow_rate": 60.0,
                "temperature_K": 337.0,
                "pressure_Pa": 101325,
                "composition": {"ethanol": 0.35, "water": 0.65},
                "phase": "liquid",
            },
        },
        "downstream_runs": None,
        "result": {
            "status": "success",
            "input": {
                "compounds": ["ethanol", "water"],
                "feed_streams": [
                    {
                        "stream_id": "chained_feed_abc12345",
                        "target_equipment": equipment_id,
                        "target_port": "feed_inlet",
                        "flow_rate": 60.0,
                        "temperature_K": 337.0,
                        "pressure_Pa": 101325,
                        "composition": {"ethanol": 0.35, "water": 0.65},
                    }
                ],
                "equipment": [
                    {
                        "id": equipment_id,
                        "type": "distillation_column",
                        "name": "Distillation Column",
                        "parameters": {"reflux_ratio": 3.0, "num_stages": 20},
                    }
                ],
                "edges": [],
            },
            "result": {
                "converged": True,
                "execution_order": ["FEED", equipment_id, "PRODUCT"],
                "node_results": {
                    equipment_id: {
                        "converged": True,
                        "iterations": 8,
                        "warnings": [],
                        "outlets": {
                            "distillate_outlet": {
                                "stream_id": f"{equipment_id}_distillate",
                                "flow_rate": 25.0,
                                "temperature_K": 351.0,
                                "pressure_Pa": 101325,
                                "composition": {"ethanol": 0.82, "water": 0.18},
                                "phase": "liquid",
                            },
                            "bottoms_outlet": {
                                "stream_id": f"{equipment_id}_bottoms",
                                "flow_rate": 35.0,
                                "temperature_K": 373.0,
                                "pressure_Pa": 101325,
                                "composition": {"ethanol": 0.02, "water": 0.98},
                                "phase": "liquid",
                            },
                        },
                    }
                },
                "stream_results": {},
                "equipment_inputs": {
                    equipment_id: {
                        "equipment_type": "distillation_column",
                        "applied_parameters": {
                            "reflux_ratio": 3.0,
                            "num_stages": 20,
                        },
                        "parameter_constraints": {},
                        "inlet_ports": ["chained_feed_abc12345"],
                        "outlet_ports": ["distillate_outlet", "bottoms_outlet"],
                    }
                },
                "warnings": [],
            },
        },
    }


# =============================================================================
# Tests for _get_result_inner / _get_input helpers
# =============================================================================


class TestHelpers:
    """Tests for the helper functions that navigate run data structure."""

    def test_get_result_inner_with_nested_result(self):
        """When data has result.node_results directly, returns result."""
        data = {
            "result": {
                "node_results": {"flash": {}},
                "stream_results": {},
            }
        }
        inner = _get_result_inner(data)
        assert "node_results" in inner
        assert inner["node_results"] == {"flash": {}}

    def test_get_result_inner_double_nested(self):
        """When data.result has no node_results but has result key, unwraps."""
        data = {
            "result": {
                "result": {
                    "node_results": {"flash": {}},
                }
            }
        }
        inner = _get_result_inner(data)
        assert "node_results" in inner

    def test_get_result_inner_empty(self):
        """Empty data returns empty dict."""
        assert _get_result_inner({}) == {}

    def test_get_input(self):
        """Extracts input section from data."""
        data = {"input": {"feed_streams": [], "equipment": []}}
        assert _get_input(data)["feed_streams"] == []

    def test_get_input_missing(self):
        """Missing input returns empty dict."""
        assert _get_input({}) == {}


# =============================================================================
# Tests for _merge_flowsheet — Single Run (Standalone)
# =============================================================================


class TestMergeStandaloneRun:
    """Test that standalone (unchained) runs pass through correctly."""

    def test_single_run_equipment_preserved(self):
        """Single run's equipment is preserved without modification."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        equipment = merged_data["input"]["equipment"]
        assert len(equipment) == 1
        assert equipment[0]["id"] == "flash"
        assert len(warnings) == 0

    def test_single_run_feed_streams_preserved(self):
        """Single run's feed streams are included."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        feeds = merged_data["input"]["feed_streams"]
        assert len(feeds) == 1
        assert feeds[0]["stream_id"] == "feed_1"

    def test_single_run_node_results_preserved(self):
        """Single run's node_results are accessible."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        node_results = merged_data["result"]["node_results"]
        assert "flash" in node_results
        assert node_results["flash"]["converged"] is True

    def test_single_run_run_map(self):
        """Run map maps equipment to its run."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        assert run_map == {"flash": "run_flash_001"}

    def test_single_run_execution_order(self):
        """Execution order is preserved."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        exec_order = merged_data["result"]["execution_order"]
        assert "flash" in exec_order
        assert "FEED" in exec_order

    def test_single_run_compounds_preserved(self):
        """Extra input keys like compounds are preserved from root."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        assert merged_data["input"]["compounds"] == ["ethanol", "water"]

    def test_single_run_no_edges(self):
        """Standalone run with no internal edges has empty edges."""
        doc = make_flash_run_doc()
        warnings = []
        merged_data, run_map = _merge_flowsheet([("run_flash_001", doc)], warnings)

        assert merged_data["input"]["edges"] == []


# =============================================================================
# Tests for _merge_flowsheet — Two-Run Chain (Flash → Column)
# =============================================================================


class TestMergeTwoRunChain:
    """Test merging a two-run chain: flash drum → distillation column."""

    def setup_method(self):
        """Create standard two-run chain fixtures."""
        self.flash_doc = make_flash_run_doc()
        self.column_doc = make_column_run_doc()
        self.ordered_runs = [
            ("run_flash_001", self.flash_doc),
            ("run_column_001", self.column_doc),
        ]
        self.warnings = []
        self.merged_data, self.run_map = _merge_flowsheet(
            self.ordered_runs, self.warnings
        )

    def test_both_equipment_present(self):
        """Both flash and column equipment are in merged output."""
        equip_ids = [e["id"] for e in self.merged_data["input"]["equipment"]]
        assert "flash" in equip_ids
        assert "column" in equip_ids
        assert len(equip_ids) == 2

    def test_only_root_feed_streams(self):
        """Only root's feed streams are included; chained_feed is excluded."""
        feeds = self.merged_data["input"]["feed_streams"]
        feed_ids = [f["stream_id"] for f in feeds]
        assert "feed_1" in feed_ids
        assert not any(fid.startswith("chained_feed_") for fid in feed_ids)
        assert len(feeds) == 1

    def test_synthetic_edge_created(self):
        """A synthetic edge connects flash liquid_outlet → column feed_inlet."""
        edges = self.merged_data["input"]["edges"]
        assert len(edges) == 1

        edge = edges[0]
        assert edge["source"] == "flash"
        assert edge["source_port"] == "liquid_outlet"
        assert edge["target"] == "column"
        assert edge["is_recycle"] is False

    def test_synthetic_edge_id_matches_outlet_stream(self):
        """Synthetic edge ID matches the upstream outlet's stream_id."""
        edges = self.merged_data["input"]["edges"]
        edge = edges[0]
        # The flash liquid outlet has stream_id "flash_liquid_out"
        assert edge["id"] == "flash_liquid_out"

    def test_stream_results_for_synthetic_edge(self):
        """Stream results contain an entry for the synthetic edge stream."""
        stream_results = self.merged_data["result"]["stream_results"]
        assert "flash_liquid_out" in stream_results
        sr = stream_results["flash_liquid_out"]
        assert sr["flow_rate"] == 60.0
        assert sr["phase"] == "liquid"

    def test_inlet_ports_rewired(self):
        """Column's inlet_ports reference the synthetic edge ID, not chained_feed."""
        equip_inputs = self.merged_data["result"]["equipment_inputs"]
        column_input = equip_inputs["column"]
        assert "flash_liquid_out" in column_input["inlet_ports"]
        assert "chained_feed_abc12345" not in column_input["inlet_ports"]

    def test_both_node_results_present(self):
        """Node results contain both flash and column."""
        nr = self.merged_data["result"]["node_results"]
        assert "flash" in nr
        assert "column" in nr

    def test_both_equipment_inputs_present(self):
        """Equipment inputs contain both flash and column."""
        ei = self.merged_data["result"]["equipment_inputs"]
        assert "flash" in ei
        assert "column" in ei

    def test_run_map_correct(self):
        """Run map maps each equipment to its source run."""
        assert self.run_map == {
            "flash": "run_flash_001",
            "column": "run_column_001",
        }

    def test_execution_order_merged(self):
        """Execution order includes equipment from both runs."""
        exec_order = self.merged_data["result"]["execution_order"]
        assert "flash" in exec_order
        assert "column" in exec_order

    def test_no_warnings(self):
        """Clean two-run chain produces no warnings."""
        assert len(self.warnings) == 0


# =============================================================================
# Tests for _merge_flowsheet — Branching (Flash → Column A + Column B)
# =============================================================================


class TestMergeBranching:
    """Test merging branching chains: flash → column_a (liquid) + column_b (vapor)."""

    def setup_method(self):
        flash_doc = make_flash_run_doc()
        flash_doc["downstream_runs"] = [
            {
                "run_id": "run_col_a",
                "source_equipment_id": "flash",
                "source_port": "liquid_outlet",
                "downstream_equipment_id": "column_a",
            },
            {
                "run_id": "run_col_b",
                "source_equipment_id": "flash",
                "source_port": "vapor_outlet",
                "downstream_equipment_id": "column_b",
            },
        ]

        col_a_doc = make_column_run_doc(
            run_id="run_col_a",
            source_run_id="run_flash_001",
            equipment_id="column_a",
        )
        col_a_doc["chain_metadata"]["source_port"] = "liquid_outlet"

        col_b_doc = make_column_run_doc(
            run_id="run_col_b",
            source_run_id="run_flash_001",
            equipment_id="column_b",
        )
        col_b_doc["chain_metadata"]["source_port"] = "vapor_outlet"

        self.ordered_runs = [
            ("run_flash_001", flash_doc),
            ("run_col_a", col_a_doc),
            ("run_col_b", col_b_doc),
        ]
        self.warnings = []
        self.merged_data, self.run_map = _merge_flowsheet(
            self.ordered_runs, self.warnings
        )

    def test_three_equipment_present(self):
        """All three equipment pieces are present."""
        equip_ids = [e["id"] for e in self.merged_data["input"]["equipment"]]
        assert len(equip_ids) == 3
        assert "flash" in equip_ids
        assert "column_a" in equip_ids
        assert "column_b" in equip_ids

    def test_two_synthetic_edges_created(self):
        """Two synthetic edges: liquid → column_a, vapor → column_b."""
        edges = self.merged_data["input"]["edges"]
        assert len(edges) == 2

        sources_ports = {(e["source"], e["source_port"]) for e in edges}
        assert ("flash", "liquid_outlet") in sources_ports
        assert ("flash", "vapor_outlet") in sources_ports

    def test_three_node_results(self):
        """Node results contain all three equipment."""
        nr = self.merged_data["result"]["node_results"]
        assert len(nr) == 3

    def test_run_map_branching(self):
        """Run map tracks all three equipment correctly."""
        assert self.run_map["flash"] == "run_flash_001"
        assert self.run_map["column_a"] == "run_col_a"
        assert self.run_map["column_b"] == "run_col_b"


# =============================================================================
# Tests for _merge_flowsheet — Equipment ID Collision Handling
# =============================================================================


class TestMergeIDCollision:
    """Test that equipment ID collisions are handled correctly."""

    def setup_method(self):
        """Create two runs with the same equipment ID 'heater'."""
        self.heater_a = make_flash_run_doc(run_id="run_heater_a")
        # Rename equipment to "heater"
        self.heater_a["result"]["input"]["equipment"] = [
            {"id": "heater", "type": "heater", "name": "Heater A"}
        ]
        self.heater_a["result"]["result"]["node_results"] = {
            "heater": {
                "converged": True,
                "iterations": 2,
                "warnings": [],
                "outlets": {
                    "outlet": {
                        "stream_id": "heater_a_outlet",
                        "flow_rate": 100.0,
                        "temperature_K": 400.0,
                        "pressure_Pa": 101325,
                        "composition": {"ethanol": 0.5, "water": 0.5},
                        "phase": "liquid",
                    }
                },
            }
        }
        self.heater_a["result"]["result"]["equipment_inputs"] = {
            "heater": {
                "equipment_type": "heater",
                "applied_parameters": {"duty_W": 5000},
                "parameter_constraints": {},
                "inlet_ports": ["feed_1"],
                "outlet_ports": ["outlet"],
            }
        }
        self.heater_a["result"]["result"]["execution_order"] = ["FEED", "heater", "PRODUCT"]
        self.heater_a["downstream_runs"] = [
            {"run_id": "run_heater_b", "source_equipment_id": "heater",
             "source_port": "outlet", "downstream_equipment_id": "heater"}
        ]

        self.heater_b = make_column_run_doc(
            run_id="run_heater_b",
            source_run_id="run_heater_a",
            equipment_id="heater",  # COLLISION!
        )
        self.heater_b["chain_metadata"]["source_equipment_id"] = "heater"
        self.heater_b["chain_metadata"]["source_port"] = "outlet"

    def test_collision_detected_and_renamed(self):
        """Second 'heater' gets renamed to 'heater_1'."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        equip_ids = [e["id"] for e in merged_data["input"]["equipment"]]
        assert "heater" in equip_ids
        assert "heater_1" in equip_ids
        assert len(equip_ids) == 2
        assert len(warnings) == 1
        assert "collision" in warnings[0].lower()

    def test_collision_node_results_both_preserved(self):
        """Both heater's node_results are preserved under different keys."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        nr = merged_data["result"]["node_results"]
        assert "heater" in nr
        assert "heater_1" in nr
        # Verify they're different
        assert nr["heater"]["iterations"] == 2

    def test_collision_equipment_inputs_both_preserved(self):
        """Both heater's equipment_inputs are preserved under different keys."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        ei = merged_data["result"]["equipment_inputs"]
        assert "heater" in ei
        assert "heater_1" in ei

    def test_collision_execution_order_both_present(self):
        """Both heater entries are in execution order."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        exec_order = merged_data["result"]["execution_order"]
        assert "heater" in exec_order
        assert "heater_1" in exec_order

    def test_collision_run_map(self):
        """Run map uses the renamed IDs."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        assert run_map["heater"] == "run_heater_a"
        assert run_map["heater_1"] == "run_heater_b"

    def test_collision_synthetic_edge_uses_correct_ids(self):
        """Synthetic edge connects original heater → renamed heater_1."""
        warnings = []
        merged_data, run_map = _merge_flowsheet(
            [("run_heater_a", self.heater_a), ("run_heater_b", self.heater_b)],
            warnings,
        )
        edges = merged_data["input"]["edges"]
        synthetic = [e for e in edges if e.get("source") == "heater"]
        assert len(synthetic) == 1
        assert synthetic[0]["target"] == "heater_1"


# =============================================================================
# Tests for GET /runs/{run_id}/flowsheet Endpoint
# =============================================================================


class TestFlowsheetEndpoint:
    """Integration tests for the flowsheet endpoint."""

    @pytest.fixture
    def app(self):
        """Create a FastAPI app with the runs router."""
        app = FastAPI()
        app.include_router(router, prefix="/api")
        return app

    @pytest.mark.asyncio
    async def test_standalone_run_returns_flowsheet(self, app):
        """Standalone run returns valid FlowsheetResponse."""
        flash_doc = make_flash_run_doc()
        flash_doc["downstream_runs"] = None  # No downstream

        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(side_effect=lambda q, *a, **kw: flash_doc)

        mock_db = MagicMock()
        mock_db.calc_simulation_runs = mock_collection

        mock_client = MagicMock()
        mock_client._client = {"arken_process_db": mock_db}
        mock_client.database_name = "arken_process_db"

        with patch("app.api.runs.get_mongo_client", return_value=mock_client):
            app.dependency_overrides = {}
            from app.dependencies import get_mongo_client
            app.dependency_overrides[get_mongo_client] = lambda: mock_client

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/runs/run_flash_001/flowsheet")

            assert resp.status_code == 200
            body = resp.json()
            assert body["run_id"] == "run_flash_001"
            assert body["root_run_id"] == "run_flash_001"
            assert body["all_run_ids"] == ["run_flash_001"]
            assert body["status"] == "success"
            assert "flash" in body["run_map"]
            assert body["chain_metadata"] is None

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, app):
        """Missing run returns 404."""
        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(return_value=None)

        mock_db = MagicMock()
        mock_db.calc_simulation_runs = mock_collection

        mock_client = MagicMock()
        mock_client._client = {"arken_process_db": mock_db}
        mock_client.database_name = "arken_process_db"

        from app.dependencies import get_mongo_client
        app.dependency_overrides[get_mongo_client] = lambda: mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/runs/nonexistent/flowsheet")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_chained_run_walks_up_to_root(self, app):
        """Chained run walks up to root and includes all runs."""
        flash_doc = make_flash_run_doc()
        column_doc = make_column_run_doc()

        async def mock_find_one(query, *args, **kwargs):
            rid = query.get("run_id")
            if rid == "run_column_001":
                return column_doc
            elif rid == "run_flash_001":
                return flash_doc
            return None

        mock_collection = AsyncMock()
        mock_collection.find_one = AsyncMock(side_effect=mock_find_one)

        mock_db = MagicMock()
        mock_db.calc_simulation_runs = mock_collection

        mock_client = MagicMock()
        mock_client._client = {"arken_process_db": mock_db}
        mock_client.database_name = "arken_process_db"

        from app.dependencies import get_mongo_client
        app.dependency_overrides[get_mongo_client] = lambda: mock_client

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/runs/run_column_001/flowsheet")

        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "run_column_001"
        assert body["root_run_id"] == "run_flash_001"
        assert "run_flash_001" in body["all_run_ids"]
        assert "run_column_001" in body["all_run_ids"]
        assert body["status"] == "success"

        # Both equipment present
        equip_ids = [e["id"] for e in body["data"]["input"]["equipment"]]
        assert "flash" in equip_ids
        assert "column" in equip_ids

        # Synthetic edge present
        edges = body["data"]["input"]["edges"]
        assert len(edges) >= 1
        assert any(e["source"] == "flash" and e["target"] == "column" for e in edges)

        # Only root feed streams
        feed_ids = [f["stream_id"] for f in body["data"]["input"]["feed_streams"]]
        assert "feed_1" in feed_ids
        assert not any(fid.startswith("chained_feed_") for fid in feed_ids)
