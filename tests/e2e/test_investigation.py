"""End-to-End Test: Complete Alarm Investigation Workflow"""
import pytest
import httpx
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8001"
ALARM_API_URL = "http://localhost:8000"

@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        yield client

@pytest.fixture
async def alarm_client():
    async with httpx.AsyncClient(base_url=ALARM_API_URL) as client:
        yield client

@pytest.mark.asyncio
async def test_health_check():
    """Test that services are healthy"""
    async with httpx.AsyncClient() as client:
        # Check simulator
        resp = await client.get(f"{ALARM_API_URL}/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Check copilot
        resp = await client.get(f"{BASE_URL}/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_asset_search(alarm_client):
    """Test asset search through API"""
    resp = await alarm_client.get(
        "/assets/search",
        params={"query": "Boiler", "limit": 10},
        headers={"Authorization": "Bearer demo-token"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert len(data["results"]) > 0
    assert "asset_id" in data["results"][0]

@pytest.mark.asyncio
async def test_get_alarms(alarm_client):
    """Test getting alarms for an asset"""
    resp = await alarm_client.get(
        "/alarms",
        params={"asset_id": "A001", "page": 1, "page_size": 50},
        headers={"Authorization": "Bearer demo-token"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert data["page"] == 1

@pytest.mark.asyncio
async def test_alarm_summary(alarm_client):
    """Test alarm summary analysis"""
    now = datetime.utcnow()
    start_time = (now - timedelta(days=90)).isoformat() + "Z"
    end_time = now.isoformat() + "Z"

    resp = await alarm_client.post(
        "/alarms/summary",
        json={
            "asset_ids": ["A001"],
            "time_range": {
                "start_time": start_time,
                "end_time": end_time
            },
            "severity": ["high", "critical"]
        },
        headers={"Authorization": "Bearer demo-token"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_alarms" in data
    assert "critical_count" in data

@pytest.mark.asyncio
async def test_investigation_endpoint(client):
    """Test the main investigation endpoint"""
    investigation = {
        "query": "Show active critical alarms for Boiler Feed Pump 101",
        "use_rag": True,
        "use_mcp": False
    }

    resp = await client.post(
        "/investigate",
        json=investigation
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "investigation_id" in data
    assert "query" in data
    assert "answer" in data
    assert "rag_sources" in data
    assert len(data["answer"]) > 0

@pytest.mark.asyncio
async def test_investigation_with_rag_sources(client):
    """Test that RAG sources are properly retrieved"""
    investigation = {
        "query": "What should I do about a vibration alarm on a motor?",
        "use_rag": True,
        "use_mcp": False
    }

    resp = await client.post(
        "/investigate",
        json=investigation
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "rag_sources" in data
    # Should have retrieved documents related to the query
    if data["rag_sources"]:
        source = data["rag_sources"][0]
        assert "content" in source
        assert "source" in source
        assert "score" in source

@pytest.mark.asyncio
async def test_mcp_tools_listing(client):
    """Test that MCP tools are properly listed"""
    resp = await client.get("/mcp/tools")

    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) > 0

    # Check for expected tools
    expected_tools = ["search_assets", "get_alarms", "alarm_summary"]
    for tool in expected_tools:
        assert tool in data["tools"]

@pytest.mark.asyncio
async def test_rag_status(client):
    """Test RAG system status"""
    resp = await client.get("/rag/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data

@pytest.mark.asyncio
async def test_example_scenarios(client):
    """Test that example scenarios are available"""
    resp = await client.get("/examples")

    assert resp.status_code == 200
    data = resp.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) > 0

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
