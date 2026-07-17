import logging
from types import SimpleNamespace

import pytest

from gateway.platforms.base import SendResult
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig


class _LoggingAdapter:
    name = "telegram"
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self, *, succeed=True):
        self.succeed = succeed
        self.sent = []
        self.edited = []

    def truncate_message(self, text, limit, len_fn=len):
        return [text]

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append(
            {"chat_id": chat_id, "content": content, "reply_to": reply_to, "metadata": metadata}
        )
        if self.succeed:
            return SendResult(success=True, message_id="m-final")
        return SendResult(success=False, error="telegram send failed")

    async def edit_message(self, chat_id, message_id, content, finalize=False, metadata=None):
        self.edited.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "finalize": finalize,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id=message_id)


@pytest.mark.asyncio
async def test_stream_final_send_logs_success(caplog):
    adapter = _LoggingAdapter(succeed=True)
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(buffer_only=True),
    )

    with caplog.at_level(logging.INFO, logger="gateway.stream_consumer"):
        consumer.on_delta("final answer")
        consumer.finish()
        await consumer.run()

    assert consumer.final_response_sent is True
    assert any(
        "Final response delivery ok" in record.message
        and "path=stream-final-visible" in record.message
        and "message_id=m-final" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_stream_final_send_logs_failure(caplog):
    adapter = _LoggingAdapter(succeed=False)
    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        StreamConsumerConfig(buffer_only=True),
    )

    with caplog.at_level(logging.INFO, logger="gateway.stream_consumer"):
        consumer.on_delta("final answer")
        consumer.finish()
        await consumer.run()

    assert consumer.final_response_sent is False
    assert any(
        "Final response delivery failed" in record.message
        and "path=stream-final-send" in record.message
        and "final stream delivery not confirmed" in record.message
        for record in caplog.records
    )
