import asyncio
import importlib.util
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_optional_dependency_stubs_installed() -> None:
    # These modules are imported at module-import time by the production code,
    # but aren't needed for these unit tests (we mock the backend).
    if "funasr" not in sys.modules and importlib.util.find_spec("funasr") is None:
        funasr_stub = types.ModuleType("funasr")

        class DummyAutoModel:
            def __init__(self, *args, **kwargs):
                pass

            def generate(self, **kwargs):
                return []

        funasr_stub.AutoModel = DummyAutoModel
        sys.modules["funasr"] = funasr_stub

    if "numba" not in sys.modules and importlib.util.find_spec("numba") is None:
        numba_stub = types.ModuleType("numba")

        def njit(*args, **kwargs):
            if args and callable(args[0]) and len(args) == 1 and not kwargs:
                return args[0]

            def decorator(func):
                return func

            return decorator

        numba_stub.njit = njit
        sys.modules["numba"] = numba_stub


_ensure_optional_dependency_stubs_installed()

import httpx

import src.core.engine as engine_mod


@pytest.fixture
def mock_model_manager():
    with patch.object(engine_mod, "model_manager") as mock_mm:
        backend = MagicMock()
        backend.get_info.return_value = {"name": "Qwen3Remote", "type": "qwen3"}
        backend.supports_speaker = False
        backend.supports_hotwords = False
        backend.supports_streaming = False
        backend.transcribe.side_effect = [
            {"text": "第一段", "sentence_info": []},
            {"text": "第二段", "sentence_info": []},
        ]
        mock_mm.backend = backend
        yield mock_mm


class _DummyHttpxResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_transcribe_async_speaker_fallback_diarization_happy_path(mock_model_manager, monkeypatch):
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_fallback_diarization_enable", True, raising=False)
    monkeypatch.setattr(
        engine_mod.settings, "speaker_fallback_diarization_base_url", "http://diar:8000", raising=False
    )

    # 2 seconds of PCM16LE @16kHz mono
    audio_bytes = b"\x00" * (2 * 16000 * 2)

    diar_payload = {
        "code": 0,
        "text": "ignored",
        "sentences": [
            {"text": "x", "start": 0, "end": 1000, "speaker": "说话人1", "speaker_id": 0},
            {"text": "y", "start": 1000, "end": 2000, "speaker": "说话人2", "speaker_id": 1},
        ],
        "speaker_turns": None,
        "transcript": None,
        "raw_text": "ignored",
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _DummyHttpxResponse(diar_payload)

        engine = engine_mod.TranscriptionEngine()
        out = asyncio.run(
            engine.transcribe_async(
                audio_bytes,
                with_speaker=True,
                apply_hotword=False,
                apply_llm=False,
            )
        )

    assert out["sentences"][0]["speaker"] == "说话人1"
    assert out["sentences"][0]["speaker_id"] == 0
    assert out["sentences"][0]["text"] == "第一段"
    assert out["sentences"][1]["speaker"] == "说话人2"
    assert out["sentences"][1]["text"] == "第二段"

    assert out.get("speaker_turns")
    assert out.get("transcript")
    assert "说话人1" in out["transcript"]

    assert mock_model_manager.backend.transcribe.call_count == 2
    for c in mock_model_manager.backend.transcribe.call_args_list:
        assert c.kwargs.get("with_speaker") is False


def test_transcribe_async_speaker_fallback_diarization_failure_falls_back_to_ignore(
    mock_model_manager, monkeypatch
):
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_fallback_diarization_enable", True, raising=False)
    monkeypatch.setattr(
        engine_mod.settings, "speaker_fallback_diarization_base_url", "http://diar:8000", raising=False
    )

    audio_bytes = b"\x00" * (2 * 16000 * 2)

    # When fallback fails, we should still return a normal transcript (no speakers),
    # instead of hard-failing the request.
    mock_model_manager.backend.transcribe.side_effect = [{"text": "普通文本", "sentence_info": []}]

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("timeout")

        engine = engine_mod.TranscriptionEngine()
        out = asyncio.run(
            engine.transcribe_async(
                audio_bytes,
                with_speaker=True,
                apply_hotword=False,
                apply_llm=False,
            )
    )

    assert out["text"] == "普通文本"
    # Even when speaker fallback fails, we should still return a normal transcript.
    # Backend may not provide sentence timestamps; engine can synthesize a single
    # sentence for UI timeline exports (best-effort).
    assert isinstance(out.get("sentences"), list)
    assert out["sentences"]
    assert out["sentences"][0]["text"] == "普通文本"
    assert "speaker_turns" not in out
    assert "transcript" not in out

    assert mock_model_manager.backend.transcribe.call_count == 1
    kwargs = mock_model_manager.backend.transcribe.call_args.kwargs
    assert kwargs.get("with_speaker") is False
