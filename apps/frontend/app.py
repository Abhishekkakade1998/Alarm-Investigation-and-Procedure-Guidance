import json
import os
from typing import Any

import requests
import streamlit as st


API_BASE_URL = os.getenv("COPILOT_API_URL", "http://localhost:8001").rstrip("/")
REQUEST_TIMEOUT = (5, 600)
DEFAULT_EXAMPLES = [
    "Show active critical alarms for Boiler Feed Pump 101 and recommend immediate actions",
    "Why are compressor discharge pressure alarms repeatedly occurring?",
    "Which alarm has the highest priority in EastRefinery?",
    "What procedure applies to a vibration alarm on a motor?",
]

st.set_page_config(
    page_title="Alarm Investigation Copilot",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)


def api_get(path: str, timeout: tuple[int, int] = (3, 5)) -> Any:
    response = requests.get(f"{API_BASE_URL}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def run_investigation(
    query: str,
    use_rag: bool,
    use_mcp: bool,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_body = {"query": query, "use_rag": use_rag, "use_mcp": use_mcp}
    if context is not None:
        request_body["context"] = context
    response = requests.post(
        f"{API_BASE_URL}/investigate",
        json=request_body,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        try:
            error_body = response.json()
            detail = (
                error_body.get("detail", response.text)
                if isinstance(error_body, dict)
                else response.text
            )
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Backend returned HTTP {response.status_code}: {detail}")

    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
        raise RuntimeError("Backend returned an invalid investigation response.")
    return result


def render_alarm_summary(calls: list[dict[str, Any]]) -> None:
    st.subheader("Alarm and asset summary")
    successful_calls = [
        call for call in calls if not call.get("error") and call.get("result")
    ]
    if not successful_calls:
        st.info("No successful MCP data was returned for this investigation.")
        return

    for index, call in enumerate(successful_calls):
        name = call.get("tool_name") or f"Tool call {index + 1}"
        with st.expander(name.replace("_", " ").title()):
            result = call["result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    pass
            if isinstance(result, (dict, list)):
                st.json(result, expanded=True)
            else:
                st.write(result)


def render_source(source: dict[str, Any], index: int) -> None:
    name = source.get("source") or f"Source {index + 1}"
    score = source.get("score")
    title = f"{name} · {score:.0%} match" if isinstance(score, (int, float)) else name
    with st.expander(title):
        st.markdown(source.get("content") or "_No passage content returned._")
        metadata = source.get("metadata")
        if isinstance(metadata, dict) and metadata:
            st.caption("Metadata")
            st.json(metadata, expanded=False)


def render_tool_call(call: dict[str, Any], index: int) -> None:
    name = call.get("tool_name") or f"Tool call {index + 1}"
    failed = bool(call.get("error"))
    status = "❌ Failed" if failed else "✅ Complete"
    duration = call.get("duration_ms")
    label = f"{status} · `{name}`"
    if call.get("cached"):
        label = f"⚡ Cached · `{name}`"
    if isinstance(duration, (int, float)):
        label += f" · {duration} ms"

    with st.expander(label):
        left, right = st.columns(2)
        with left:
            st.caption("Request arguments")
            st.json(call.get("arguments") or {}, expanded=False)
        with right:
            st.caption("Error" if failed else "Response")
            value = call.get("error") if failed else call.get("result")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            if isinstance(value, (dict, list)):
                st.json(value, expanded=False)
            else:
                st.code(value or "No response content", language="text")


def render_result(result: dict[str, Any]) -> None:
    answer = result.get("answer", "")
    st.markdown(answer)

    sources = result.get("rag_sources") or []
    calls = result.get("mcp_calls") or []
    confidence = result.get("confidence")
    metric_cols = st.columns(3)
    metric_cols[0].metric("Procedure sources", len(sources))
    metric_cols[1].metric("MCP tool calls", len(calls))
    metric_cols[2].metric(
        "Evidence confidence",
        f"{confidence:.0%}" if isinstance(confidence, (float, int)) else "N/A",
    )
    performance = result.get("performance") or {}
    if isinstance(performance, dict):
        ollama_times = performance.get("ollama_calls_ms") or []
        ollama_total_ms = sum(
            value for value in ollama_times if isinstance(value, (int, float))
        )
        st.caption(
            "Latency — "
            f"total {performance.get('total_ms', 'N/A')} ms · "
            f"model {ollama_total_ms} ms across {len(ollama_times)} call(s) · "
            f"MCP tool-time sum {performance.get('mcp_tools_ms', 'N/A')} ms · "
            f"RAG {performance.get('rag_ms', 'N/A')} ms · "
            f"MCP connect/discovery "
            f"{performance.get('mcp_connect_ms', 'N/A')}/"
            f"{performance.get('mcp_discovery_ms', 'N/A')} ms"
        )

    render_alarm_summary(
        [call for call in calls if isinstance(call, dict)]
    )

    st.subheader(f"Procedure evidence ({len(sources)})")
    if sources:
        for index, source in enumerate(sources):
            if isinstance(source, dict):
                render_source(source, index)
    else:
        st.info("No procedure passages were retrieved for this investigation.")

    st.subheader(f"MCP execution trace ({len(calls)})")
    if calls:
        for index, call in enumerate(calls):
            if isinstance(call, dict):
                render_tool_call(call, index)
    else:
        st.info("No MCP calls were requested for this investigation.")

    investigation_id = result.get("investigation_id")
    if investigation_id:
        st.caption(f"Investigation ID: `{investigation_id}`")
    st.warning(
        "AI-generated guidance can be incomplete. Verify safety-critical actions "
        "against approved site procedures and operating limits."
    )


def queue_prompt(prompt: str) -> None:
    st.session_state["queued_prompt"] = prompt


def _conversation_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    history = []
    for item in messages[-12:]:
        if item.get("role") == "user" and isinstance(item.get("content"), str):
            history.append({"role": "user", "content": item["content"]})
        elif item.get("role") == "assistant" and isinstance(item.get("result"), dict):
            answer = item["result"].get("answer")
            if isinstance(answer, str):
                history.append({"role": "assistant", "content": answer})
    return {"history": history[-6:]}


def main() -> None:
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    with st.sidebar:
        st.title("🚨 Alarm Copilot")
        st.caption("Operations intelligence · Local Ollama")
        st.divider()

        try:
            health = api_get("/health")
            if health.get("status") == "ok":
                st.success("Copilot backend connected")
            else:
                st.warning("Copilot backend reported an unhealthy status")
        except (requests.RequestException, ValueError, AttributeError) as error:
            st.error(f"Backend unavailable: {error}")
            st.caption(f"API endpoint: {API_BASE_URL}")

        try:
            tool_data = api_get("/mcp/tools")
            st.caption(f"{tool_data.get('total', 0)} MCP tools discovered")
        except (requests.RequestException, ValueError, AttributeError) as error:
            st.warning(f"MCP tools unavailable: {error}")

        st.divider()
        st.subheader("Investigation sources")
        use_mcp = st.checkbox("Live alarm data (MCP)", value=True)
        use_rag = st.checkbox("Operating procedures (RAG)", value=True)
        st.caption("Disable either source to run a focused investigation.")

        st.divider()
        st.subheader("Try an investigation")
        try:
            example_data = api_get("/examples")
            examples = [
                scenario["query"]
                for scenario in example_data.get("scenarios", [])
                if isinstance(scenario, dict) and scenario.get("query")
            ]
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            examples = DEFAULT_EXAMPLES
        for example in examples:
            st.button(
                example,
                key=f"example-{example}",
                use_container_width=True,
                on_click=queue_prompt,
                args=(example,),
            )

        if st.button("Clear conversation", use_container_width=True):
            st.session_state["messages"] = []
            st.session_state.pop("queued_prompt", None)
            st.rerun()

    st.title("Alarm investigation workspace")
    st.caption(
        "Investigate alarms using live system data and approved procedures. "
        "Results include source citations and the MCP execution trace."
    )

    if not st.session_state["messages"]:
        with st.container(border=True):
            st.subheader("What would you like to investigate?")
            st.write(
                "Ask about an alarm, asset, or recurring pattern. The copilot can "
                "retrieve current alarm context and relevant operating procedures."
            )
            st.markdown(
                "- Resolve asset and alarm context\n"
                "- Retrieve procedures and guidance\n"
                "- Review evidence and tool trace"
            )

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.write(message["content"])
            elif message.get("error"):
                st.error(message["error"])
                if message.get("query"):
                    if st.button(
                        "Retry this investigation",
                        key=f"retry-{message['id']}",
                    ):
                        st.session_state["queued_prompt"] = message["query"]
                        st.rerun()
            elif message.get("result"):
                render_result(message["result"])

    prompt = st.chat_input(
        "Describe an alarm or asset to investigate…",
    )
    query = st.session_state.pop("queued_prompt", None) or prompt
    if query and query.strip():
        query = query.strip()
        st.session_state["messages"].append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.write(query)

        with st.chat_message("assistant"):
            try:
                with st.spinner("Investigating alarm context and procedure evidence…"):
                    result = run_investigation(
                        query,
                        use_rag=use_rag,
                        use_mcp=use_mcp,
                        context=_conversation_context(
                            st.session_state["messages"][:-1]
                        ),
                    )
                st.session_state["messages"].append(
                    {"role": "assistant", "result": result}
                )
                render_result(result)
            except (requests.RequestException, RuntimeError, ValueError) as error:
                if isinstance(error, requests.ReadTimeout):
                    error_message = (
                        "The investigation is taking longer than the 10-minute "
                        "request limit. CPU-based local model inference can be slow; "
                        "check the copilot backend terminal before retrying."
                    )
                else:
                    error_message = str(error)
                st.session_state["messages"].append(
                    {
                        "role": "assistant",
                        "error": error_message,
                        "query": query,
                        "id": len(st.session_state["messages"]),
                    }
                )
                st.error(error_message)
                st.caption("Check service availability and retry the investigation.")


if __name__ == "__main__":
    main()
