import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import WebSocketDisconnect

def test_ws_connection_state():
    """测试 WebSocket 连接状态管理"""
    from src.api.ws_manager import ConnectionState

    state = ConnectionState()
    assert state.is_speaking == False
    assert state.asr_cache == {}

    state.is_speaking = True
    assert state.is_speaking == True

def test_ws_manager_add_remove():
    """测试连接管理器添加/移除"""
    from src.api.ws_manager import WebSocketManager

    manager = WebSocketManager()
    mock_ws = Mock()

    manager.connect(mock_ws, "test-id")
    assert "test-id" in manager.connections

    manager.disconnect("test-id")
    assert "test-id" not in manager.connections

def test_ws_manager_get_state():
    """测试获取连接状态"""
    from src.api.ws_manager import WebSocketManager

    manager = WebSocketManager()
    mock_ws = Mock()

    manager.connect(mock_ws, "test-id")
    state = manager.get_state("test-id")

    assert state is not None
    assert state.is_speaking == False

def test_connection_state_reset():
    """测试状态重置"""
    from src.api.ws_manager import ConnectionState

    state = ConnectionState()
    state.is_speaking = True
    state.asr_cache = {"key": "value"}

    state.reset()

    assert state.is_speaking == False
    assert state.asr_cache == {}

def test_connection_state_defaults():
    """测试默认值"""
    from src.api.ws_manager import ConnectionState

    state = ConnectionState()

    assert state.mode == "2pass"
    assert state.chunk_interval == 10
    assert state.hotwords is None


def test_ws_asr_online_runs_generate_off_event_loop_thread(monkeypatch):
    import src.api.routes.websocket as ws_mod
    from src.api.ws_manager import ConnectionState

    main_thread = threading.current_thread()
    observed = {}

    class DummyOnlineModel:
        def generate(self, **kwargs):
            observed["thread"] = threading.current_thread()
            return [{"text": "partial", "cache": {"seen": True}}]

    monkeypatch.setattr(
        ws_mod,
        "model_manager",
        SimpleNamespace(loader=SimpleNamespace(asr_model_online=DummyOnlineModel())),
    )

    state = ConnectionState()
    state.is_speaking = True

    result = asyncio.run(ws_mod._asr_online(b"\x00" * 16, state))

    assert result == {"text": "partial"}
    assert state.asr_cache == {"seen": True}
    assert observed["thread"] is not main_thread


def test_ws_asr_offline_runs_backend_transcribe_off_event_loop_thread(monkeypatch):
    import src.api.routes.websocket as ws_mod
    from src.api.ws_manager import ConnectionState

    main_thread = threading.current_thread()
    observed = {}

    class DummyBackend:
        supports_streaming = False

        def get_info(self):
            return {"name": "RouterBackend", "type": "router"}

        def transcribe(self, audio_input, hotwords=None):
            observed["thread"] = threading.current_thread()
            observed["audio_input"] = audio_input
            observed["hotwords"] = hotwords
            return {"text": "final"}

    monkeypatch.setattr(
        ws_mod,
        "model_manager",
        SimpleNamespace(backend=DummyBackend()),
    )

    state = ConnectionState()
    state.hotwords = "政务 会议"

    result = asyncio.run(ws_mod._asr_offline(b"\x01" * 32, state))

    assert result == {"text": "final"}
    assert observed["audio_input"] == b"\x01" * 32
    assert observed["hotwords"] == "政务 会议"
    assert observed["thread"] is not main_thread


def test_ws_asr_offline_handles_backend_info_without_type(monkeypatch):
    import src.api.routes.websocket as ws_mod
    from src.api.ws_manager import ConnectionState

    class DummyBackend:
        supports_streaming = False

        def get_info(self):
            return {"name": "RouterBackend"}

        def transcribe(self, audio_input, hotwords=None):
            return {"text": "final"}

    monkeypatch.setattr(
        ws_mod,
        "model_manager",
        SimpleNamespace(backend=DummyBackend()),
    )

    state = ConnectionState()

    result = asyncio.run(ws_mod._asr_offline(b"\x02" * 24, state))

    assert result == {"text": "final"}


def test_ws_realtime_flushes_final_result_when_is_speaking_turns_false(monkeypatch):
    import src.api.routes.websocket as ws_mod

    class FakeWebSocket:
        def __init__(self, messages):
            self._messages = iter(messages)
            self.sent = []
            self.accepted = False

        async def accept(self):
            self.accepted = True

        async def receive(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise WebSocketDisconnect(code=1000) from exc

        async def send_json(self, data):
            self.sent.append(data)

    async def fake_asr_online(audio_in, state):
        return {}

    async def fake_asr_offline(audio_in, state):
        return {"text": "最终文本"}

    monkeypatch.setattr(ws_mod.settings, "ws_heartbeat_interval", 0, raising=False)
    monkeypatch.setattr(ws_mod.settings, "stream_dedup_enable", False, raising=False)
    monkeypatch.setattr(ws_mod.transcription_engine, "_hotwords_loaded", False, raising=False)
    monkeypatch.setattr(ws_mod, "_check_streaming_support", lambda: True)
    monkeypatch.setattr(ws_mod, "_asr_online", fake_asr_online)
    monkeypatch.setattr(ws_mod, "_asr_offline", fake_asr_offline)

    fake_ws = FakeWebSocket(
        [
            {"text": json.dumps({"mode": "2pass", "is_speaking": True, "chunk_interval": 99})},
            {"bytes": b"\x00" * 8},
            {"text": json.dumps({"is_speaking": False})},
        ]
    )

    asyncio.run(ws_mod.websocket_realtime(fake_ws))

    assert fake_ws.accepted is True
    assert any(msg.get("type") == "connected" for msg in fake_ws.sent)
    assert any(msg.get("is_final") is True and msg.get("text") == "最终文本" for msg in fake_ws.sent)


def test_ws_realtime_ignores_runtime_disconnect_after_client_close(monkeypatch):
    import src.api.routes.websocket as ws_mod

    class FakeWebSocket:
        def __init__(self, messages):
            self._messages = iter(messages)
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise RuntimeError('Cannot call "receive" once a disconnect message has been received.') from exc

        async def send_json(self, data):
            self.sent.append(data)

    async def fake_asr_online(audio_in, state):
        return {}

    async def fake_asr_offline(audio_in, state):
        return {"text": "最终文本"}

    error_mock = Mock()
    monkeypatch.setattr(ws_mod.settings, "ws_heartbeat_interval", 0, raising=False)
    monkeypatch.setattr(ws_mod.settings, "stream_dedup_enable", False, raising=False)
    monkeypatch.setattr(ws_mod.transcription_engine, "_hotwords_loaded", False, raising=False)
    monkeypatch.setattr(ws_mod, "_check_streaming_support", lambda: True)
    monkeypatch.setattr(ws_mod, "_asr_online", fake_asr_online)
    monkeypatch.setattr(ws_mod, "_asr_offline", fake_asr_offline)
    monkeypatch.setattr(ws_mod.logger, "error", error_mock)

    fake_ws = FakeWebSocket(
        [
            {"text": json.dumps({"mode": "2pass", "is_speaking": True, "chunk_interval": 99})},
            {"bytes": b"\x00" * 8},
            {"text": json.dumps({"is_speaking": False})},
        ]
    )

    asyncio.run(ws_mod.websocket_realtime(fake_ws))

    assert any(msg.get("is_final") is True and msg.get("text") == "最终文本" for msg in fake_ws.sent)
    error_mock.assert_not_called()
