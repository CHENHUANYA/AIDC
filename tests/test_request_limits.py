import pytest
from pydantic import ValidationError

from app_context import AlarmTrigger, ChatRequest, FeedbackRequest, IngestTextRequest, Message


def test_chat_request_requires_messages_and_bounds_generation_parameters():
    with pytest.raises(ValidationError):
        ChatRequest(messages=[])
    with pytest.raises(ValidationError):
        ChatRequest(messages=[Message(role="user", content="hello")], temperature=2.1)
    with pytest.raises(ValidationError):
        ChatRequest(messages=[Message(role="user", content="hello")], max_tokens=8193)


def test_chat_request_rejects_excessive_history_and_message_content():
    message = {"role": "user", "content": "hello"}
    with pytest.raises(ValidationError):
        ChatRequest(messages=[message] * 25)
    with pytest.raises(ValidationError):
        Message(role="user", content="x" * 20_001)


def test_ingest_feedback_and_alarm_payloads_have_storage_bounds():
    with pytest.raises(ValidationError):
        IngestTextRequest(text="x" * 200_001)
    with pytest.raises(ValidationError):
        FeedbackRequest(query="q", collection="808d", feedback="x" * 33)
    with pytest.raises(ValidationError):
        AlarmTrigger(alarm_code="x" * 129)
