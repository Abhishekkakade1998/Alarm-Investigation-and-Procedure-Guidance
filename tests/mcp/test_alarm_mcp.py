import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx


SERVER_PATH = (
    Path(__file__).resolve().parents[2]
    / "mcp-servers"
    / "alarm-management"
    / "server.py"
)
SPEC = importlib.util.spec_from_file_location("alarm_management_server_test", SERVER_PATH)
server_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server_module)


def test_mcp_server_exposes_required_typed_tools():
    tools = asyncio.run(server_module.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert {"search_assets", "get_alarms", "get_asset_metadata", "alarm_correlation"} <= set(by_name)
    assert by_name["search_assets"].inputSchema["required"] == ["query"]
    assert by_name["get_alarms"].inputSchema["properties"]["page_size"]["type"] == "integer"
    assert "site" in by_name["get_alarms"].inputSchema["properties"]
    assert by_name["search_assets"].inputSchema["additionalProperties"] is False
    assert "trace_id" in by_name["search_assets"].inputSchema["properties"]


def test_get_alarms_accepts_site_without_asset_id(monkeypatch):
    class FakeAlarmClient:
        async def get_alarms(self, asset_id, page, page_size, site):
            assert asset_id is None
            assert site == "EastRefinery"
            assert page_size == 200
            return {
                "total": 1,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "data": [{
                    "alarm_id": "ALM-A001-000",
                    "asset_id": "A001",
                    "alarm_name": "Temperature High",
                    "severity": "critical",
                    "status": "active",
                    "start_time": "2026-11-11T00:00:00Z",
                }],
            }

    monkeypatch.setattr(server_module, "alarm_client", FakeAlarmClient())
    result = asyncio.run(server_module.call_tool(
        "get_alarms",
        {"site": "EastRefinery", "page_size": 200},
    ))

    assert '"alarm_id": "ALM-A001-000"' in result[0].text

    with pytest.raises(ValueError, match="Provide either asset_id or site"):
        asyncio.run(server_module.call_tool("get_alarms", {}))


def test_mcp_server_invokes_api_connector(monkeypatch):
    class FakeAlarmClient:
        async def search_assets(self, query, limit):
            return {"total": 1, "results": [{
                "asset_id": "A001",
                "asset_name": query,
                "asset_type": "Pump",
                "unit": "Unit 1",
                "location": "EastRefinery",
                "criticality": "high",
                "limit": limit,
            }]}

    monkeypatch.setattr(server_module, "alarm_client", FakeAlarmClient())
    result = asyncio.run(server_module.call_tool(
        "search_assets", {"query": "Boiler", "limit": 4}
    ))

    assert result[0].text
    assert '"asset_id": "A001"' in result[0].text


def test_mcp_server_validates_input_and_output(monkeypatch):
    class FakeAlarmClient:
        async def search_assets(self, _query, _limit):
            return {"total": 1, "results": [{"asset_id": "missing-fields"}]}

    monkeypatch.setattr(server_module, "alarm_client", FakeAlarmClient())

    with pytest.raises(ValueError, match="Invalid arguments"):
        asyncio.run(server_module.call_tool(
            "search_assets", {"query": "", "unexpected": True}
        ))

    with pytest.raises(ValueError, match="invalid output"):
        asyncio.run(server_module.call_tool(
            "search_assets", {"query": "pump"}
        ))


def test_alarm_api_client_retries_transient_upstream_status():
    client = server_module.AlarmAPIClient("http://alarm-api", "demo-token")
    responses = [
        httpx.Response(
            503,
            request=httpx.Request("GET", "http://alarm-api/health"),
        ),
        httpx.Response(
            200,
            json={"status": "ok"},
            request=httpx.Request("GET", "http://alarm-api/health"),
        ),
    ]

    class FakeHttpClient:
        async def request(self, *_args, **_kwargs):
            return responses.pop(0)

    client._http_client = FakeHttpClient()
    result = asyncio.run(client._request("GET", "http://alarm-api/health"))

    assert result.json() == {"status": "ok"}
    assert not responses


def test_alarm_api_client_forwards_site_alarm_filter():
    client = server_module.AlarmAPIClient("http://alarm-api", "demo-token")
    response = httpx.Response(
        200,
        json={"total": 0, "page": 1, "page_size": 200, "total_pages": 0, "data": []},
        request=httpx.Request("GET", "http://alarm-api/alarms"),
    )

    class FakeHttpClient:
        async def request(self, method, url, **kwargs):
            assert method == "GET"
            assert url == "http://alarm-api/alarms"
            assert kwargs["params"] == {
                "page": 1,
                "page_size": 200,
                "site": "EastRefinery",
            }
            return response

    client._http_client = FakeHttpClient()
    result = asyncio.run(client.get_alarms(
        page=1,
        page_size=200,
        site="EastRefinery",
    ))

    assert result["total"] == 0


def test_alarm_api_client_propagates_trace_metadata():
    client = server_module.AlarmAPIClient("http://alarm-api", "demo-token")
    trace_token = server_module.TRACE_METADATA.set({
        "trace_id": "test-trace",
        "x-client-id": "test-client",
    })
    try:
        headers = client._request_headers()
    finally:
        server_module.TRACE_METADATA.reset(trace_token)

    assert headers["trace_id"] == "test-trace"
    assert headers["x-client-id"] == "test-client"


def test_alarm_summary_request_propagates_trace_metadata():
    captured_headers = {}

    def handle_request(request):
        captured_headers.update(request.headers)
        return httpx.Response(
            200,
            json={"total_alarms": 0},
            request=request,
        )

    async def run():
        client = server_module.AlarmAPIClient("http://alarm-api", "demo-token")
        client._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle_request)
        )
        trace_token = server_module.TRACE_METADATA.set({
            "trace_id": "summary-trace",
            "x-client-id": "summary-client",
            "x-metadata-tag": "summary-test",
        })
        try:
            await client.alarm_summary(
                ["A001"],
                {"start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-02T00:00:00Z"},
            )
        finally:
            server_module.TRACE_METADATA.reset(trace_token)
            await client.aclose()

    asyncio.run(run())

    assert captured_headers["trace_id"] == "summary-trace"
    assert captured_headers["x-client-id"] == "summary-client"
    assert captured_headers["x-metadata-tag"] == "summary-test"


def test_mcp_server_raises_connector_errors_for_sdk_mapping(monkeypatch):
    class FailingAlarmClient:
        async def search_assets(self, _query, _limit):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(server_module, "alarm_client", FailingAlarmClient())
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        asyncio.run(server_module.call_tool("search_assets", {"query": "Boiler"}))


def test_mcp_server_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Unknown tool"):
        asyncio.run(server_module.call_tool("unknown_tool", {}))
