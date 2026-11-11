# MCP Tool Catalog

The alarm-management MCP server uses the MCP SDK's SSE transport and connects
to the simulator over HTTP. It exposes eight tools. Input arguments are
validated by Pydantic with unknown fields rejected. Selected output fields are
validated before being serialized as JSON text in MCP `TextContent`; the
installed MCP SDK `Tool` contract does not expose a separate output schema.
Output models allow extra API fields so the connector can preserve useful
simulator response data.

All tools use the configured `ALARM_API_BASE_URL` and bearer token
(`ALARM_API_TOKEN`). Optional trace arguments are `trace_id`, `x_client_id`,
and `x_metadata_tag`; when supplied, they are forwarded as request headers.

## `search_assets`

- **Purpose:** Resolve a user-facing asset name or keyword to asset IDs.
- **Arguments:** `query` (string, 1–200 chars, required), `limit` (integer,
  1–100, default 10), optional trace fields.
- **API operation:** `GET /assets/search?query=...&limit=...`.
- **Validated output:** `{total: int, results: [{asset_id, asset_name,
  asset_type, unit, location, criticality}]}`.
- **Errors/timeouts:** Invalid arguments fail validation. API errors propagate
  through the MCP SDK. API timeout is 10 seconds per attempt; transient
  connection/timeouts and upstream 502/503/504 responses are retried.
- **Example invocation:**

  ```json
  {"name":"search_assets","arguments":{"query":"Boiler Feed Pump 101","limit":10}}
  ```

- **Example response shape:**

  ```json
  {"total":1,"results":[{"asset_id":"A001","asset_name":"Boiler Feed Pump 101","asset_type":"Pump","unit":"Unit 1","location":"EastRefinery","criticality":"high"}]}
  ```

## `get_asset_metadata`

- **Purpose:** Retrieve asset information and related asset IDs.
- **Arguments:** `asset_id` (string, 1–128 chars, required), optional trace
  fields.
- **API operation:** `GET /assets/{asset_id}/metadata`.
- **Validated output:** `asset_id`, `asset_name`, `asset_type`, `unit`,
  `location`, `criticality`, `related_assets`, and `kpi_targets`.
- **Errors/timeouts:** Unknown asset returns API 404, surfaced as an MCP error;
  timeout/retry behavior is shared with the connector.
- **Example invocation:** `{"name":"get_asset_metadata","arguments":{"asset_id":"A001"}}`
- **Example response shape:**

  ```json
  {"asset_id":"A001","asset_name":"Boiler Feed Pump 101","asset_type":"Pump","unit":"Unit 1","location":"EastRefinery","criticality":"high","related_assets":["A002","A003"],"kpi_targets":{"max_temp":185,"min_flow":100,"max_vibration":5}}
  ```

## `get_alarms`

- **Purpose:** Retrieve a page of alarms for an asset or all alarms at a site.
- **Arguments:** Provide either `asset_id` or `site` (string, 1–128 chars);
  `page` (integer, minimum 1, default 1), `page_size` (integer, 1–200,
  default 50), and optional trace fields.
- **API operation:** `GET /alarms` with `asset_id` or `site`, `page`, and
  `page_size`.
- **Validated output:** Pagination totals plus records containing
  `alarm_id`, `asset_id`, `alarm_name`, `severity`, `status`, and `start_time`.
- **Errors/timeouts:** Unknown asset returns 404; omitting both `asset_id` and
  `site`, or invalid page arguments, is rejected before the API request.
- **Example invocation:** `{"name":"get_alarms","arguments":{"asset_id":"A001","page":1,"page_size":50}}`
- **Site-wide example:** `{"name":"get_alarms","arguments":{"site":"EastRefinery","page":1,"page_size":200}}`
- **Example response shape:**

  ```json
  {"total":15,"page":1,"page_size":50,"total_pages":1,"data":[{"alarm_id":"ALM-A001-003","asset_id":"A001","asset_name":"Boiler Feed Pump 101","alarm_name":"Flow Rate Low","severity":"critical","status":"active","start_time":"<ISO-8601>","end_time":null,"count":4,"recurring":true}]}
  ```

## `get_alarm_by_id`

- **Purpose:** Retrieve one alarm record by its unique identifier.
- **Arguments:** `alarm_id` (string, 1–128 chars, required), optional trace
  fields.
- **API operation:** `GET /alarms/{alarm_id}`.
- **Validated output:** The alarm-list fields plus `asset_name`, `description`,
  `count`, and `recurring`.
- **Errors/timeouts:** Unknown alarm returns 404, surfaced as an MCP error.
- **Example invocation:** `{"name":"get_alarm_by_id","arguments":{"alarm_id":"ALM-A001-003"}}`
- **Example response:** Same record shape as an item in `get_alarms`.

## `alarm_summary`

