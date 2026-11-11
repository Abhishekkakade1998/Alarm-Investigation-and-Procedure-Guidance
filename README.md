# Alarm Investigation and Procedure Guidance Copilot

A local development and assignment prototype that combines synthetic alarm data,
MCP tools, local document retrieval, and an Ollama language model. It is not a
live plant monitoring system, an approved operating procedure, or production
industrial-control software.

## What it does

- Accepts natural-language alarm investigation questions through a Streamlit UI
  or the FastAPI backend.
- Uses the alarm-management MCP server to discover and invoke typed tools.
- Retrieves evidence from the bundled procedure and motor-maintenance documents
  using sentence-transformer embeddings and a persistent local Qdrant index.
- Returns the response, retrieved passages, confidence estimate, MCP request
  and result trace, and per-stage timing information.
- Supports follow-up turns by passing a bounded conversation history from the
  UI to the backend.

The bundled alarm API is a **synthetic simulator**. Its results are examples
for development and testing only; they do not represent a real refinery, ABB
equipment, or live alarms. Generated recommendations and documents are not
validated for operational use. Always defer to approved site procedures,
limits, and operator judgment.

## Architecture

```text
Browser
  └── Streamlit UI (:3000)
        └── FastAPI Copilot (:8001)
              ├── Ollama chat API (:11434)
              ├── Local Qdrant persistence (rag/qdrant_data)
              └── MCP SSE client (:9000)
                    └── Alarm-management MCP server
                          └── Synthetic Alarm API (:8000)
```

The copilot retrieves documents and dynamically discovers MCP tool schemas
before asking the model to select tools. It does not call the alarm simulator
directly. The MCP server validates tool arguments and selected API responses,
then forwards requests to the simulator.

## Prerequisites

- Windows 10/11, macOS, or Linux.
- Python 3.11 or newer. The local project environment has been tested with
  Python 3.13.
- Ollama installed and running on the host.
- The model used by default: `qwen2.5:1.5b`.
- Docker Desktop with Docker Compose only if using the containerized setup.
- Internet access for the first download of the model and embedding model.

On Windows, Ollama often runs as a background application/service. If the
Ollama API is already available on port `11434`, do not start a second
`ollama serve`; it will fail because the port is already in use.

## Quick start: Docker Compose

From the project root:

1. Copy the example environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Make sure Ollama is running on the host and download the model:

   ```powershell
   ollama pull qwen2.5:1.5b
   ollama list
   ```

3. Build and start the application:

   ```powershell
   docker compose up --build
   ```

   The backend image ingests the bundled documents into Qdrant before starting
   the API. The compose configuration connects the container to host Ollama at
   `http://host.docker.internal:11434`.

4. Open the UI at <http://localhost:3000>.

Service URLs:

| Service | URL |
|---|---|
| Streamlit frontend | <http://localhost:3000> |
| Copilot API | <http://localhost:8001> |
| Copilot health | <http://localhost:8001/health> |
| MCP tool listing | <http://localhost:8001/mcp/tools> |
| RAG status | <http://localhost:8001/rag/status> |
| MCP health | <http://localhost:9000/health> |
| Alarm simulator health | <http://localhost:8000/health> |

Stop the containers with `Ctrl+C` if running in the foreground, or run:

```powershell
docker compose down
```

Docker Compose reads `.env` automatically. Set `OLLAMA_MODEL` there if you
want a model other than the default. Keep `ALARM_API_TOKEN` identical for the
simulator and MCP server; change the demo token before exposing services to a
network.

## Local development: Windows PowerShell

Run Ollama on the host and pull the model first:

```powershell
ollama pull qwen2.5:1.5b
```

From the repository root, create and activate a virtual environment and install
all three services' dependencies:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r apps\backend\requirements.txt
python -m pip install -r mcp-servers\alarm-management\requirements.txt
python -m pip install -r apps\frontend\requirements.txt
```

Start each service in its own PowerShell window, with the working directory
set to the repository root.

**Terminal 1 — alarm simulator**

```powershell
$env:ALARM_API_TOKEN = "demo-token"
python -m uvicorn apps.backend.simulator:app --host 0.0.0.0 --port 8000
```

**Terminal 2 — MCP server**

```powershell
$env:ALARM_API_BASE_URL = "http://localhost:8000"
$env:ALARM_API_TOKEN = "demo-token"
python mcp-servers\alarm-management\server.py
```

**Terminal 3 — ingest documents, then start the copilot backend**

Ingestion uses the same local Qdrant directory that the backend opens. Run it
before starting the backend, not concurrently with it.

```powershell
$env:VECTOR_STORE_PATH = ".\rag\qdrant_data"
$env:DOCUMENT_PATH = ".\rag\documents"
python -m rag.ingestion.ingester

