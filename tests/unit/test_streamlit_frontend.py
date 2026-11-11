import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests


frontend = importlib.import_module("apps.frontend.app")


def test_run_investigation_sends_options_and_returns_result(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            json=lambda: {"answer": "Check pump pressure.", "mcp_calls": []},
        )

    monkeypatch.setattr(frontend.requests, "post", fake_post)

    result = frontend.run_investigation("Investigate pump", use_rag=False, use_mcp=True)

    assert captured["url"] == f"{frontend.API_BASE_URL}/investigate"
    assert captured["json"] == {
        "query": "Investigate pump",
        "use_rag": False,
        "use_mcp": True,
    }
    assert captured["timeout"] == frontend.REQUEST_TIMEOUT
    assert result["answer"] == "Check pump pressure."


def test_run_investigation_includes_supplied_conversation_context(monkeypatch):
    captured = {}
    context = {"history": [{"role": "user", "content": "Check pump A001"}]}

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            json=lambda: {"answer": "Pump A001 has no verified active alarm."},
        )

    monkeypatch.setattr(frontend.requests, "post", fake_post)

    frontend.run_investigation(
        "What about its alarms?",
        use_rag=True,
        use_mcp=False,
        context=context,
    )

    assert captured["json"]["context"] == context


def test_conversation_context_keeps_only_recent_six_messages():
    messages = [
        {"role": "user", "content": f"Question {index}"}
        for index in range(8)
    ]

    context = frontend._conversation_context(messages)

    assert context["history"] == [
        {"role": "user", "content": f"Question {index}"}
        for index in range(2, 8)
    ]


def test_investigation_timeout_allows_slow_local_model():
    assert frontend.REQUEST_TIMEOUT == (5, 600)


def test_render_result_does_not_nest_expanders(monkeypatch):
    state = {"depth": 0}

    @contextmanager
    def fake_expander(*_args, **_kwargs):
        assert state["depth"] == 0
        state["depth"] += 1
        try:
            yield
        finally:
            state["depth"] -= 1

    class FakeColumn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def metric(self, *_args, **_kwargs):
            pass

    for name in ("markdown", "subheader", "info", "warning", "caption", "json", "write", "code"):
        monkeypatch.setattr(frontend.st, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frontend.st, "expander", fake_expander)
    monkeypatch.setattr(
        frontend.st,
        "columns",
        lambda count: [FakeColumn() for _ in range(count)],
    )

    frontend.render_result({
        "answer": "Alarm summary",
        "rag_sources": [{
            "source": "pump.txt",
            "content": "Check pump pressure.",
            "score": 0.9,
            "metadata": {},
        }],
        "mcp_calls": [{
            "tool_name": "get_alarms",
            "arguments": {"asset_id": "A001"},
            "result": '{"data": []}',
            "duration_ms": 10,
        }],
    })

    assert state["depth"] == 0


def test_run_investigation_reports_backend_error(monkeypatch):
    response = SimpleNamespace(
        ok=False,
        status_code=503,
        text="MCP unavailable",
        json=lambda: {"detail": "MCP server is unavailable"},
    )
    monkeypatch.setattr(frontend.requests, "post", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="503: MCP server is unavailable"):
        frontend.run_investigation("Investigate pump", use_rag=True, use_mcp=True)


def test_run_investigation_rejects_malformed_response(monkeypatch):
    response = SimpleNamespace(ok=True, json=lambda: {"answer": None})
    monkeypatch.setattr(frontend.requests, "post", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="invalid investigation response"):
        frontend.run_investigation("Investigate pump", use_rag=True, use_mcp=True)


def test_run_investigation_surfaces_connection_errors(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise requests.ConnectionError("backend is offline")

    monkeypatch.setattr(frontend.requests, "post", fail_post)

    with pytest.raises(requests.ConnectionError, match="backend is offline"):
        frontend.run_investigation("Investigate pump", use_rag=True, use_mcp=True)
