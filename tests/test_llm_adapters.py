"""Exercise the Claude adapters against a stub client so the request shapes
and response handling are covered without network access or an API key."""
import json
from types import SimpleNamespace

import pytest

from analyst.llm import AnthropicAnalystLLM, LLMRefusal, QueryPlan
from docnav.document import load_default
from docnav.navigator import AnthropicNavigator, DocumentNavigator


class StubMessages:
    def __init__(self, parse_responses=None, create_responses=None):
        self.parse_calls, self.create_calls = [], []
        self._parse = list(parse_responses or [])
        self._create = list(create_responses or [])

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        return self._parse.pop(0)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._create.pop(0)


def stub_client(**kw):
    return SimpleNamespace(messages=StubMessages(**kw))


def test_analyst_plan_sends_schema_and_feedback():
    plan = QueryPlan(intent="x", sql="SELECT 1 AS one")
    client = stub_client(parse_responses=[SimpleNamespace(stop_reason="end_turn", parsed_output=plan)])
    llm = AnthropicAnalystLLM(model="claude-opus-5-5", client=client)
    out = llm.plan("q", "TABLE t -- stuff", "analyst", feedback="restricted column")
    assert out is plan
    call = client.messages.parse_calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is QueryPlan
    assert "TABLE t -- stuff" in call["system"]
    assert "restricted column" in call["messages"][0]["content"]


def test_analyst_refusal_raises():
    client = stub_client(parse_responses=[SimpleNamespace(
        stop_reason="refusal", parsed_output=None,
        stop_details=SimpleNamespace(explanation="declined"))])
    llm = AnthropicAnalystLLM(client=client)
    with pytest.raises(LLMRefusal, match="declined"):
        llm.plan("q", "schema", "analyst")


def test_navigator_tool_loop_then_structured_answer():
    tool_turn = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="tu_1", name="read", input={"section_id": "4.3"})],
    )
    final = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps({
            "answer": "Call back a number on file (Section 4.3).",
            "citations": ["4.3"], "confidence": "high", "evidence_gaps": []}))],
    )
    client = stub_client(create_responses=[tool_turn, final])
    nav = DocumentNavigator(load_default(), AnthropicNavigator(client=client))
    res = nav.ask("vendor bank change?")

    assert res.answer.citations == ["4.3"] and res.sections_read == ["4.3"] and res.iterations == 2
    first, second = client.messages.create_calls
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert all(t["strict"] for t in first["tools"])
    # the tool result was fed back with the matching id
    result_block = second["messages"][-1]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "tu_1"
    assert "call-back" in result_block["content"]
