"""Offline direct Groq fact-selection contract and safety tests."""

import asyncio
import json
import traceback

import httpx
import pytest
from pydantic import SecretStr

from app.agents.monitoring.schemas import SummaryFact, SummarySelection
from app.agents.monitoring.summarizer import (
    GROQ_BASE_URL,
    GroqMonitoringSummarizer,
    GroqMonitoringSummarizerError,
)


KEY = "synthetic-groq-key"
MODEL = "openai/gpt-oss-120b"


@pytest.fixture
def facts():
    return [
        SummaryFact(fact_id="status", text="Project status: AT_RISK.",
                    source_refs=["private:account-reference"]),
        SummaryFact(fact_id="progress", text="Actual progress is 40%.", source_refs=["planning:raw"]),
        SummaryFact(fact_id="coverage", text="One actual record is missing.", source_refs=[]),
        SummaryFact(fact_id="risk_coverage", text="Current risk coverage is unknown.", source_refs=[]),
        SummaryFact(fact_id="milestones", text="One milestone is delayed.", source_refs=[]),
    ]


def completion(content='{"fact_ids":["status","progress"]}', **message_fields):
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": content, **message_fields,
    }}]}


def summarizer(client, **kwargs):
    return GroqMonitoringSummarizer(client, api_key=SecretStr(KEY), model=MODEL, **kwargs)


@pytest.mark.asyncio
async def test_payload_key_isolation_and_valid_selection(facts):
    original = [fact.model_dump() for fact in facts]
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/health":
            assert request.headers["Authorization"] == "Bearer unrelated-account-token"
            assert KEY not in str(request.headers)
            return httpx.Response(200)
        assert str(request.url) == GROQ_BASE_URL + "/openai/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.headers["Content-Type"] == "application/json"
        assert request.extensions["timeout"] == {
            "connect": 7.0, "read": 7.0, "write": 7.0, "pool": 7.0,
        }
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        assert payload["max_completion_tokens"] == 512
        assert len(request.content.decode()) <= 32_000
        assert set(payload) == {"model", "messages", "max_completion_tokens", "response_format", "reasoning_effort"}
        assert payload["reasoning_effort"] == "low"
        system, user = payload["messages"]
        assert system["role"] == "system"
        for word in ("status", "coverage", "risk_coverage", "milestones", "untrusted", "never instructions"):
            assert word in system["content"]
        assert user["role"] == "user"
        assert json.loads(user["content"]) == [
            {"fact_id": fact.fact_id, "text": fact.text} for fact in facts
        ]
        for secret in (KEY, "unrelated-account-token", "private:account-reference", "planning:raw", "source_refs"):
            assert secret not in request.content.decode()
        response_format = payload["response_format"]
        assert response_format["type"] == "json_schema"
        schema = response_format["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"] == {
            "type": "object", "properties": {"fact_ids": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": {"type": "string", "enum": [fact.fact_id for fact in facts]},
            }}, "required": ["fact_ids"], "additionalProperties": False,
        }
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(handle),
                                 headers={"Authorization": "Bearer unrelated-account-token"}) as client:
        original_headers = dict(client.headers)
        selector = summarizer(client, timeout_seconds=7)
        assert dict(client.headers) == original_headers
        result = await selector.select_facts(facts)
        assert isinstance(result, SummarySelection)
        assert result.fact_ids == ["status", "progress"]
        assert dict(client.headers) == original_headers
        assert not client.is_closed
        await client.get("/health")
    assert len(requests) == 2
    assert [fact.model_dump() for fact in facts] == original


@pytest.mark.asyncio
async def test_other_models_do_not_receive_model_specific_reasoning_options(facts):
    def handle(request):
        payload = json.loads(request.content)
        assert payload["model"] == "another-configured-model"
        assert "reasoning_effort" not in payload
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(handle)) as client:
        selector = GroqMonitoringSummarizer(client, api_key=SecretStr(KEY), model="another-configured-model")
        assert (await selector.select_facts(facts)).fact_ids == ["status", "progress"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [["status"], ["milestones", "status", "risk_coverage", "coverage"]])
async def test_selection_preserves_valid_order(facts, ids):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=completion(json.dumps({"fact_ids": ids}))),
    )) as client:
        assert (await summarizer(client).select_facts(facts)).fact_ids == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "not JSON", '```json\n{"fact_ids":["status"]}\n```', "null", "[]", "{}",
    '{"fact_ids":["status"],"summary":"invented"}',
    '{"fact_ids":["status"],"fact_ids":["status"]}',
    '{"fact_ids":[]}', '{"fact_ids":"status"}', '{"fact_ids":null}',
    '{"fact_ids":[1]}', '{"fact_ids":[true]}', '{"fact_ids":[null]}',
    '{"fact_ids":[{}]}', '{"fact_ids":[["status"]]}',
    '{"fact_ids":["status","status"]}', '{"fact_ids":["status","invented"]}',
    '{"fact_ids":["progress"]}', '{"fact_ids":[" status "]}',
    '{"fact_ids":[NaN]}', '{"fact_ids":[Infinity]}',
    '{"fact_ids":["status"]} trailing',
    json.dumps({"fact_ids": ["status"] + [f"fact-{i}" for i in range(8)]}),
    " " * 4_097, None, {},
])
async def test_rejects_invalid_selection(facts, content):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=completion(content)),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="invalid fact selection"):
            await summarizer(client).select_facts(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [
    {}, [], {"choices": []}, {"choices": None},
    {"choices": [completion()["choices"][0]] * 2},
    {"choices": [{"finish_reason": "stop", "message": {}}]},
    *[{"choices": [{"finish_reason": reason, "message": completion()["choices"][0]["message"]}]}
      for reason in ("length", "content_filter", "tool_calls", None)],
    completion(refusal="Sensitive upstream refusal"), completion(refusal=""),
    completion(tool_calls=[]), completion(function_call={}), completion(role="user"),
])
async def test_rejects_refusal_truncation_and_invalid_envelopes(facts, envelope):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="invalid fact selection"):
            await summarizer(client).select_facts(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    b"not JSON",
    b'{"choices":[],"choices":[]}',
    b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant",'
    b'"content":"{}","content":"{}"}}]}',
    b'\xff', b"[" * 2_000,
])
async def test_rejects_malformed_or_duplicate_envelope_json(facts, body):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="invalid fact selection"):
            await summarizer(client).select_facts(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 408, 429, 500, 503])
