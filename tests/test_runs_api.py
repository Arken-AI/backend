"""
Tests for Runs API — Phase 7: Backend Adjustments

Tests cover:
1. Model changes: TemplateType enum, chain_metadata on RunResultResponse, 
   template_type + has_chain_metadata on RunListItem
2. normalize_run / normalize_run_summary helpers — new fields
3. GET /runs/{run_id} — returns chain_metadata, resolves template_type
4. GET /runs — template_type filter, process_server exclusion, empty results
5. System prompt — single-equipment and chaining guidance
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.models.runs import (
    RunResultResponse,
    RunListResponse,
    RunListItem,
    RunSource,
    RunStatus,
    TemplateType,
)
from app.api.runs import (
    normalize_run,
    normalize_run_summary,
    router,
)


# =============================================================================
# Test Data Fixtures
# =============================================================================

NOW = datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc)


def _calc_run_doc(
    run_id="run_001",
    process_id="single-distillation-column",
    template_type=None,
    chain_metadata=None,
    status="success",
):
    """Create a mock calc_simulation_runs MongoDB document."""
    doc = {
        "_id": "objectid123",
        "run_id": run_id,
        "user_id": "user_1",
        "process_id": process_id,
        "status": status,
        "created_at": NOW,
        "execution_time_ms": 1200,
        "result": {"node_results": {}, "stream_results": {}},
        "version_used": 1,
        "run_name": "Test Run",
        "completed_at": NOW,
        "payload_snapshot": {"equipment": []},
    }
    if template_type:
        doc["template_type"] = template_type
    if chain_metadata:
        doc["chain_metadata"] = chain_metadata
    return doc


def _process_server_run_doc(run_id="run_ps_001"):
    return {
        "_id": "objectid456",
        "run_id": run_id,
        "user_id": "user_1",
        "process_id": "sugar",
        "status": "completed",
        "created_at": NOW,
        "execution_time_ms": 800,
        "outputs": {"result": {}},
        "industry": "food",
        "inputs": {},
        "metadata": {},
    }


SAMPLE_CHAIN_METADATA = {
    "source_run_id": "run_heat_001",
    "source_equipment_id": "heater",
    "source_port": "outlet",
    "extracted_stream": {
        "flow_rate": 100.0,
        "temperature_K": 380.0,
        "pressure_Pa": 101325.0,
        "composition": {"ethanol": 0.3, "water": 0.7},
    },
}


# =============================================================================
# 1. Model Tests
# =============================================================================

class TestTemplateTypeEnum:
    def test_values(self):
        assert TemplateType.PROCESS.value == "process"
        assert TemplateType.SINGLE_EQUIPMENT.value == "single_equipment"

    def test_is_str_enum(self):
        assert isinstance(TemplateType.PROCESS, str)
        assert TemplateType.PROCESS == "process"


class TestRunResultResponseModel:
    def test_chain_metadata_field_optional(self):
        resp = RunResultResponse(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
        )
        assert resp.chain_metadata is None

    def test_chain_metadata_field_present(self):
        resp = RunResultResponse(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
            chain_metadata=SAMPLE_CHAIN_METADATA,
        )
        assert resp.chain_metadata["source_run_id"] == "run_heat_001"
        assert resp.chain_metadata["source_port"] == "outlet"

    def test_template_type_field_optional(self):
        resp = RunResultResponse(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
        )
        assert resp.template_type is None

    def test_template_type_field_present(self):
        resp = RunResultResponse(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
            template_type="single_equipment",
        )
        assert resp.template_type == "single_equipment"


class TestRunListItemModel:
    def test_has_chain_metadata_default_false(self):
        item = RunListItem(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
        )
        assert item.has_chain_metadata is False

    def test_has_chain_metadata_true(self):
        item = RunListItem(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
            has_chain_metadata=True,
        )
        assert item.has_chain_metadata is True

    def test_template_type_field(self):
        item = RunListItem(
            run_id="r1",
            source=RunSource.CALC_ENGINE,
            status=RunStatus.SUCCESS,
            created_at=NOW,
            template_type="single_equipment",
        )
        assert item.template_type == "single_equipment"


# =============================================================================
# 2. Normalize Helper Tests
# =============================================================================

class TestNormalizeRun:
    def test_calc_engine_includes_chain_metadata(self):
        doc = _calc_run_doc(chain_metadata=SAMPLE_CHAIN_METADATA)
        result = normalize_run(doc, "calc_engine")
        assert result["chain_metadata"] == SAMPLE_CHAIN_METADATA

    def test_calc_engine_no_chain_metadata(self):
        doc = _calc_run_doc()
        result = normalize_run(doc, "calc_engine")
        assert result["chain_metadata"] is None

    def test_calc_engine_includes_template_type(self):
        doc = _calc_run_doc(template_type="single_equipment")
        result = normalize_run(doc, "calc_engine")
        assert result["template_type"] == "single_equipment"

    def test_calc_engine_no_template_type(self):
        doc = _calc_run_doc()
        result = normalize_run(doc, "calc_engine")
        assert result["template_type"] is None

    def test_process_server_chain_metadata_always_none(self):
        doc = _process_server_run_doc()
        result = normalize_run(doc, "process_server")
        assert result["chain_metadata"] is None

    def test_process_server_template_type_always_none(self):
        doc = _process_server_run_doc()
        result = normalize_run(doc, "process_server")
        assert result["template_type"] is None

    def test_calc_engine_standard_fields_preserved(self):
        doc = _calc_run_doc()
        result = normalize_run(doc, "calc_engine")
        assert result["run_id"] == "run_001"
        assert result["source"] == "calc_engine"
        assert result["user_id"] == "user_1"
        assert result["process_id"] == "single-distillation-column"
        assert result["status"] == "success"
        assert result["data"] == {"node_results": {}, "stream_results": {}}


class TestNormalizeRunSummary:
    def test_calc_engine_has_chain_metadata_true(self):
        doc = _calc_run_doc(chain_metadata=SAMPLE_CHAIN_METADATA)
        result = normalize_run_summary(doc, "calc_engine")
        assert result["has_chain_metadata"] is True

    def test_calc_engine_has_chain_metadata_false(self):
        doc = _calc_run_doc()
        result = normalize_run_summary(doc, "calc_engine")
        assert result["has_chain_metadata"] is False

    def test_calc_engine_includes_template_type(self):
        doc = _calc_run_doc(template_type="process")
        result = normalize_run_summary(doc, "calc_engine")
        assert result["template_type"] == "process"

    def test_process_server_has_chain_metadata_false(self):
        doc = _process_server_run_doc()
        result = normalize_run_summary(doc, "process_server")
        assert result["has_chain_metadata"] is False

    def test_process_server_template_type_none(self):
        doc = _process_server_run_doc()
        result = normalize_run_summary(doc, "process_server")
        assert result["template_type"] is None


# =============================================================================
# 3. API Endpoint Tests (mocked MongoDB)
# =============================================================================

class AsyncCursorMock:
    """Mock for async MongoDB cursors (supports async for)."""
    def __init__(self, docs):
        self._docs = list(docs)
        self._idx = 0

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._idx]
        self._idx += 1
        return doc


def _build_mock_db(
    calc_runs=None,
    process_runs=None,
    templates=None,
    conversations=None,
):
    """
    Build a mock MongoDB database object with the given documents.
    """
    db = MagicMock()

    # calc_simulation_runs
    calc_coll = MagicMock()
    calc_coll.find_one = AsyncMock(
        return_value=calc_runs[0] if calc_runs else None
    )
    calc_coll.find = MagicMock(
        return_value=AsyncCursorMock(calc_runs or [])
    )
    db.calc_simulation_runs = calc_coll

    # runs (process_server)
    ps_coll = MagicMock()
    ps_coll.find_one = AsyncMock(
        return_value=process_runs[0] if process_runs else None
    )
    ps_coll.find = MagicMock(
        return_value=AsyncCursorMock(process_runs or [])
    )
    db.runs = ps_coll

    # calc_process_templates
    tmpl_coll = MagicMock()
    tmpl_coll.find_one = AsyncMock(
        return_value=templates[0] if templates else None
    )
    tmpl_coll.find = MagicMock(
        return_value=AsyncCursorMock(templates or [])
    )
    db.calc_process_templates = tmpl_coll

    # conversations
    conv_coll = MagicMock()
    conv_coll.find = MagicMock(
        return_value=AsyncCursorMock(conversations or [])
    )
    conv_coll.find_one = AsyncMock(
        return_value=conversations[0] if conversations else None
    )
    db.conversations = conv_coll

    return db


def _build_mock_mongo_client(db):
    mock_client = MagicMock()
    mock_client.database_name = "test_db"
    mock_client._client = {mock_client.database_name: db}
    return mock_client


def _create_test_app(mock_mongo_client):
    """Create a FastAPI test app with mocked dependencies."""
    from app.dependencies import get_mongo_client

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_mongo_client] = lambda: mock_mongo_client
    return app


# --- GET /runs/{run_id} ---

@pytest.mark.asyncio
async def test_get_run_returns_chain_metadata():
    """GET /runs/{run_id} should include chain_metadata for chained calc_engine runs."""
    doc = _calc_run_doc(chain_metadata=SAMPLE_CHAIN_METADATA)
    db = _build_mock_db(calc_runs=[doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_001")

    assert response.status_code == 200
    data = response.json()
    assert data["chain_metadata"] is not None
    assert data["chain_metadata"]["source_run_id"] == "run_heat_001"
    assert data["chain_metadata"]["source_port"] == "outlet"


@pytest.mark.asyncio
async def test_get_run_without_chain_metadata():
    """GET /runs/{run_id} should have chain_metadata=null for non-chained runs."""
    doc = _calc_run_doc()
    db = _build_mock_db(calc_runs=[doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_001")

    assert response.status_code == 200
    data = response.json()
    assert data["chain_metadata"] is None


@pytest.mark.asyncio
async def test_get_run_resolves_template_type_from_templates():
    """GET /runs/{run_id} should resolve template_type from calc_process_templates if not on run doc."""
    doc = _calc_run_doc()  # No template_type on the run doc
    template_doc = {"process_id": "single-distillation-column", "template_type": "single_equipment"}
    db = _build_mock_db(calc_runs=[doc], templates=[template_doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_001")

    assert response.status_code == 200
    data = response.json()
    assert data["template_type"] == "single_equipment"


@pytest.mark.asyncio
async def test_get_run_template_type_on_doc_takes_precedence():
    """If template_type is on the run doc itself, don't look up templates."""
    doc = _calc_run_doc(template_type="process")
    db = _build_mock_db(calc_runs=[doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_001")

    assert response.status_code == 200
    data = response.json()
    assert data["template_type"] == "process"
    # Should NOT have queried calc_process_templates
    db.calc_process_templates.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_run_404():
    """GET /runs/{run_id} should return 404 when run not found."""
    db = _build_mock_db()
    # Override find_one to return None for both collections
    db.calc_simulation_runs.find_one = AsyncMock(return_value=None)
    db.runs.find_one = AsyncMock(return_value=None)
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/nonexistent")

    assert response.status_code == 404


# --- GET /runs (list) ---

@pytest.mark.asyncio
async def test_list_runs_no_filters():
    """GET /runs without filters should return runs from both collections."""
    calc_doc = _calc_run_doc()
    ps_doc = _process_server_run_doc()
    db = _build_mock_db(calc_runs=[calc_doc], process_runs=[ps_doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs")

    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    sources = {r["source"] for r in data["runs"]}
    assert "calc_engine" in sources
    assert "process_server" in sources


@pytest.mark.asyncio
async def test_list_runs_template_type_filter_single_equipment():
    """GET /runs?template_type=single_equipment should only return matching calc_engine runs."""
    templates = [
        {"process_id": "single-distillation-column", "template_type": "single_equipment"},
    ]
    calc_doc = _calc_run_doc(
        process_id="single-distillation-column",
        template_type="single_equipment",
    )
    db = _build_mock_db(calc_runs=[calc_doc], templates=templates)
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs?template_type=single_equipment")

    assert response.status_code == 200
    data = response.json()
    # Should have queried templates first
    db.calc_process_templates.find.assert_called_once()
    # Should NOT have queried process_server collection
    db.runs.find.assert_not_called()
    assert len(data["runs"]) >= 1
    for run in data["runs"]:
        assert run["source"] == "calc_engine"


@pytest.mark.asyncio
async def test_list_runs_template_type_filter_no_matching_templates():
    """GET /runs?template_type=single_equipment with no matching templates returns empty."""
    db = _build_mock_db(templates=[])  # No matching templates
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs?template_type=single_equipment")

    assert response.status_code == 200
    data = response.json()
    assert data["runs"] == []
    assert data["has_more"] is False


@pytest.mark.asyncio
async def test_list_runs_template_type_with_mismatched_process_id():
    """GET /runs?template_type=single_equipment&process_id=sugar returns empty when mismatch."""
    templates = [
        {"process_id": "single-distillation-column", "template_type": "single_equipment"},
    ]
    db = _build_mock_db(templates=templates)
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/runs?template_type=single_equipment&process_id=sugar"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["runs"] == []


@pytest.mark.asyncio
async def test_list_runs_has_chain_metadata_flag():
    """List view should include has_chain_metadata=true for chained runs."""
    chained_doc = _calc_run_doc(
        run_id="run_chain",
        chain_metadata=SAMPLE_CHAIN_METADATA,
    )
    normal_doc = _calc_run_doc(run_id="run_normal")
    db = _build_mock_db(calc_runs=[chained_doc, normal_doc])
    mongo = _build_mock_mongo_client(db)
    app = _create_test_app(mongo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs?source=calc_engine")

    assert response.status_code == 200
    data = response.json()
    runs_by_id = {r["run_id"]: r for r in data["runs"]}
    assert runs_by_id["run_chain"]["has_chain_metadata"] is True
    assert runs_by_id["run_normal"]["has_chain_metadata"] is False


# =============================================================================
# 4. System Prompt Tests
# =============================================================================

class TestSystemPromptEnhancement:
    """Verify the orchestration service system prompt includes single-equipment and chaining guidance."""

    def _get_system_parts(self):
        """Extract the system_parts list from the orchestration service source."""
        import inspect
        from app.services.orchestration_service import OrchestrationService
        source = inspect.getsource(OrchestrationService)
        return source

    def test_single_equipment_guidance_present(self):
        source = self._get_system_parts()
        assert "SINGLE-EQUIPMENT SIMULATION" in source
        assert "single_equipment" in source
        assert "compound_mapping" in source

    def test_chaining_guidance_present(self):
        source = self._get_system_parts()
        assert "EQUIPMENT CHAINING" in source
        assert "calc_chain_equipment" in source

    def test_compound_mapping_guidance(self):
        source = self._get_system_parts()
        assert "compound_mapping" in source
        assert "generic compound" in source.lower() or "generic compounds" in source.lower()

    def test_connection_validation_guidance(self):
        source = self._get_system_parts()
        assert "phase mismatch" in source.lower() or "connection is invalid" in source.lower()

    def test_multi_outlet_guidance(self):
        source = self._get_system_parts()
        assert "multi-outlet" in source.lower() or "vapor_outlet" in source.lower()
