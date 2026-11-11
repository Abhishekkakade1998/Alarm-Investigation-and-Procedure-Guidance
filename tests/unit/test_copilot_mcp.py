import asyncio
from contextlib import asynccontextmanager
import importlib
import json
from types import SimpleNamespace
import sys
import types
from unittest.mock import patch

import httpx

rag_stub = types.ModuleType("rag")
rag_stub.__path__ = []
ingestion_stub = types.ModuleType("rag.ingestion")
ingestion_stub.__path__ = []
ingester_stub = types.ModuleType("rag.ingestion.ingester")
ingester_stub.RAGRetriever = lambda _path: None
ingester_stub.DocumentIngester = object
with patch.dict(sys.modules, {
    "rag": rag_stub,
    "rag.ingestion": ingestion_stub,
    "rag.ingestion.ingester": ingester_stub,
}):
    copilot = importlib.import_module("apps.backend.copilot")


def test_find_nested_exception_extracts_upstream_error():
    upstream_error = httpx.ConnectError("Ollama is unavailable")
    group = ExceptionGroup(
        "MCP cleanup failed",
        [ExceptionGroup("client cleanup failed", [upstream_error])],
    )

    assert copilot._find_nested_exception(group, httpx.RequestError) is upstream_error


def test_ollama_chat_sends_messages_and_tools(monkeypatch):
    captured = {}
    original_client = httpx.AsyncClient

    def handle_request(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"message": {"content": "Answer"}})

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handle_request)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(copilot.httpx, "AsyncClient", mock_client)
    messages = [{"role": "user", "content": "Find a boiler asset"}]
    tools = [{"type": "function", "function": {"name": "search_assets"}}]

    response = asyncio.run(copilot.OllamaClient(
        "http://ollama:11434/",
        "llama3.1",
        {"num_ctx": 2048, "num_gpu": 0},
    ).chat(messages, tools))

    assert response["content"] == "Answer"
    assert captured["url"] == "http://ollama:11434/api/chat"
    body = json.loads(captured["body"])
    assert body["model"] == "llama3.1"
    assert body["stream"] is False
    assert body["messages"] == messages
    assert body["tools"] == tools
    assert body["options"] == {"num_ctx": 2048, "num_gpu": 0}
    assert body["keep_alive"] == copilot.OLLAMA_KEEP_ALIVE


def test_ollama_client_reuses_http_client(monkeypatch):
    instances = []

    class FakeClient:
        async def post(self, *_args, **_kwargs):
            return httpx.Response(
                200,
                json={"message": {"content": "Answer"}},
                request=httpx.Request("POST", "http://ollama:11434/api/chat"),
            )

        async def aclose(self):
            pass

    def fake_client(**_kwargs):
        client = FakeClient()
        instances.append(client)
        return client

    monkeypatch.setattr(copilot.httpx, "AsyncClient", fake_client)
    client = copilot.OllamaClient("http://ollama:11434", "llama3.2")

    asyncio.run(client.chat([]))
    asyncio.run(client.chat([]))

    assert len(instances) == 1


def test_mcp_registry_discovers_tools(monkeypatch):
    discovered_tool = SimpleNamespace(
        name="search_assets",
        description="Search assets",
        inputSchema={"type": "object"},
    )

    class FakeSession:
        def __init__(self, *_streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[discovered_tool])

    @asynccontextmanager
    async def fake_sse_client(*_args, **_kwargs):
        yield object(), object()

    monkeypatch.setattr(copilot, "sse_client", fake_sse_client)
    monkeypatch.setattr(copilot, "ClientSession", FakeSession)

    tools = asyncio.run(copilot.MCPToolRegistry("http://mcp:9000").discover_tools())

    assert tools == [discovered_tool]


