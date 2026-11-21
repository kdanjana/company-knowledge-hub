"""Step 3 of the audit (only for "unsure" pairs): the investigator agent, built with LangGraph.

Why an agent here and nowhere else: how many lookups an unclear pair needs depends on the case
(sometimes one, sometimes four). The LLM decides which tool to call next, in a loop, with a
hard limit on tool calls.

Graph:
    agent --(model asked for a tool)--> tools --> agent
    agent --(model gave an answer, or limit reached)--> finalize --> END
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from .judge import format_passage, parse_verdict
from .prompts import INVESTIGATOR_SYSTEM, INVESTIGATOR_USER_TEMPLATE

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "get_neighbor_chunks",
            "description": "Return the passages directly before and after a passage in the same document. "
            "Use it to find a condition (region, audience, exception) that may be in a nearby passage.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"chunk_id": {"type": "integer", "description": "ID of the passage"}},
                    "required": ["chunk_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_document_details",
            "description": "Return a document's title, type, version, effective date and status (active, draft, superseded).",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "integer", "description": "ID of the document"}},
                    "required": ["doc_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_related_passages",
            "description": "Search all documents for passages about a topic. Returns the 5 most similar passages.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "What to search for"}},
                    "required": ["query"],
                }
            },
        }
    },
]


class InvestigatorState(TypedDict):
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]  # readable trace, shown in the UI
    rounds: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    stop_reason: str
    answer: str


@dataclass
class InvestigationResult:
    final: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    grounded: bool = False
    error: str = ""


def _preview(obj: Any, limit: int = 400) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def make_tool_runner(adapter, embed_fn: Callable[[str], list[float]]) -> Callable[[str, dict], dict]:
    def run_tool(name: str, args: dict) -> dict:
        if name == "get_neighbor_chunks":
            neighbors = adapter.get_neighbors(int(args["chunk_id"]), window=1)
            return {
                "neighbors": [
                    {"chunk_id": n.id, "doc_id": n.doc_id, "section": n.heading_path, "text": n.text} for n in neighbors
                ]
            }
        if name == "get_document_details":
            doc = adapter.get_document(int(args["doc_id"]))
            return doc.to_dict() if doc else {"error": "No document with that ID."}
        if name == "search_related_passages":
            hits = adapter.search_by_vector(embed_fn(str(args["query"])), k=5)
            return {
                "results": [
                    {"chunk_id": c.id, "doc_id": c.doc_id, "section": c.heading_path, "text": c.text, "similarity": round(s, 3)}
                    for c, s in hits
                ]
            }
        return {"error": f"Unknown tool: {name}"}

    return run_tool


def build_graph(llm, run_tool: Callable[[str, dict], dict], max_steps: int):
    def agent(state: InvestigatorState) -> dict:
        result = llm.converse(state["messages"], system=INVESTIGATOR_SYSTEM, tools=TOOL_SPECS, max_tokens=1000)
        requested = [b["toolUse"]["name"] for b in result.content if "toolUse" in b]
        return {
            "messages": state["messages"] + [{"role": "assistant", "content": result.content or [{"text": "(empty answer)"}]}],
            "steps": state["steps"] + [{"type": "model", "text": result.text, "tool_requests": requested}],
            "rounds": state["rounds"] + 1,
            "input_tokens": state["input_tokens"] + result.input_tokens,
            "output_tokens": state["output_tokens"] + result.output_tokens,
            "stop_reason": result.stop_reason,
            "answer": result.text,
        }

    def tools(state: InvestigatorState) -> dict:
        last = state["messages"][-1]
        results, steps, calls = [], list(state["steps"]), state["tool_calls"]
        for block in last["content"]:
            if "toolUse" not in block:
                continue
            tool_use = block["toolUse"]
            if calls >= max_steps:
                output = {"error": "Tool limit reached. Give your final JSON answer now."}
            else:
                calls += 1
                try:
                    output = run_tool(tool_use["name"], tool_use.get("input") or {})
                except Exception as exc:  # a failing tool should not crash the run
                    output = {"error": f"Tool failed: {exc}"}
            results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use["toolUseId"],
                        "content": [{"text": json.dumps(output, ensure_ascii=False, default=str)}],
                    }
                }
            )
            steps.append({"type": "tool", "name": tool_use["name"], "input": tool_use.get("input"), "output_preview": _preview(output)})
        return {"messages": state["messages"] + [{"role": "user", "content": results}], "steps": steps, "tool_calls": calls}

    def route(state: InvestigatorState) -> str:
        if state["stop_reason"] == "tool_use" and state["rounds"] <= max_steps + 1:
            return "tools"
        return "finalize"

    def finalize(state: InvestigatorState) -> dict:
        return {}

    graph = StateGraph(InvestigatorState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", "finalize": "finalize"})
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_investigation(llm, adapter, embed_fn, chunk_a, doc_a, chunk_b, doc_b, judge_reason: str, max_steps: int = 4) -> InvestigationResult:
    user = INVESTIGATOR_USER_TEMPLATE.format(
        passage_a=format_passage("A", chunk_a, doc_a, with_ids=True),
        passage_b=format_passage("B", chunk_b, doc_b, with_ids=True),
        judge_reason=judge_reason or "(no reason given)",
        max_steps=max_steps,
    )
    graph = build_graph(llm, make_tool_runner(adapter, embed_fn), max_steps)
    state = graph.invoke(
        {
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "steps": [],
            "rounds": 0,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "stop_reason": "",
            "answer": "",
        },
        config={"recursion_limit": 50},
    )

    verdict, problem = parse_verdict(state["answer"], chunk_a.text, chunk_b.text)
    if verdict is None:
        verdict = {
            "label": "unsure",
            "topic": "",
            "quote_a": "",
            "quote_b": "",
            "confidence": "low",
            "reason": f"The investigator's answer was rejected: {problem}",
        }
    return InvestigationResult(
        final=verdict,
        steps=state["steps"],
        tool_calls=state["tool_calls"],
        input_tokens=state["input_tokens"],
        output_tokens=state["output_tokens"],
        grounded=not problem,
        error=problem,
    )
