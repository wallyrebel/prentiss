import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rss_to_wp.pipeline import error_details
from rss_to_wp.replay import replay
from rss_to_wp.rewriter.openai_client import (
    EditorialResponseError,
    OpenAIRewriter,
    REVIEW_PROMPT,
)


def writer_response(content="{}", finish="stop", refusal=None):
    writer = OpenAIRewriter("test")
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=8000,
            total_tokens=8100,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=7800),
        ),
    )
    writer.client.chat.completions.create = Mock(return_value=response)
    return writer


def test_truncation_retains_safe_metadata_and_fails_closed():
    writer = writer_response("private response body", "length")
    with pytest.raises(EditorialResponseError) as caught:
        writer._json(REVIEW_PROMPT, {"schema": {}}, writer.review_model)
    details = error_details(caught.value)
    assert details["diagnostics"]["code"] == "incomplete_response"
    assert details["diagnostics"]["stage"] == "review"
    assert details["diagnostics"]["finish_reason"] == "length"
    assert details["diagnostics"]["reasoning_tokens"] == 7800
    assert "private response body" not in json.dumps(details)
    assert writer.client.chat.completions.create.call_count == 1


def test_confirmed_empty_reasoning_exhaustion_still_fails_closed_without_retry():
    writer = writer_response("", "length")
    response = writer.client.chat.completions.create.return_value
    response.usage.completion_tokens_details.reasoning_tokens = 8000
    with pytest.raises(EditorialResponseError) as caught:
        writer._json(REVIEW_PROMPT, {"schema": {}, "article": {}}, writer.review_model)
    diagnostics = caught.value.diagnostics
    assert diagnostics["code"] == "incomplete_response"
    assert diagnostics["content_chars"] == 0
    assert diagnostics["reasoning_tokens"] == 8000
    assert diagnostics["token_limit"] == 12000
    assert diagnostics["reasoning_effort"] == "medium"
    assert writer.client.chat.completions.create.call_count == 1


def test_review_error_retains_input_draft_but_not_failed_response():
    writer = writer_response("failed response text", "length")
    article = {"headline": "Public draft", "body": "<p>Source facts.</p>"}
    with pytest.raises(EditorialResponseError) as caught:
        writer._json(REVIEW_PROMPT, {"schema": {}, "article": article}, writer.review_model)
    assert caught.value.review_request == {"article": article}
    assert "failed response text" not in json.dumps(caught.value.review_request)


@pytest.mark.parametrize(
    "content,code", [("not-json secret", "invalid_json"), ("[]", "non_object_response")]
)
def test_invalid_response_never_serializes_content(content, code):
    with pytest.raises(EditorialResponseError) as caught:
        writer_response(content)._json("writer", {}, "test")
    assert caught.value.diagnostics["code"] == code
    assert content not in json.dumps(error_details(caught.value))


def test_refusal_and_api_errors_do_not_leak_messages():
    with pytest.raises(EditorialResponseError) as caught:
        writer_response(refusal="private refusal")._json("writer", {}, "test")
    assert caught.value.diagnostics["code"] == "refused_response"
    assert "private refusal" not in json.dumps(error_details(caught.value))
    writer = writer_response()
    writer.client.chat.completions.create.side_effect = RuntimeError("Bearer secret-key")
    with pytest.raises(EditorialResponseError) as caught:
        writer._json("writer", {}, "test")
    assert caught.value.diagnostics["code"] == "api_request_failed"
    assert "secret-key" not in json.dumps(error_details(caught.value))
    assert error_details(ValueError("secret")) == {"reason": "ValueError"}


def test_replay_uses_retained_cluster_without_publication_or_state(monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        Mock(side_effect=AssertionError("HTTP forbidden")),
    )
    writer = Mock()
    writer.rewrite_sources.return_value = {
        "skip": True,
        "reason": "editorial_rejection",
    }
    snapshot = json.loads(Path("tests/fixtures/oct3_editorial_replay.json").read_text())
    result = replay(snapshot, writer)
    assert result["dry_run"] and result["status"] == "skipped"
    assert result["source_words"] == 220 and len(result["urls"]) == 4
    assert writer.rewrite_sources.call_count == 1


def test_replay_reports_error_without_retry_or_publishing():
    writer = Mock()
    writer.rewrite_sources.side_effect = EditorialResponseError(
        "incomplete_response", "review", finish_reason="length"
    )
    snapshot = json.loads(Path("tests/fixtures/oct3_editorial_replay.json").read_text())
    result = replay(snapshot, writer)
    assert result["status"] == "error"
    assert result["diagnostics"]["finish_reason"] == "length"
    assert writer.rewrite_sources.call_count == 1


def test_saved_review_replay_repeats_review_without_rewriting():
    writer = writer_response("truncated response", "length")
    writer.rewrite_sources = Mock(side_effect=AssertionError("Rewriting forbidden"))
    snapshot = json.loads(Path("tests/fixtures/oct3_editorial_replay.json").read_text())
    article = {"headline": "Public draft", "body": "<p>Source facts.</p>"}
    snapshot["review_request"] = {"article": article}
    result = replay(snapshot, writer)
    assert result["status"] == "error"
    assert result["diagnostics"]["stage"] == "review"
    assert result["diagnostics"]["finish_reason"] == "length"
    assert not writer.rewrite_sources.called
    args = writer.client.chat.completions.create.call_args.kwargs
    assert json.loads(args["messages"][1]["content"])["article"] == article
    assert args["max_completion_tokens"] == 12000
    assert args["reasoning_effort"] == "medium"
    assert writer.client.chat.completions.create.call_count == 1


@pytest.mark.parametrize("previous_review", [False, True])
def test_review_budget_preserves_models_schema_and_content(previous_review):
    writer = writer_response()
    payload = {
        "schema": {"type": "object"},
        "article": {"headline": "Original draft"},
        "recent_stories": [{"id": 42, "title": "Original duplicate context"}],
    }
    if previous_review:
        payload["previous_review"] = {"approved": False}
    assert writer._json(REVIEW_PROMPT, payload, writer.review_model) == {}
    args = writer.client.chat.completions.create.call_args.kwargs
    assert args["model"] == "gpt-5.4-mini"
    assert args["max_completion_tokens"] == 12000
    assert args["reasoning_effort"] == "medium"
    assert args["messages"][0]["content"] == REVIEW_PROMPT
    assert json.loads(args["messages"][1]["content"]) == payload
    assert args["response_format"]["json_schema"]["strict"] is True
    assert args["response_format"]["json_schema"]["schema"] == payload["schema"]
    assert writer.client.chat.completions.create.call_count == 1


def test_older_model_override_does_not_receive_unsupported_reasoning_option():
    writer = writer_response()
    writer._json(REVIEW_PROMPT, {"schema": {}}, "gpt-4.1-nano")
    args = writer.client.chat.completions.create.call_args.kwargs
    assert args["model"] == "gpt-4.1-nano"
    assert "reasoning_effort" not in args


def test_writer_budget_unchanged_and_review_budget_has_hard_ceiling():
    writer = writer_response()
    writer._json("writer", {}, writer.model)
    args = writer.client.chat.completions.create.call_args.kwargs
    assert args["max_completion_tokens"] == 8000 and args["reasoning_effort"] == "low"
    assert writer.review_max_tokens == 12000
    assert OpenAIRewriter("test", max_tokens=20000).review_max_tokens == 12000