def test_ollama_tool_call_invokes_discovered_mcp_tool():
    class FakeOllama:
        def __init__(self):
            self.responses = [
                {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "search_assets",
                            "arguments": {"query": "boiler"},
                        }
                    }],
                },
                {"content": "The boiler asset is A001."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        async def invoke_tool(self, _session, name, arguments):
            assert name == "search_assets"
            assert arguments == {"query": "boiler"}
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"asset_id": "A001"}')],
                isError=False,
            )

    orchestrator = copilot.CopilotOrchestrator(FakeOllama(), FakeRegistry(), None)
    tool_calls = []
    answer = asyncio.run(orchestrator._chat_with_tools(
        messages=[{"role": "user", "content": "Find the boiler"}],
        ollama_tools=[{"type": "function"}],
        available_tools={"search_assets": object()},
        session=object(),
        mcp_calls=tool_calls,
    ))

    assert answer == "The boiler asset is A001."
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "search_assets"
    assert tool_calls[0].result == '{"asset_id": "A001"}'


def test_get_alarms_resolves_asset_name_before_invocation():
    class FakeOllama:
        def __init__(self):
            self.responses = [
                {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "get_alarms",
                            "arguments": {
                                "asset_id": "Boiler Feed Pump 101",
                                "page": 1,
                            },
                        }
                    }],
                },
                {"content": "The pump has one active critical alarm."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        async def invoke_tool(self, _session, name, arguments):
            if name == "search_assets":
                assert arguments["query"] == "Boiler Feed Pump 101"
                return SimpleNamespace(
                    content=[SimpleNamespace(text=json.dumps({
                        "results": [{
                            "asset_id": "A001",
                            "asset_name": "Boiler Feed Pump 101",
                        }]
                    }))],
                    isError=False,
                )
            assert name == "get_alarms"
            assert arguments["asset_id"] == "A001"
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"data": []}')],
                isError=False,
            )

    orchestrator = copilot.CopilotOrchestrator(FakeOllama(), FakeRegistry(), None)
    calls = []
    answer = asyncio.run(orchestrator._chat_with_tools(
        messages=[{"role": "user", "content": "Show alarms for Boiler Feed Pump 101"}],
        ollama_tools=[{"type": "function"}],
        available_tools={"search_assets": object(), "get_alarms": object()},
        session=object(),
        mcp_calls=calls,
    ))

    assert answer == "The pump has one active critical alarm."
    assert [call.tool_name for call in calls] == ["search_assets", "get_alarms"]
    assert calls[1].arguments["asset_id"] == "A001"


def test_get_alarms_resolves_numbered_asset_alias_with_unique_type_match():
    class FakeOllama:
        def __init__(self):
            self.responses = [
                {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "get_alarms",
                            "arguments": {"asset_id": "Compressor 1"},
                        }
                    }],
                },
                {"content": "The Main Compressor has three recurring pressure alarms."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        async def invoke_tool(self, _session, name, arguments):
            if name == "search_assets":
                if arguments["query"] == "Compressor 1":
                    results = []
                else:
                    assert arguments["query"] == "Compressor"
                    results = [{
                        "asset_id": "A003",
                        "asset_name": "Main Compressor",
                    }]
                return SimpleNamespace(
                    content=[SimpleNamespace(text=json.dumps({"results": results}))],
                    isError=False,
                )
            assert name == "get_alarms"
            assert arguments["asset_id"] == "A003"
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({
                    "data": [{
                        "alarm_id": "ALM-A003-000",
                        "alarm_name": "High Discharge Pressure",
                        "count": 3,
                        "recurring": True,
                    }]
                }))],
                isError=False,
            )

    calls = []
    answer = asyncio.run(copilot.CopilotOrchestrator(
        FakeOllama(), FakeRegistry(), None
    )._chat_with_tools(
        messages=[{"role": "user", "content": "Why are compressor alarms recurring?"}],
        ollama_tools=[{"type": "function"}],
        available_tools={"search_assets": object(), "get_alarms": object()},
        session=object(),
        mcp_calls=calls,
    ))

    assert "Main Compressor" in answer
    assert [call.tool_name for call in calls] == [
        "search_assets",
        "search_assets",
        "get_alarms",
    ]
    assert calls[-1].arguments["asset_id"] == "A003"