- **Purpose:** Summarize alarms over a time range for one or more assets.
- **Arguments:** `asset_ids` (1–10 strings), `start_time` and `end_time`
  (ISO-8601 strings), optional trace fields.
- **API operation:** `POST /alarms/summary` with `asset_ids` and
  `time_range`.
- **Validated output:** `total_alarms`, `critical_count`, `high_count`, and
  `medium_count`; the API's other summary/KPI fields are retained.
- **Errors/timeouts:** Invalid timestamps/time ranges and unknown assets are
  surfaced as API errors through MCP.
- **Example invocation:**

  ```json
  {"name":"alarm_summary","arguments":{"asset_ids":["A001"],"start_time":"2026-01-01T00:00:00Z","end_time":"2026-03-01T00:00:00Z","trace_id":"investigation-123"}}
  ```

- **Example response shape:** `{"total_alarms":15,"critical_count":3,"high_count":3,"medium_count":9,"recurring_rate":0.2,"trace_id":"investigation-123"}`

## `alarm_correlation`

- **Purpose:** Analyze same-type alarm co-occurrence between selected assets.
- **Arguments:** Same asset/time-range arguments as `alarm_summary`, optional
  trace fields.
- **API operation:** `POST /alarms/correlation`; the connector requests
  `cooccurrence` with a 15-minute lag window.
- **Validated output:** `method`, `asset_pairs`, `root_cause_candidates`, and
  `confidence_score` (0–1). Candidates are synthetic alarm-name matches, not
  verified causal findings.
- **Errors/timeouts:** Invalid timestamps, unknown assets, and more than 10
  asset IDs are returned as API errors through MCP.
- **Example invocation:** `{"name":"alarm_correlation","arguments":{"asset_ids":["A001","A002"],"start_time":"2026-01-01T00:00:00Z","end_time":"2026-03-01T00:00:00Z"}}`
- **Example response shape:**

  ```json
  {"method":"cooccurrence","asset_pairs":[{"asset1":"A001","asset2":"A002","correlation":0.2,"support":3,"shared_alarm_types":["High Discharge Pressure"]}],"root_cause_candidates":["High Discharge Pressure"],"confidence_score":0.2}
  ```

## `priority_score`

- **Purpose:** Compute a deterministic demo priority from severity, recurrence,
  and occurrence count.
- **Arguments:** `alarm_id` (string, 1–128 chars, required), optional trace
  fields.
- **API operation:** `POST /alarms/priority-score`.
- **Validated output:** `alarm_id`, `score` (0–1), `reason`, and
  `recommendations`.
- **Errors/timeouts:** Unknown alarm returns 404.
- **Example invocation:** `{"name":"priority_score","arguments":{"alarm_id":"ALM-A001-003"}}`
- **Example response shape:** `{"alarm_id":"ALM-A001-003","score":0.99,"reason":"critical severity, recurring, count 4","recommendations":["Verify the alarm against current process values and approved limits","Follow the approved site response procedure"]}`

## `operator_recommendations`

- **Purpose:** Return generic simulator recommendations and related assets.
- **Arguments:** `alarm_id` (string, 1–128 chars, required), optional trace
  fields.
- **API operation:** `POST /recommendations/operator-actions` with
  `include_related`, `include_asset_context`, and `include_historical_pattern`
  enabled.
- **Validated output:** `alarm_id`, `immediate_actions`,
  `preventive_measures`, `related_assets`, and `documentation_references`.
- **Errors/timeouts:** Unknown alarm returns 404. The action text and reference
  labels are synthetic; check approved site procedures before action.
- **Example invocation:** `{"name":"operator_recommendations","arguments":{"alarm_id":"ALM-A001-003"}}`
- **Example response shape:**

  ```json
  {"alarm_id":"ALM-A001-003","immediate_actions":["Verify the Flow Rate Low indication against current instrument readings","Follow the approved site procedure and operating limits before taking action"],"preventive_measures":["Schedule preventive maintenance","Review operating procedures"],"related_assets":["A002","A003"],"documentation_references":["SOP-101","Maintenance-Manual-Ch3"]}
  ```

## Shared connector behavior

- **Authentication:** The MCP server sends a bearer token configured through
  `ALARM_API_TOKEN`; the simulator has a development-only default.
- **Trace metadata:** Optional tool arguments map to `trace_id`, `x-client-id`,
  and `x-metadata-tag` HTTP headers.
- **Timeout:** `httpx` timeout is 10 seconds per API attempt.
- **Retries:** Up to three attempts for connection errors, timeouts, and
  upstream 502/503/504 responses, with short exponential backoff. Other HTTP
  errors are not retried. There is no circuit breaker.
- **Error mapping:** The connector raises validation and HTTP errors; the MCP
  SDK returns an error result that the copilot records in its execution trace.
- **Secret handling:** The token is read from configuration and is not included
  in tool responses.

Run the independently testable tool layer with:

```powershell
python -m pytest tests\mcp\test_alarm_mcp.py -v
```
