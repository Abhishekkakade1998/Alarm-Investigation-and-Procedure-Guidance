# Alarm API Integration

## Contract and configuration

The simulator implements the Alarm Management API used by the supplied
Postman collections. It serves synthetic asset and alarm data from
`apps/backend/simulator.py`. The MCP server is the copilot's only integration
path to alarm data; the copilot backend does not call the simulator directly.

Configure both simulator and MCP server with the same `ALARM_API_TOKEN`.
Requests to protected routes use `Authorization: Bearer <token>`. `/health`
does not require authentication. The local demo token is only a development
default; set a private value in `.env` or the process environment before using
Compose with a non-local network.

The simulator accepts `trace_id`, `x-client-id`, and `x-metadata-tag` headers.
It echoes provided metadata in response headers and includes fields in the
response body for the summary, correlation, recommendation, and calculation
execution routes where applicable. Trace headers are also included in simulator
request log context.

## Endpoint surface

| Method and path | Authentication | Request / query | Behavior |
|---|---|---|---|
| `GET /health` | None | — | Service health. |
| `GET /assets/search` | Bearer | `query` (required), `limit` (1–100), optional `site`, `unit` | Case-insensitive substring search; returns `total` and `results`. |
| `GET /assets/{asset_id}/metadata` | Bearer | Asset identifier in path | Returns asset information, related asset IDs, and KPI targets. |
| `GET /alarms` | Bearer | Optional `asset_id`, `site`, `unit`, `status`, `start_time`, `end_time`; `page`, `page_size` (1–200), `sort_by`, `sort_order` | Returns a paginated `data` list and page totals. Supported sort fields are `start_time`, `severity`, `status`, `alarm_name`, and `count`; order is `asc` or `desc`. |
| `GET /alarms/{alarm_id}` | Bearer | Alarm identifier in path | Returns one alarm or 404. |
| `POST /alarms/summary` | Bearer | `asset_ids`, `time_range` or top-level time bounds; optional `site`, `unit`, `severity`, `alarm_types`, `group_by`, `kpis` | Computes counts, recurrence and acknowledgement metrics from matching synthetic alarms. |
| `POST /alarms/trends` | Bearer | Asset/site/unit/time filters; `bucket` (`hourly`, `daily`, `weekly`), `metrics` | Groups matching alarms into requested time buckets. |
| `POST /alarms/correlation` | Bearer | `asset_ids` (maximum 10), time range, `lag_window_minutes`, `min_support` | Computes same-type alarm co-occurrence between selected assets. |
| `POST /alarms/flood-analysis` | Bearer | Site/unit/time filters, `rolling_window_minutes`, `threshold_count` | Reports the maximum alarm count in a rolling time window. |
| `POST /alarms/rationalization-candidates` | Bearer | Asset/site/unit/time filters, `recurrence_threshold`, `stale_minutes_threshold` | Returns recurring alarms meeting the selected count threshold. |
| `POST /alarms/priority-score` | Bearer | `alarm_id` | Computes a deterministic severity/recurrence/count score and returns generic safe recommendations. |
| `POST /recommendations/operator-actions` | Bearer | `alarm_id`; optional `include_related` | Returns synthetic generic actions, related asset IDs, and reference labels. |
| `POST /calculation-code/generate` | Bearer | One of four supported `calculation_type` values and optional filters | Creates an in-memory calculation job; it does not generate or execute arbitrary code. |
| `POST /calculation-code/execute` | Bearer | `calculation_id`, optional filters | Computes the pre-defined calculation type for the stored job. |
| `GET /analytics/kpi-definitions` | Bearer | — | Lists supported KPI definitions. |

Timestamps are parsed as ISO 8601. Naive timestamps are interpreted as UTC.
Invalid timestamps, reversed time ranges, unsupported sorting, and invalid
pagination produce HTTP 422 responses. Unknown assets and alarms produce 404;
missing or invalid bearer credentials produce 401.

## MCP connector and chaining

The MCP server exposes eight tools: `search_assets`, `get_asset_metadata`,
`get_alarms`, `get_alarm_by_id`, `alarm_summary`, `alarm_correlation`,
`priority_score`, and `operator_recommendations`. Tool arguments are validated
with Pydantic before the connector sends an HTTP request; selected response
fields are validated before returning tool text. MCP request errors are raised
to the SDK and surfaced in the copilot trace.

The primary chain is:

1. `search_assets(query)` resolves a user-facing asset name to an `asset_id`.
2. `get_alarms(asset_id, page, page_size)` retrieves alarm records.
3. Optional `get_asset_metadata(asset_id)` provides related asset context.
4. `alarm_correlation(asset_ids, start_time, end_time)` and
   `priority_score(alarm_id)` support analysis when selected by the model.
5. `operator_recommendations(alarm_id)` returns simulator recommendations.
6. The copilot combines successful MCP results with retrieved document evidence
   and returns both MCP trace entries and RAG citations.

Some simulator endpoints (trends, flood analysis, rationalization, and
calculation jobs) are available through HTTP/Postman but are not exposed as MCP
tools in the current copilot workflow.

## Timeouts, retries, and test coverage

The MCP connector reuses an `httpx.AsyncClient`, uses a 10-second API timeout,
and makes up to three attempts for connection/timeouts and upstream 502/503/504
responses, with a short exponential delay. Other HTTP errors are raised without
retry. MCP tool input/output validation, retry behavior, trace forwarding,
simulator filtering, pagination, and analytics have automated tests in
`tests/mcp/` and `tests/unit/`.

Run the API contract tests locally:

```powershell
python -m pytest tests\unit\test_simulator.py tests\mcp\test_alarm_mcp.py -v
```