def test_repeated_mcp_tool_call_uses_cached_result():
    class FakeOllama:
        def __init__(self):
            self.responses = [
                {"content": "", "tool_calls": [{
                    "function": {
                        "name": "get_alarms",
                        "arguments": {"asset_id": "A001"},
                    }
                }]},
                {"content": "", "tool_calls": [{
                    "function": {
                        "name": "get_alarms",
                        "arguments": {"asset_id": "A001"},
                    }
                }]},
                {"content": "There is one active alarm."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        def __init__(self):
            self.invocation_count = 0

        async def invoke_tool(self, _session, _name, _arguments):
            self.invocation_count += 1
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"data": [{"alarm_id": "ALM-1"}]}')],
                isError=False,
            )

    registry = FakeRegistry()
    calls = []
    answer = asyncio.run(copilot.CopilotOrchestrator(
        FakeOllama(), registry, None
    )._chat_with_tools(
        messages=[{"role": "user", "content": "Show alarms"}],
        ollama_tools=[{"type": "function"}],
        available_tools={"get_alarms": object()},
        session=object(),
        mcp_calls=calls,
    ))

    assert answer == "There is one active alarm."
    assert registry.invocation_count == 1
    assert len(calls) == 2
    assert calls[1].cached is True
    assert calls[1].duration_ms == 0


def test_repeated_failed_mcp_tool_call_is_not_reinvoked():
    class FakeOllama:
        def __init__(self):
            failed_call = {
                "function": {
                    "name": "priority_score",
                    "arguments": {"alarm_id": "ALM-A001-000"},
                }
            }
            self.responses = [
                {"content": "", "tool_calls": [failed_call]},
                {"content": "", "tool_calls": [failed_call]},
                {"content": "The priority lookup failed."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        def __init__(self):
            self.invocation_count = 0

        async def invoke_tool(self, _session, _name, _arguments):
            self.invocation_count += 1
            return SimpleNamespace(
                content=[SimpleNamespace(text="Alarm not found")],
                isError=True,
            )

    registry = FakeRegistry()
    calls = []
    answer = asyncio.run(copilot.CopilotOrchestrator(
        FakeOllama(), registry, None
    )._chat_with_tools(
        messages=[{"role": "user", "content": "Find the highest priority alarm"}],
        ollama_tools=[{"type": "function"}],
        available_tools={"priority_score": object()},
        session=object(),
        mcp_calls=calls,
    ))

    assert answer == "The priority lookup failed."
    assert registry.invocation_count == 1
    assert len(calls) == 2
    assert calls[1].cached is True
    assert calls[1].error == "Alarm not found"


def test_independent_mcp_tool_calls_run_concurrently():
    class FakeOllama:
        def __init__(self):
            self.responses = [
                {"content": "", "tool_calls": [
                    {
                        "function": {
                            "name": "get_alarm_by_id",
                            "arguments": {"alarm_id": "ALM-A001-003"},
                        }
                    },
                    {
                        "function": {
                            "name": "operator_recommendations",
                            "arguments": {"alarm_id": "ALM-A001-003"},
                        }
                    },
                ]},
                {"content": "The alarm detail and recommendations are available."},
            ]

        async def chat(self, _messages, tools=None):
            assert tools is not None
            return self.responses.pop(0)

    class FakeRegistry:
        def __init__(self):
            self.active_calls = 0
            self.max_active_calls = 0
            self.both_started = asyncio.Event()

        async def invoke_tool(self, _session, name, _arguments):
            self.active_calls += 1
            self.max_active_calls = max(
                self.max_active_calls, self.active_calls
            )
            if self.active_calls == 2:
                self.both_started.set()
            try:
                await asyncio.wait_for(self.both_started.wait(), timeout=1)
            finally:
                self.active_calls -= 1
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({"tool": name}))],
                isError=False,
            )

    registry = FakeRegistry()
    calls = []
    answer = asyncio.run(copilot.CopilotOrchestrator(
        FakeOllama(), registry, None
    )._chat_with_tools(
        messages=[{"role": "user", "content": "Investigate alarm ALM-A001-003"}],
        ollama_tools=[{"type": "function"}],
        available_tools={
            "get_alarm_by_id": object(),
            "operator_recommendations": object(),
        },
        session=object(),
        mcp_calls=calls,
    ))

    assert answer == "The alarm detail and recommendations are available."
    assert registry.max_active_calls == 2
    assert [call.tool_name for call in calls] == [
        "get_alarm_by_id", "operator_recommendations"
    ]
