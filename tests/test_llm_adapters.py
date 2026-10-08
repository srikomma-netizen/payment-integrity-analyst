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
        content=[SimpleNamespace(type="tool_use", id="tu_1", name="read", input={"section_id": "4.1"})],
    )
    final = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps({
            "answer": "Call back a number on file (Section 4.1).",
            "citations": ["4.1"], "confidence": "high", "evidence_gaps": []}))],
    )
    client = stub_client(create_responses=[tool_turn, final])
    nav = DocumentNavigator(load_default(), AnthropicNavigator(client=client))
    res = nav.ask("vendor bank change?")

    assert res.answer.citations == ["4.1"] and res.sections_read == ["4.1"] and res.iterations == 2
    first, second = client.messages.create_calls
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert all(t["strict"] for t in first["tools"])
    # the tool result was fed back with the matching id
    result_block = second["messages"][-1]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "tu_1"
    assert "call-back" in result_block["content"]


def test_navigator_stops_cleanly_at_iteration_limit():
    looping = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="tu_1", name="read", input={"section_id": "4.1"})],
    )
    client = stub_client(create_responses=[looping, looping])
    nav = DocumentNavigator(load_default(), AnthropicNavigator(client=client, max_iterations=2))
    res = nav.ask("vendor bank change?")
    assert res.answer.confidence == "low" and res.answer.citations == ["4.1"]


# ---- Gemini adapters, against a stub of client.models.generate_content ----
from google.genai import types as gtypes  # noqa: E402

from analyst.llm import GeminiAnalystLLM, make_llm  # noqa: E402
from docnav.navigator import GeminiNavigator  # noqa: E402


class StubModels:
    def __init__(self, responses):
        self.calls, self._responses = [], list(responses)

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def gemini_client(*responses):
    return SimpleNamespace(models=StubModels(responses))


def test_gemini_plan_uses_json_schema_and_validates():
    plan = {"intent": "count", "tables": ["cases"], "sql": "SELECT COUNT(*) FROM cases"}
    client = gemini_client(SimpleNamespace(text=json.dumps(plan)))
    out = GeminiAnalystLLM(model="gemini-test", client=client).plan("q", "TABLE cases", "analyst", feedback="no such column")
    assert out.sql == plan["sql"] and out.refuse is False
    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["config"].response_mime_type == "application/json"
    assert "sql" in call["config"].response_json_schema["properties"]
    assert "TABLE cases" in call["config"].system_instruction and "no such column" in call["contents"]


def test_gemini_empty_reply_is_a_refusal():
    blocked = SimpleNamespace(text=None, prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[])
    with pytest.raises(LLMRefusal, match="SAFETY"):
        GeminiAnalystLLM(client=gemini_client(blocked)).plan("q", "schema", "analyst")


def test_gemini_navigator_runs_tools_then_asks_for_json():
    call_turn = SimpleNamespace(
        function_calls=[gtypes.FunctionCall(name="read", args={"section_id": "4.1"})],
        candidates=[SimpleNamespace(content=gtypes.Content(role="model", parts=[gtypes.Part(function_call=gtypes.FunctionCall(name="read", args={"section_id": "4.1"}))]))],
    )
    done_turn = SimpleNamespace(function_calls=None, candidates=[])
    final = SimpleNamespace(text=json.dumps({"answer": "Call back (Section 4.1).", "citations": ["4.1"], "confidence": "high"}))
    client = gemini_client(call_turn, done_turn, final)
    res = DocumentNavigator(load_default(), GeminiNavigator(model="gemini-test", client=client)).ask("bank change?")
    assert res.sections_read == ["4.1"] and res.answer.citations == ["4.1"]
    first, second, last = client.models.calls
    assert first["config"].tools and last["config"].tools is None
    # the read result went back as a function response (contents is one growing list, so search it)
    fr = next(part.function_response for c in second["contents"] for part in c.parts if part.function_response)
    assert fr.name == "read" and "call-back" in fr.response["result"]["text"]


def test_provider_selection_prefers_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(make_llm(), GeminiAnalystLLM)
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert type(make_llm()).__name__ == "FakeAnalystLLM"
