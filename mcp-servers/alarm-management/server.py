"""MCP Server for Alarm Management API Integration"""
import json
import logging
import os
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# MCP imports
from mcp.server import Server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ALARM_API_BASE_URL = os.getenv("ALARM_API_BASE_URL", "http://localhost:8000")
ALARM_API_TOKEN = os.getenv("ALARM_API_TOKEN", "demo-token")
TRACE_METADATA: ContextVar[dict[str, str]] = ContextVar(
    "alarm_trace_metadata", default={}
)


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    x_client_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    x_metadata_tag: Optional[str] = Field(default=None, min_length=1, max_length=256)


class SearchAssetsArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)


class AssetIdArguments(ToolArguments):
    asset_id: str = Field(min_length=1, max_length=128)


class GetAlarmsArguments(ToolArguments):
    asset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    site: Optional[str] = Field(default=None, min_length=1, max_length=128)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def require_asset_or_site(self):
        if not self.asset_id and not self.site:
            raise ValueError("Provide either asset_id or site")
        return self


class AlarmIdArguments(ToolArguments):
    alarm_id: str = Field(min_length=1, max_length=128)


class AlarmRangeArguments(ToolArguments):
    asset_ids: list[str] = Field(min_length=1, max_length=10)
    start_time: str
    end_time: str


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class AssetSearchItem(ToolOutput):
    asset_id: str
    asset_name: str
    asset_type: str
    unit: str
    location: str
    criticality: str


class SearchAssetsOutput(ToolOutput):
    total: int = Field(ge=0)
    results: list[AssetSearchItem]


class AlarmRecordOutput(ToolOutput):
    alarm_id: str
    asset_id: str
    alarm_name: str
    severity: str
    status: str
    start_time: str


class GetAlarmsOutput(ToolOutput):
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
    data: list[AlarmRecordOutput]


class AlarmDetailOutput(AlarmRecordOutput):
    asset_name: str
    description: str
    count: int
    recurring: bool


class AssetMetadataOutput(ToolOutput):
    asset_id: str
    asset_name: str
    asset_type: str
    unit: str
    location: str
    criticality: str
    related_assets: list[str]
    kpi_targets: dict[str, Any]


class AlarmSummaryOutput(ToolOutput):
    total_alarms: int = Field(ge=0)
    critical_count: int = Field(ge=0)
    high_count: int = Field(ge=0)
    medium_count: int = Field(ge=0)


class CorrelationOutput(ToolOutput):
    method: str
    asset_pairs: list[dict[str, Any]]
    root_cause_candidates: list[str]
    confidence_score: float = Field(ge=0, le=1)


class PriorityOutput(ToolOutput):
    alarm_id: str
    score: float = Field(ge=0, le=1)
    reason: str
    recommendations: list[str]


class RecommendationsOutput(ToolOutput):
    alarm_id: str
    immediate_actions: list[str]
    preventive_measures: list[str]
    related_assets: list[str]
    documentation_references: list[str]


TOOL_ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "search_assets": SearchAssetsArguments,
    "get_asset_metadata": AssetIdArguments,
    "get_alarms": GetAlarmsArguments,
    "get_alarm_by_id": AlarmIdArguments,
    "alarm_summary": AlarmRangeArguments,
    "alarm_correlation": AlarmRangeArguments,
    "priority_score": AlarmIdArguments,
    "operator_recommendations": AlarmIdArguments,
}
TOOL_OUTPUT_MODELS: dict[str, type[ToolOutput]] = {
    "search_assets": SearchAssetsOutput,
    "get_asset_metadata": AssetMetadataOutput,
    "get_alarms": GetAlarmsOutput,
    "get_alarm_by_id": AlarmDetailOutput,
    "alarm_summary": AlarmSummaryOutput,
    "alarm_correlation": CorrelationOutput,
    "priority_score": PriorityOutput,
    "operator_recommendations": RecommendationsOutput,
}


def _validate_tool_arguments(name: str, arguments: dict) -> dict:
    model = TOOL_ARGUMENT_MODELS.get(name)
    if model is None:
        raise ValueError(f"Unknown tool: {name}")
    try:
        validated = model.model_validate(arguments)
    except ValidationError as error:
        raise ValueError(f"Invalid arguments for '{name}': {error}") from error
    return validated.model_dump(exclude_none=True)


def _serialize_tool_output(name: str, result: dict) -> str:
    model = TOOL_OUTPUT_MODELS.get(name)
    if model is None or not isinstance(result, dict):
        raise ValueError(f"Invalid output for MCP tool '{name}'")
    try:
        validated = model.model_validate(result)
    except ValidationError as error:
        raise ValueError(f"Alarm API returned invalid output for '{name}': {error}") from error
    return json.dumps(validated.model_dump(mode="json"), ensure_ascii=False)


class AlarmAPIClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self._http_client: Optional[httpx.AsyncClient] = None
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10)
        return self._http_client

    def _request_headers(self) -> dict[str, str]:
        return {**self.headers, **TRACE_METADATA.get()}

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        response: Optional[httpx.Response] = None
        for attempt in range(3):
            try:
                response = await self._get_http_client().request(
                    method, url, **kwargs
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.1 * (2 ** attempt))
                continue
            if response.status_code not in {502, 503, 504} or attempt == 2:
                response.raise_for_status()
                return response
            await asyncio.sleep(0.1 * (2 ** attempt))
        if response is None:
            raise RuntimeError("Alarm API request did not produce a response")
        response.raise_for_status()
        return response

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def search_assets(self, query: str, limit: int = 10) -> dict:
        resp = await self._request(
            "GET",
            f"{self.base_url}/assets/search",
            params={"query": query, "limit": limit},
            headers=self._request_headers(),
        )
        return resp.json()

    async def get_asset_metadata(self, asset_id: str) -> dict:
        resp = await self._request(
            "GET",
            f"{self.base_url}/assets/{asset_id}/metadata",
            headers=self._request_headers(),
        )
        return resp.json()

    async def get_alarms(
        self,
        asset_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        site: Optional[str] = None,
    ) -> dict:
        params = {
            key: value
            for key, value in {
                "asset_id": asset_id,
                "page": page,
                "page_size": page_size,
                "site": site,
            }.items()
            if value is not None
        }
        resp = await self._request(
            "GET",
            f"{self.base_url}/alarms",
            params=params,
            headers=self._request_headers(),
        )
        return resp.json()

    async def get_alarm_by_id(self, alarm_id: str) -> dict:
        resp = await self._request(
            "GET",
            f"{self.base_url}/alarms/{alarm_id}",
            headers=self._request_headers(),
        )
        return resp.json()

    async def alarm_summary(self, asset_ids: list, time_range: dict, trace_id: Optional[str] = None) -> dict:
        headers = self._request_headers()
        if trace_id:
            headers["trace_id"] = trace_id

        resp = await self._request(
            "POST",
            f"{self.base_url}/alarms/summary",
            json={"asset_ids": asset_ids, "time_range": time_range},
            headers=headers,
        )
        return resp.json()

    async def alarm_correlation(self, asset_ids: list, time_range: dict, trace_id: Optional[str] = None) -> dict:
        headers = self._request_headers()
        if trace_id:
            headers["trace_id"] = trace_id

        resp = await self._request(
            "POST",
            f"{self.base_url}/alarms/correlation",
            json={
                "asset_ids": asset_ids,
                "time_range": time_range,
                "correlation_method": "cooccurrence",
                "lag_window_minutes": 15,
                "severity_threshold": "medium",
            },
            headers=headers,
        )
        return resp.json()

    async def priority_score(self, alarm_id: str) -> dict:
        resp = await self._request(
            "POST",
            f"{self.base_url}/alarms/priority-score",
            json={"alarm_id": alarm_id},
            headers=self._request_headers(),
        )
        return resp.json()

    async def operator_recommendations(self, alarm_id: str, trace_id: Optional[str] = None) -> dict:
        headers = self._request_headers()
        if trace_id:
            headers["trace_id"] = trace_id

        resp = await self._request(
            "POST",
            f"{self.base_url}/recommendations/operator-actions",
            json={
                "alarm_id": alarm_id,
                "include_related": True,
                "include_asset_context": True,
                "include_historical_pattern": True,
            },
            headers=headers,
        )
        return resp.json()

# MCP Server
server = Server("alarm-management-mcp")
alarm_client = AlarmAPIClient(ALARM_API_BASE_URL, ALARM_API_TOKEN)

