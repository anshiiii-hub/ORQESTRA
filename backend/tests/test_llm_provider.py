import json
from decimal import Decimal

import httpx
import pytest

from app.llm import (
    Completion,
    OpenAIPlannerProvider,
    PlannerSettings,
    PlanningError,
    planner_settings,
)
from app.planner import demo_plan
from app.planning import PlannerProposal, input_token_bound, planning_messages


def settings():
    return PlannerSettings(
        True, "test-model", Decimal("1"), Decimal("2"), 2048, "server-only-test-secret"
    )


def body(content="{}", **updates):
    data = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    return {**data, **updates}


def call(response=None, handler=None):
    transport = httpx.MockTransport(
        handler or (lambda _: httpx.Response(200, json=response or body()))
    )
    return OpenAIPlannerProvider(settings(), transport).complete(
        [{"role": "system", "content": "Return JSON"}], 2048, 10.0, 1000
    )


def test_provider_sends_bounded_json_request_without_prompt_secrets():
    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer server-only-test-secret"
        data = json.loads(request.content)
        assert data["response_format"] == {"type": "json_object"}
        assert data["max_completion_tokens"] == 2048
        assert data["store"] is False
        assert data["service_tier"] == "default"
        assert "server-only-test-secret" not in request.content.decode()
        return httpx.Response(200, json=body())

    assert call(handler=handler) == Completion("{}", 100, 50)
    assert "server-only-test-secret" not in repr(settings())


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "provider_unavailable"),
        (400, "provider_unavailable"),
        (401, "provider_auth_failed"),
        (403, "provider_auth_failed"),
        (429, "provider_rate_limited"),
        (500, "provider_unavailable"),
    ],
)
def test_provider_errors_are_sanitized_without_retries(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "http://127.0.0.1/secret"},
            text="sk-private /host/private-data",
        )

    with pytest.raises(PlanningError) as error:
        call(handler=handler)
    assert error.value.code == code
    assert len(calls) == 1
    assert "sk-private" not in str(error.value)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 50, "total_tokens": 51},
        {"prompt_tokens": 1001, "completion_tokens": 50, "total_tokens": 1051},
        {"prompt_tokens": 100, "completion_tokens": 2049, "total_tokens": 2149},
        {"prompt_tokens": 100, "completion_tokens": -1, "total_tokens": 99},
    ],
)
def test_invalid_usage_is_rejected(usage):
    with pytest.raises(PlanningError):
        call(body(usage=usage))


def test_timeout_does_not_retry_or_leak_details():
    def timeout(request):
        raise httpx.ReadTimeout("private-token", request=request)

    with pytest.raises(PlanningError) as error:
        call(handler=timeout)
    assert error.value.code == "provider_timeout"
    assert "private-token" not in str(error.value)


def test_refusal_truncation_and_oversized_response():
    with pytest.raises(PlanningError, match="declined"):
        call(
            body(choices=[{"message": {"refusal": "private explanation"}, "finish_reason": "stop"}])
        )
    completion = call(body(choices=[{"message": {"content": "{"}, "finish_reason": "length"}]))
    assert completion.truncated
    with pytest.raises(PlanningError) as error:
        call(handler=lambda _: httpx.Response(200, content=b"x" * 262145))
    assert error.value.code == "provider_response_too_large"


def test_disabled_or_invalid_configuration_fails_without_network(monkeypatch):
    monkeypatch.setenv("LLM_PLANNER_ENABLED", "false")
    assert not planner_settings().enabled
    monkeypatch.setenv("LLM_PLANNER_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlanningError, match="configuration"):
        planner_settings()


def test_cost_uses_explicit_decimal_snapshot():
    assert settings().cost(100, 50) == Decimal("0.0002")
    assert settings().snapshot()["input_price_per_million"] == "1"
    assert "api_key" not in settings().snapshot()


def test_prompt_sends_profile_but_never_csv_rows_or_credentials():
    messages = planning_messages("Analyze data", "item,sales\nVERY_PRIVATE_ROW,10\nB,20")
    text = json.dumps(messages)
    assert "VERY_PRIVATE_ROW" not in text
    assert "sales" in text and "row_count" in text
    assert "server-only-test-secret" not in text
    assert input_token_bound(messages) >= sum(
        len(message["content"].encode()) for message in messages
    )
    assert len(planning_messages("Analyze", "a\n1", repair=True)) == 3


def proposal():
    return {
        "schema_version": "1.0",
        "tasks": demo_plan().model_dump()["tasks"],
        "final_task_id": "verify",
    }


def test_proposal_normalizes_out_of_order_tasks():
    data = proposal()
    data["tasks"] = list(reversed(data["tasks"]))
    parsed = PlannerProposal.model_validate(data)
    assert [task.id for task in parsed.ordered_tasks()] == ["inspect", "summarize", "verify"]


@pytest.mark.parametrize("change", ["unverified", "unrelated", "missing_final", "foreign_tool"])
def test_proposals_reject_unverified_or_unauthorized_work(change):
    data = proposal()
    if change == "unverified":
        data["final_task_id"] = "inspect"
    if change == "missing_final":
        data.pop("final_task_id")
    if change == "unrelated":
        data["tasks"].append(
            {"id": "unused", "agent": "csv_inspector", "capability": "csv.inspect"}
        )
    if change == "foreign_tool":
        data["tasks"][0]["tool_permissions"] = ["shell.exec"]
    with pytest.raises(ValueError):
        PlannerProposal.model_validate(data)
