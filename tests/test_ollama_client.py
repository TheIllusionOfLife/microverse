"""Unit tests for microverse.llm.ollama_client.chat (mocked ollama)."""

from unittest.mock import MagicMock, patch

import pytest

from microverse.llm import ollama_client
from microverse.llm.ollama_client import chat


@pytest.fixture(autouse=True)
def reset_counter():
    ollama_client.thinking_leak = 0
    ollama_client.llm_retry = 0
    # The chat() helper caches an ollama.Client per timeout. Clear the
    # cache so each test's fresh ``patch("...ollama.Client")`` is what
    # actually gets constructed.
    ollama_client._get_client.cache_clear()
    return None


def _mock_response(*, content: str, thinking: str = "") -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.thinking = thinking
    resp = MagicMock()
    resp.message = msg
    resp.model_dump.return_value = {"message": {"content": content, "thinking": thinking}}
    return resp


def test_chat_returns_content_thinking_and_raw():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="hello", thinking="")

        result = chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "hello"
    assert result["thinking"] == ""
    assert result["raw"] == {"message": {"content": "hello", "thinking": ""}}


def test_chat_forwards_think_false_by_default():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="ok")

        chat([{"role": "user", "content": "hi"}])

    kwargs = instance.chat.call_args.kwargs
    assert kwargs["think"] is False
    assert kwargs["model"] == "gemma4:e4b"


def test_chat_forwards_think_true_when_requested():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="ok", thinking="hmm")

        result = chat([{"role": "user", "content": "hi"}], think=True)

    assert instance.chat.call_args.kwargs["think"] is True
    assert result["thinking"] == "hmm"


def test_strip_thinking_applied_when_content_has_leak():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="<think>internal</think>real answer")

        result = chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "real answer"
    assert ollama_client.thinking_leak == 1


def test_thinking_leak_counter_does_not_bump_when_clean():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="clean answer")

        chat([{"role": "user", "content": "hi"}])

    assert ollama_client.thinking_leak == 0


def test_think_false_clears_thinking_field_even_if_model_emits_it():
    """If think=False but the runtime still populates message.thinking,
    the wrapper must clear it so callers cannot accidentally read it.
    """
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(
            content="real answer", thinking="model leaked thinking despite think=False"
        )

        result = chat([{"role": "user", "content": "hi"}], think=False)

    assert result["thinking"] == ""
    # And: this counts as a leak signal
    assert ollama_client.thinking_leak == 1


def test_think_true_preserves_thinking_field():
    """When the caller explicitly requested thinking, do not clear it."""
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="answer", thinking="reasoning")

        result = chat([{"role": "user", "content": "hi"}], think=True)

    assert result["thinking"] == "reasoning"


def test_unclosed_think_in_content_increments_leak_counter():
    """Counter must trigger on detected markers, not just on stripped
    output, so unclosed <think> is still flagged as a leak."""
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="<think>unclosed reasoning")

        chat([{"role": "user", "content": "hi"}])

    assert ollama_client.thinking_leak == 1


def test_channel_marker_in_content_increments_leak_counter():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(
            content="<|channel|>analysis<|message|>x<|channel|>final<|message|>final"
        )

        chat([{"role": "user", "content": "hi"}])

    assert ollama_client.thinking_leak == 1


def test_counter_concurrency_safe():
    """Bump counter from many threads — final count must equal calls."""
    import threading

    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="<think>x</think>ok")

        threads = [
            threading.Thread(target=lambda: chat([{"role": "user", "content": "hi"}]))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert ollama_client.thinking_leak == 20


def test_format_and_options_forwarded():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="{}")

        chat(
            [{"role": "user", "content": "hi"}],
            format="json",
            options={"temperature": 0.6, "top_p": 0.9},
        )

    kwargs = instance.chat.call_args.kwargs
    assert kwargs["format"] == "json"
    assert kwargs["options"] == {"temperature": 0.6, "top_p": 0.9}


def test_transient_connection_error_retried_then_succeeds():
    """A connection-class error on the first call must be retried and
    the second call's success returned. llm_retry counter increments.

    Uses the built-in ConnectionError (the type ollama-python actually
    emits by wrapping httpx.ConnectError) rather than httpx.ConnectError
    directly — testing the type that reaches our code in production.
    """
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = [
            ConnectionError("connection refused"),
            _mock_response(content="recovered"),
        ]
        # No real backoff — patch sleep so the test stays fast,
        # AND assert the schedule so a regression to (0, 0) is caught.
        with patch("microverse.llm.ollama_client.time.sleep") as mock_sleep:
            result = chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "recovered"
    assert ollama_client.llm_retry == 1
    assert instance.chat.call_count == 2
    # Pin the backoff schedule: one retry → one sleep at the first backoff.
    mock_sleep.assert_called_once_with(0.5)


def test_non_connection_error_not_retried():
    """ValueError-class errors (validation, schema) must not retry —
    those signal a programmer bug, not a transient infra blip."""
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = ValueError("bad schema")

        with (
            patch("microverse.llm.ollama_client.time.sleep"),
            pytest.raises(ValueError),
        ):
            chat([{"role": "user", "content": "hi"}])

    assert instance.chat.call_count == 1
    assert ollama_client.llm_retry == 0


def test_max_retries_exceeded_reraises():
    """After 2 retries (3 total attempts) of connection errors, the
    last error must propagate."""
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = ConnectionError("permanent")

        with (
            patch("microverse.llm.ollama_client.time.sleep") as mock_sleep,
            pytest.raises(ConnectionError),
        ):
            chat([{"role": "user", "content": "hi"}])

    assert instance.chat.call_count == 3  # initial + 2 retries
    assert ollama_client.llm_retry == 2
    # Pin the schedule: two retries → sleep called twice at (0.5, 1.5).
    assert mock_sleep.call_args_list == [((0.5,), {}), ((1.5,), {})]


def test_5xx_response_error_retried():
    """ollama.ResponseError with status_code >= 500 is transient
    (daemon restart, upstream proxy hiccup) and must be retried."""
    import ollama as _ollama

    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = [
            _ollama.ResponseError("upstream restart", 503),
            _mock_response(content="recovered"),
        ]
        with patch("microverse.llm.ollama_client.time.sleep"):
            result = chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "recovered"
    assert ollama_client.llm_retry == 1
    assert instance.chat.call_count == 2


def test_4xx_response_error_not_retried():
    """ollama.ResponseError with status_code < 500 is a caller bug
    (bad model name, malformed payload). Retrying just wastes time."""
    import ollama as _ollama

    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.side_effect = _ollama.ResponseError("bad model", 404)

        with (
            patch("microverse.llm.ollama_client.time.sleep"),
            pytest.raises(_ollama.ResponseError),
        ):
            chat([{"role": "user", "content": "hi"}])

    assert instance.chat.call_count == 1
    assert ollama_client.llm_retry == 0


def test_timeout_s_constructs_client_with_timeout():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="ok")

        chat([{"role": "user", "content": "hi"}], timeout_s=5)

    MockClient.assert_called_with(timeout=5)