$env:OLLAMA_BASE_URL = "http://localhost:11434"
$env:OLLAMA_MODEL = "qwen2.5:1.5b"
$env:MCP_SERVER_URL = "http://localhost:9000"
python -m uvicorn apps.backend.copilot:app --host 0.0.0.0 --port 8001
```

**Terminal 4 — Streamlit frontend**

```powershell
$env:COPILOT_API_URL = "http://localhost:8001"
python -m streamlit run apps\frontend\app.py --server.port 3000
```

Open <http://localhost:3000>. Environment variables set with `$env:` apply only
to that PowerShell process and its child process; set the necessary values in
each terminal. The local Python entry points do not automatically load `.env`.

## Using the application

The chat UI provides toggles for MCP data and document retrieval. Responses
include:

- The generated answer and an evidence-confidence indicator.
- Successfully retrieved procedure passages and their source names.
- An expandable trace of MCP calls, including arguments, result or error, and
  duration.
- Timing for RAG, MCP connection/discovery, MCP tools, Ollama calls, and total
  investigation time.

Example questions aligned with the assignment:

1. Show active critical alarms for Boiler Feed Pump 102 and recommend immediate
   actions.
2. Why are compressor discharge pressure alarms repeatedly occurring?
3. Which alarm has the highest priority in EastRefinery, and why?
4. What related assets should be inspected for this motor trip alarm?
5. Which operating procedure applies to this alarm?
6. Are the API recommendations consistent with the maintenance manual?

Questions such as “this alarm” depend on prior chat context. The UI sends only
a bounded recent history; conversations are not stored as server-side sessions.
The model may choose a different tool sequence or answer than the deterministic
test fixtures. Inspect the returned evidence and tool trace, and do not treat
generated output as an operational instruction.

## API reference

### Copilot backend (`:8001`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Backend health check |
| `POST` | `/investigate` | Run an investigation |
| `GET` | `/mcp/tools` | Discover and list MCP tools |
| `GET` | `/rag/status` | Run a retrieval check and report index status |
| `GET` | `/examples` | Return built-in example scenarios |

Example request:

```powershell
$body = @{
  query = "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions"
  use_rag = $true
  use_mcp = $true
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8001/investigate" `
  -ContentType "application/json" `
  -Body $body
```

The investigation response includes `answer`, `mcp_calls`, `rag_sources`,
`confidence`, and `performance`. A failed required MCP call is explicitly
reported rather than treated as verified alarm data.

### Alarm simulator (`:8000`)

Protected API operations require `Authorization: Bearer <ALARM_API_TOKEN>`.
`/health` is public. The simulator currently provides:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/assets/search` | Search assets; optional site and unit filters |
| `GET` | `/assets/{asset_id}/metadata` | Retrieve asset metadata and related asset IDs |
| `GET` | `/alarms` | Filter and page alarms by asset, site, unit, status, and time |
| `GET` | `/alarms/{alarm_id}` | Retrieve a specific alarm |
| `POST` | `/alarms/summary` | Summarize selected alarms |
| `POST` | `/alarms/trends` | Aggregate alarm metrics into time buckets |
| `POST` | `/alarms/correlation` | Analyze same-type alarm co-occurrence across assets |
| `POST` | `/alarms/flood-analysis` | Analyze alarms in rolling windows |
| `POST` | `/alarms/rationalization-candidates` | Find recurring alarm candidates |
| `POST` | `/alarms/priority-score` | Calculate the simulator's priority score |
| `POST` | `/recommendations/operator-actions` | Return generic simulator recommendations |
| `POST` | `/calculation-code/generate` | Create an in-memory predefined calculation job |
| `POST` | `/calculation-code/execute` | Execute a stored predefined calculation |
| `GET` | `/analytics/kpi-definitions` | List supported KPI definitions |

The `GET /alarms` site filter is also available through MCP's `get_alarms`
tool. Provide either `asset_id` or `site`; for example, use
`{"site":"EastRefinery","page_size":200}` to retrieve the simulator's site-wide
records.

## Postman collections

The `postman/` directory contains the supplied Postman collections for
exercising the alarm simulator API:

- `Alarm-API-Simulator.postman_collection.json` — individual API requests.
- `chaining/Alarm-API-Chaining.postman_collection.json` — multi-step API
  chaining scenarios.
- `scenarios/Alarm-API-Scenarios.postman_collection.json` — scenario requests.

Import a collection into Postman, start the alarm simulator, and use the
collection's `baseUrl` and `auth_token` variables. The default local values are
`http://localhost:8000` and `demo-token`. These collections test the HTTP
simulator API; they do not call the MCP server or the copilot UI.

### MCP tools (`:9000`)

The MCP server exposes eight dynamically discovered tools:

| Tool | Purpose |
|---|---|
| `search_assets` | Resolve an asset name or keyword to simulator asset IDs |
| `get_asset_metadata` | Retrieve asset properties and related asset IDs |
| `get_alarms` | Retrieve alarms for an asset or all assets at a site |
| `get_alarm_by_id` | Retrieve one alarm by its ID |
| `alarm_summary` | Summarize alarms over an asset/time range |
| `alarm_correlation` | Compare alarm co-occurrence for multiple assets |
| `priority_score` | Retrieve a deterministic simulator priority score |
| `operator_recommendations` | Retrieve generic simulator recommendations |

The server uses the MCP SDK's SSE transport at `/sse`; its health endpoint is
`/health`. Detailed request schemas and examples are in
[docs/mcp-tool-catalog.md](docs/mcp-tool-catalog.md). Several simulator
analytics endpoints are available through HTTP but are not exposed as MCP
tools.

## Documents and Qdrant

Bundled documents:

- [`rag/documents/pump_operating_procedure.txt`](rag/documents/pump_operating_procedure.txt)
- [`rag/documents/motor_troubleshooting_guide.txt`](rag/documents/motor_troubleshooting_guide.txt)

The ingester splits text into overlapping word chunks, embeds them with
`sentence-transformers/all-MiniLM-L6-v2`, and stores vectors and metadata in
the Qdrant local collection `alarm_procedures`. No separate Qdrant server is
needed. Qdrant's local database is stored at `rag/qdrant_data` by default; the
database files are generated locally and excluded from Git.

To re-ingest documents locally, stop the backend first and run from the project
root:

```powershell
python -m rag.ingestion.ingester
```

The ingester upserts document chunks. It does not remove old chunks when a
document changes; see [docs/known-limitations.md](docs/known-limitations.md)
before rebuilding or replacing an index. Do not have the ingester and backend
open the same Qdrant local path simultaneously.

## Configuration

Docker Compose reads `.env` automatically. Local Python processes use their
environment variables; they do not automatically load `.env`.

| Variable | Purpose | Default |
|---|---|---|
| `ALARM_API_BASE_URL` | MCP server's simulator base URL | `http://alarm-simulator:8000` in Compose; `http://localhost:8000` for local MCP |
| `ALARM_API_TOKEN` | Shared simulator/MCP bearer token | `demo-token` |
| `MCP_SERVER_URL` | Backend's MCP server URL | `http://alarm-mcp:9000` in Compose; `http://localhost:9000` locally |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://host.docker.internal:11434` in Compose; `http://localhost:11434` locally |
| `OLLAMA_MODEL` | Model available in Ollama | `qwen2.5:1.5b` |
| `OLLAMA_KEEP_ALIVE` | Keep model loaded between requests | `10m` |
| `OLLAMA_NUM_CTX` | Ollama context size | `2048` |
| `OLLAMA_NUM_GPU` | Number of model layers to offload to GPU; `0` forces CPU | `0` |
| `OLLAMA_NUM_PREDICT` | Maximum generated tokens per response | `512` |
| `MAX_MCP_TOOL_ROUNDS` | Maximum tool-selection rounds | `3` |
| `OLLAMA_REQUEST_TIMEOUT` | Maximum seconds for one Ollama request | `300` |
| `VECTOR_STORE_PATH` | Qdrant local database path | `./rag/qdrant_data` |
| `DOCUMENT_PATH` | Document ingestion directory | `./rag/documents` |
| `COPILOT_API_URL` | Frontend's backend base URL | `http://localhost:8001` |

The frontend's investigation request timeout is currently set in
`apps/frontend/app.py` (connect timeout 5 seconds; read timeout 600 seconds).
The Ollama timeout is a maximum wait, not a latency target. Lowering
`OLLAMA_NUM_PREDICT` may shorten answers; GPU offload only helps when supported
and configured correctly. Measure the displayed timing breakdown before
tuning.

## Tests

From the repository root, with the project's Python dependencies installed:

```powershell
python -m pytest tests\unit tests\integration tests\mcp -q
```

These unit, deterministic integration, and MCP tests do not require Docker,
Ollama, or external services. The integration suite exercises the simulator
and actual MCP tool handler in-process using a deterministic model fixture.
It covers the six assignment example workflows, but does not establish that a
live model will choose identical tools or produce identical wording.

The `tests/e2e` directory contains service-level tests. In the current test
environment, they fail during fixture setup because async fixtures are
provided as `async_generator` objects rather than initialized HTTP clients;
they are not part of the verified passing command above. CI runs only the
unit, integration, and MCP suites. Coverage notes are in
[docs/coverage-report.md](docs/coverage-report.md).

## Troubleshooting

### Ollama connection refused

Check Ollama is running and accessible:

```powershell
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
```

If Ollama is already listening on port `11434`, do not run another
`ollama serve`. If using Docker Compose, ensure `OLLAMA_BASE_URL` points to the
host Ollama server, normally `http://host.docker.internal:11434`.

### Model not found

```powershell
ollama pull qwen2.5:1.5b
ollama list
```

Verify that the backend's `OLLAMA_MODEL` exactly matches an installed model
tag, and restart the backend after configuration changes.

### MCP tools fail

Check simulator and MCP health endpoints, confirm `ALARM_API_TOKEN` matches
between the two services, and inspect the failed tool's arguments and error in
the UI trace:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:9000/health
Invoke-RestMethod http://localhost:8001/mcp/tools
```

For asset-specific queries, resolve the simulator's asset name to its ID. For
site-wide alarm questions, use the `site` filter instead of guessing an asset
ID.

### RAG status is empty or documents are not retrieved

Check <http://localhost:8001/rag/status>, confirm the procedure text files
exist under `rag/documents`, and re-ingest with the backend stopped:

```powershell
python -m rag.ingestion.ingester
```

The app uses `rag/qdrant_data` by default. When running in Docker, check that
the Qdrant data volume and document mount are present.

### Slow investigations

Check the `Latency` breakdown in the result. Ollama generation/planning is
often much slower than retrieval or individual MCP requests, especially on
CPU. The smaller `qwen2.5:1.5b` model is configured by default, but actual
latency varies with hardware, model loading, output length, and number of tool
rounds. Keep the model warm with `OLLAMA_KEEP_ALIVE`; avoid raising the tool
round limit unless the workflow needs it.

## Limitations and security

- All included alarm records are generated synthetic data; recurrence patterns
  are fixture behavior, not real trends.
- Bundled procedures are examples. Simulator recommendation document labels
  are not verified citations to approved site documents.
- Model output may be incomplete or wrong. It is not an operating instruction.
- The simulator uses a development bearer token by default. The copilot API
  and MCP SSE endpoint do not authenticate individual users or enforce
  per-tool authorization.
- TLS, enterprise identity, rate limiting, centralized secret management,
  distributed tracing, and production-grade audit retention are not configured.
- Qdrant local storage is intended for a single process, not concurrent
  multi-replica production use.
- Local chat history is bounded and transient; there is no server-side
  conversation store.
- The full multi-container/browser/Ollama stack has not been validated as an
  end-to-end production deployment.

Read [docs/known-limitations.md](docs/known-limitations.md),
[docs/design-decisions.md](docs/design-decisions.md), and
[docs/architecture.md](docs/architecture.md) for additional detail.

## Project layout

```text
apps/
  backend/
    copilot.py                 # FastAPI orchestration and Ollama client
    simulator.py               # Synthetic Alarm Management API
    requirements.txt
  frontend/
    app.py                     # Streamlit chat and investigation trace
    requirements.txt
mcp-servers/
  alarm-management/
    server.py                  # Typed MCP tools and simulator connector
    requirements.txt
rag/
  documents/                   # Bundled sample procedures and guides
  ingestion/
    ingester.py                # Chunking, embedding, Qdrant ingestion/retrieval
  qdrant_data/                 # Local Qdrant persistence (generated)
postman/
  *.postman_collection.json    # Alarm API test collection
  chaining/                    # Multi-step API chaining collection
  scenarios/                   # API scenario collection
tests/
  unit/
  integration/
  mcp/
  e2e/
docs/                           # Architecture, API, RAG, limitations, reports
Dockerfile.copilot
Dockerfile.mcp
Dockerfile.simulator
docker-compose.yml
.env.example
```

## Assignment and status

**Use case:** Alarm Investigation and Procedure Guidance Copilot.

The project is a development prototype. Demo screenshots/video are not
included. Review the current [known limitations](docs/known-limitations.md)
before presenting or extending it.
