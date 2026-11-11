# Design Decisions

## One integrated vertical slice

The primary path is a single investigation request that retrieves procedures
from RAG and calls alarm-system operations through the MCP server. This keeps
structured alarm facts and unstructured procedure evidence in one answer and
one trace, instead of presenting unrelated MCP and RAG demonstrations.

## MCP as the alarm-system boundary

The backend discovers MCP tools at runtime and invokes them using the returned
schemas. The MCP server owns alarm API authentication, HTTP connection reuse,
timeouts, retries, and request/response validation. The copilot backend does
not bypass MCP to access the simulator. The simulator contains more endpoints
than are currently exposed as tools; the tool surface is limited to the
operations used by the main investigation workflow.

## Local-first dependencies

Qdrant local mode avoids requiring a separate vector-database service for the
development setup. It persists under `rag/qdrant_data`, but should be opened by
only one process at a time. Sentence Transformers and Ollama run locally; this
avoids sending procedure text or prompts to a hosted model in the default
configuration. Local inference trades deployment simplicity for latency and
hardware dependence.

## Bounded work and visible latency

The backend measures RAG, MCP connection/discovery, tool calls, Ollama calls,
and total request time. It uses a bounded number of tool rounds, limits
generated output, reuses HTTP connections, and moves blocking retrieval work to
a worker thread. These are latency safeguards, not latency guarantees; model
size, hardware, and tool-planning behavior still affect response time.

## Evidence and failure behavior

Retrieved passages and conversation history are explicitly marked untrusted.
They provide evidence but cannot override system instructions. When no
relevant passages are retrieved, the answer must not present general model
knowledge as approved site procedure. When MCP data needed to verify live alarm
state fails, the answer says it cannot confirm the state rather than
fabricating results. The GUI retains the per-tool trace and source citations so
operators can inspect the basis for the answer.

## Simulator and operational safety

The API simulator uses deterministic synthetic alarm patterns and performs
bounded calculations rather than executing generated code. API-generated
actions are generic demo content and document references are sample labels;
they are not an approved operating procedure. The copilot must direct users to
site-approved limits and procedures for safety-critical decisions.

## Transport and deployment scope

The MCP server uses the SDK's SSE transport for the current client. Compose
provides a repeatable multi-service option, while local process startup remains
available when Docker is not installed. The demo bearer token is a local
development default and is configurable through the environment; no
enterprise identity provider, user authorization, TLS termination, or
production secret manager is included.
