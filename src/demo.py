import asyncio
import json
import os
import time
import traceback
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import OpenAI

ENV_PATH = Path(__file__).parent / "tests" / ".env"
load_dotenv(ENV_PATH)

# Configuration
MCP_URL = "http://localhost:8000/mcp"
MODEL = "openai/gpt-4o-mini"
MAX_TOOL_ROUNDS = 30

SYSTEM_PROMPT = """
You are an assistant for public transport in Poznań (ZTM Poznań) only.

Rules:
1. Only answer questions about Poznań public transport. Politely decline
   unrelated questions and questions about transport in other cities.
   Do not call tools for such questions.
2. If the city is not specified, assume Poznań.
3. If a stop name or route number needed to answer is missing, ask one
   short clarifying question and do not call tools yet.
4. Follow the MCP server instructions when selecting and using tools.
   Verify factual claims about stops and routes using the appropriate tools;
   never answer these questions from memory alone.
5. If a tool returns no results, report that honestly. Never invent stops,
   routes, or departure times.
6. Reply in the language used by the user.
"""


def json_text(value):
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def render_trace(container, trace):
    """Refresh the visible tool-call timeline."""
    with container.container():
        st.subheader(f"🔧 MCP tool calls ({len(trace)})")

        if not trace:
            st.caption("Waiting for the agent to call a tool…")
            return

        for i, call in enumerate(trace, 1):
            status = call["status"]
            icon = {
                "running": "🔄",
                "success": "✅",
                "error": "❌",
            }.get(status, "•")

            with st.expander(
                f"{icon} {i}. {call['name']} — {status}",
                expanded=(status == "running"),
            ):
                st.caption("Arguments")
                st.json(call["arguments"])

                if "result" in call:
                    st.caption("Tool result")
                    st.code(call["result"], language="json")

                if "error" in call:
                    st.error(call["error"])

                if "duration" in call:
                    st.caption(f"Duration: {call['duration']:.2f} s")


async def run_agent(messages, trace_container):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Set the OPENROUTER_API_KEY environment variable.")

    llm = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    trace = []
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *messages,
    ]

    # Connect to the existing MCP server.
    async with (
        streamablehttp_client(MCP_URL) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        discovered = await session.list_tools()

        # Convert actual MCP definitions into OpenAI-compatible tools.
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in discovered.tools
        ]

        # Run the model/tool loop.
        for _ in range(MAX_TOOL_ROUNDS):
            response = await asyncio.to_thread(
                llm.chat.completions.create,
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            assistant_message = response.choices[0].message

            if not assistant_message.tool_calls:
                return assistant_message.content or "", trace

            # Preserve the assistant's tool-call message for the next turn.
            messages.append(assistant_message.model_dump(exclude_none=True))

            for tool_call in assistant_message.tool_calls:
                name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments or "{}")

                entry = {
                    "name": name,
                    "arguments": arguments,
                    "status": "running",
                }
                trace.append(entry)
                render_trace(trace_container, trace)

                started = time.perf_counter()

                try:
                    result = await session.call_tool(name, arguments)
                    entry["duration"] = time.perf_counter() - started

                    # Keep the complete textual result visible.
                    output = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            output.append(item.text)
                        else:
                            output.append(str(item))

                    result_text = "\n".join(output)

                    if result.isError:
                        entry["status"] = "error"
                        entry["error"] = result_text
                    else:
                        entry["status"] = "success"

                    entry["result"] = result_text
                    render_trace(trace_container, trace)

                except Exception as exc:
                    entry["status"] = "error"
                    entry["duration"] = time.perf_counter() - started
                    entry["error"] = str(exc)
                    result_text = f"Tool execution failed: {exc}"
                    render_trace(trace_container, trace)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_text,
                    }
                )

    return (
        "I couldn't complete the request within the tool-call limit.",
        trace,
    )


# ----------------------- Streamlit UI -----------------------

st.set_page_config(
    page_title="Poznań Transit Assistant",
    page_icon="🚋",
    layout="wide",
)

st.title("🚋 Poznań Transit Assistant")
st.caption("Live conversational demo · OpenRouter + MCP")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "traces" not in st.session_state:
    st.session_state.traces = {}

with st.sidebar:
    st.header("Demo settings")
    st.code(MCP_URL)
    st.caption(f"Model: {MODEL}")

    if st.button("New conversation"):
        st.session_state.messages = []
        st.session_state.traces = {}
        st.rerun()

# Render previous messages.
for index, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message["role"] == "assistant":
            old_trace = st.session_state.traces.get(index, [])
            if old_trace:
                with st.expander(f"🔧 {len(old_trace)} MCP tool call(s)"):
                    for i, call in enumerate(old_trace, 1):
                        st.markdown(f"**{i}. {call['name']} — {call['status']}**")
                        st.json(call["arguments"])
                        if "result" in call:
                            st.code(call["result"])
                        if "error" in call:
                            st.error(call["error"])

if question := st.chat_input("Ask about public transport in Poznań…"):
    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        answer_area = st.empty()
        trace_area = st.empty()

        try:
            with st.spinner("Agent is thinking…"):
                answer, trace = asyncio.run(
                    run_agent(
                        st.session_state.messages,
                        trace_area,
                    )
                )

            answer_area.markdown(answer)

        except Exception as exc:
            answer = f"Error: {exc}"
            trace = []
            answer_area.error(answer)
            st.code(
                "".join(traceback.format_exception(exc)),
                language="text",
            )

            print(traceback.format_exc())

        assistant_index = len(st.session_state.messages)
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )
        st.session_state.traces[assistant_index] = trace

    st.rerun()
