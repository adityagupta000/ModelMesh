import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from helpers import load_module_from_path


class _WebSocketStub:
    def __init__(self, messages):
        self.messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, data):
        return None

    async def recv(self):
        message = self.messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        return message


@pytest.mark.parametrize(
    ("messages", "expected_status"),
    [
        (['{"status":"ready"}', '{"error":"worker failed"}'], 1),
        (['{"status":"ready"}', asyncio.TimeoutError()], 1),
        (
            [
                '{"status":"ready"}',
                json.dumps({"result": {"text": "ok"}, "is_final": True}),
            ],
            0,
        ),
    ],
)
@pytest.mark.asyncio(scope="session")
async def test_websocket_client_exit_status_matches_final_result(
    tmp_path, monkeypatch, capsys, messages, expected_status
):
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"sample")
    connection = _WebSocketStub(messages)
    websockets_stub = SimpleNamespace(
        connect=MagicMock(return_value=connection)
    )
    module = load_module_from_path(
        "ws_client_under_test",
        "test_ws_client.py",
        stubs={"websockets": websockets_stub},
        monkeypatch=monkeypatch,
    )

    status = await module.test_streaming("key", "asr", str(audio_file))

    assert status == expected_status
    output = capsys.readouterr().out
    assert ("FINAL_RESULT=yes" in output) == (expected_status == 0)
