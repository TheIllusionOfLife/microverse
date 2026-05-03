"""Unit tests for microverse.llm.ollama_client.chat (mocked ollama)."""

from unittest.mock import MagicMock, patch

import pytest

from microverse.llm import ollama_client
from microverse.llm.ollama_client import chat


@pytest.fixture(autouse=True)
def reset_counter():
    ollama_client.thinking_leak = 0
    yield


def _mock_response(*, content: str, thinking: str = "") -> dict:
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


def test_timeout_s_constructs_client_with_timeout():
    with patch("microverse.llm.ollama_client.ollama.Client") as MockClient:
        instance = MockClient.return_value
        instance.chat.return_value = _mock_response(content="ok")

        chat([{"role": "user", "content": "hi"}], timeout_s=5)

    MockClient.assert_called_with(timeout=5)