@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="search_assets",
            description="Search for assets by name or identifier. Returns matching assets with IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Asset name or keyword to search"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_asset_metadata",
            description="Get detailed metadata for a specific asset including type, location, related assets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "description": "The asset identifier"}
                },
                "required": ["asset_id"]
            }
        ),
        Tool(
            name="get_alarms",
            description=(
                "Retrieve alarms for one asset or all assets at a site. "
                "For site-wide questions provide site (for example EastRefinery) "
                "instead of guessing an asset ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "description": "Asset identifier"},
                    "site": {"type": "string", "description": "Site name for site-wide alarm retrieval"},
                    "page": {"type": "integer", "description": "Page number (default 1)"},
                    "page_size": {"type": "integer", "description": "Results per page (default 50)"}
                },
            }
        ),
        Tool(
            name="get_alarm_by_id",
            description="Get detailed information about a specific alarm.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alarm_id": {"type": "string", "description": "The alarm identifier"}
                },
                "required": ["alarm_id"]
            }
        ),
        Tool(
            name="alarm_summary",
            description="Generate a summary of alarms for assets in a time range. Returns counts and statistics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "asset_ids": {"type": "array", "items": {"type": "string"}, "description": "List of asset IDs"},
                    "start_time": {"type": "string", "description": "ISO timestamp (e.g., 2026-05-01T00:00:00Z)"},
                    "end_time": {"type": "string", "description": "ISO timestamp (e.g., 2026-07-01T00:00:00Z)"}
                },
                "required": ["asset_ids", "start_time", "end_time"]
            }
        ),
        Tool(
            name="alarm_correlation",
            description="Compare alarm co-occurrence across at least two resolved asset IDs. Requires asset_ids, start_time, and end_time; not needed to analyze one asset's alarm history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "asset_ids": {"type": "array", "items": {"type": "string"}},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"}
                },
                "required": ["asset_ids", "start_time", "end_time"]
            }
        ),
        Tool(
            name="priority_score",
            description="Calculate priority score for an alarm to determine urgency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alarm_id": {"type": "string", "description": "The alarm identifier"}
                },
                "required": ["alarm_id"]
            }
        ),
        Tool(
            name="operator_recommendations",
            description="Get recommended actions for an alarm including immediate actions and preventive measures.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alarm_id": {"type": "string", "description": "The alarm identifier"}
                },
                "required": ["alarm_id"]
            }
        ),
    ]
    for tool in tools:
        tool.inputSchema = TOOL_ARGUMENT_MODELS[tool.name].model_json_schema()
    logger.info("Discovered %d alarm-management MCP tools", len(tools))
    return tools

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> Any:
    arguments = _validate_tool_arguments(name, arguments)
    trace_headers = {
        "trace_id": arguments.get("trace_id"),
        "x-client-id": arguments.get("x_client_id"),
        "x-metadata-tag": arguments.get("x_metadata_tag"),
    }
    trace_headers = {
        key: value for key, value in trace_headers.items() if value is not None
    }
    trace_token = TRACE_METADATA.set(trace_headers)
    try:
        if name == "search_assets":
            result = await alarm_client.search_assets(
                arguments["query"],
                arguments.get("limit", 10)
            )
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "get_asset_metadata":
            result = await alarm_client.get_asset_metadata(arguments["asset_id"])
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "get_alarms":
            result = await alarm_client.get_alarms(
                arguments.get("asset_id"),
                arguments.get("page", 1),
                arguments.get("page_size", 50),
                arguments.get("site"),
            )
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "get_alarm_by_id":
            result = await alarm_client.get_alarm_by_id(arguments["alarm_id"])
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "alarm_summary":
            result = await alarm_client.alarm_summary(
                arguments["asset_ids"],
                {
                    "start_time": arguments["start_time"],
                    "end_time": arguments["end_time"]
                }
            )
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "alarm_correlation":
            result = await alarm_client.alarm_correlation(
                arguments["asset_ids"],
                {
                    "start_time": arguments["start_time"],
                    "end_time": arguments["end_time"]
                }
            )
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "priority_score":
            result = await alarm_client.priority_score(arguments["alarm_id"])
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        elif name == "operator_recommendations":
            result = await alarm_client.operator_recommendations(arguments["alarm_id"])
            return [TextContent(type="text", text=_serialize_tool_output(name, result))]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.exception("Tool error: %s", name)
        raise
    finally:
        TRACE_METADATA.reset(trace_token)

transport = SseServerTransport("/messages/")

class MCPSSERoute:
    async def __call__(self, scope, receive, send):
        async with transport.connect_sse(scope, receive, send) as streams:
            await server.run(*streams, server.create_initialization_options(), raise_exceptions=True)


class MCPMessageRoute:
    async def __call__(self, scope, receive, send):
        await transport.handle_post_message(scope, receive, send)


async def health(_request: Request):
    return JSONResponse({"status": "ok", "service": "alarm-management-mcp"})


@asynccontextmanager
async def app_lifespan(_app: Starlette) -> AsyncIterator[None]:
    yield
    await alarm_client.aclose()


app = Starlette(routes=[
    Route("/sse", endpoint=MCPSSERoute()),
    Route("/messages/", endpoint=MCPMessageRoute(), methods=["POST"]),
    Route("/health", endpoint=health),
], lifespan=app_lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