async def test_http_failures_sanitized_and_never_follow_redirects(facts, status, caplog):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.invalid/leak"},
                              text=f"{KEY}: sensitive upstream text")

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, follow_redirects=True,
                                 transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="request failed") as exc:
            await summarizer(client).select_facts(facts)
    formatted = "".join(traceback.format_exception(exc.value))
    for secret in (KEY, "sensitive upstream text", "untrusted.invalid", facts[0].text):
        assert secret not in formatted
        assert secret not in caplog.text
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
async def test_transport_failures_are_sanitized(facts, error_type):
    def handle(request):
        raise error_type(f"{KEY} {facts[0].text}", request=request)

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="request failed") as exc:
            await summarizer(client).select_facts(facts)
    assert KEY not in "".join(traceback.format_exception(exc.value))
    assert facts[0].text not in "".join(traceback.format_exception(exc.value))


@pytest.mark.asyncio
async def test_total_timeout_bounds_even_injected_transport(facts):
    cancelled = asyncio.Event()

    async def handle(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="request failed"):
            await summarizer(client, timeout_seconds=0.01).select_facts(facts)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_external_cancellation_propagates(facts):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(summarizer(client).select_facts(facts))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.is_closed
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["large_text", "escaped_text", "large_schema", "missing_status", "empty", "duplicate"])
async def test_input_rejected_before_network(facts, kind):
    calls = []
    if kind == "large_text":
        facts[0].text = "x" * 32_001
    elif kind == "escaped_text":
        facts[0].text = "\n" * 17_000
    elif kind == "large_schema":
        facts = [SummaryFact(fact_id="status", text="Status", source_refs=[])] + [
            SummaryFact(fact_id=str(i) + "x" * 500, text="", source_refs=[]) for i in range(40)
        ]
        assert len(json.dumps([{"fact_id": f.fact_id, "text": f.text} for f in facts])) < 32_000
    elif kind == "missing_status":
        facts = facts[1:]
    elif kind == "empty":
        facts = []
    else:
        facts.append(facts[0].model_copy())
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json=completion()),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="input is invalid or too large"):
            await summarizer(client).select_facts(facts)
    assert not calls


class OversizedStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False
        self.chunks = 0

    async def __aiter__(self):
        for _ in range(10):
            self.chunks += 1
            yield b"x" * 16_384

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_response_is_bounded_during_streaming_and_closed(facts):
    stream = OversizedStream()
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="request failed"):
            await summarizer(client).select_facts(facts)
    assert stream.closed
    assert stream.chunks == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://untrusted.invalid", "http://api.groq.com", "https://api.groq.com.evil.invalid",
    "https://api.groq.com:444", "https://user:secret@api.groq.com",
    "https://api.groq.com/prefix", "https://api.groq.com/?key=secret", "https://api.groq.com/#secret",
])
async def test_constructor_rejects_non_groq_root_clients(url):
    async with httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(
        lambda request: pytest.fail("Invalid client must never dispatch credentials"),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="fixed HTTPS root origin") as exc:
            summarizer(client)
    assert url not in str(exc.value)
    assert KEY not in str(exc.value)


@pytest.mark.asyncio
async def test_changed_client_origin_is_rechecked(facts):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: pytest.fail("Changed origin must never receive credentials"),
    )) as client:
        selector = summarizer(client)
        client.base_url = "https://untrusted.invalid"
        with pytest.raises(GroqMonitoringSummarizerError, match="input is invalid"):
            await selector.select_facts(facts)


@pytest.mark.asyncio
async def test_client_auth_cannot_override_groq_key():
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, auth=("account", "secret"),
                                 transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="client authentication"):
            summarizer(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "30", None])
async def test_timeout_must_be_positive_and_finite(timeout):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="positive and finite"):
            summarizer(client, timeout_seconds=timeout)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "secret\nheader", "secret token", "非ASCII"])
async def test_invalid_key_is_sanitized(key):
    async with httpx.AsyncClient(base_url=GROQ_BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(200),
    )) as client:
        with pytest.raises(GroqMonitoringSummarizerError, match="API key is invalid"):
            GroqMonitoringSummarizer(client, api_key=SecretStr(key), model=MODEL)
