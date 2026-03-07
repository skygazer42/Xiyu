import asyncio
import sys
import types
import importlib.util
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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

import src.core.engine as engine_mod


@pytest.fixture
def mock_model_manager():
    with patch.object(engine_mod, "model_manager") as mock_mm:
        # Engine uses model_manager.backend.transcribe(...) as the primary path.
        mock_backend = MagicMock()
        mock_backend.supports_speaker = True
        mock_backend.transcribe.return_value = {
            "text": "买当劳很好吃",
            "sentence_info": [{"text": "买当劳很好吃", "start": 0, "end": 1500, "spk": 0}],
        }
        mock_mm.backend = mock_backend
        yield mock_mm


def test_engine_initialization(mock_model_manager):
    """测试引擎初始化"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    assert engine is not None
    assert engine.corrector is not None
    assert engine.speaker_labeler is not None


def test_transcribe_basic(mock_model_manager):
    """测试基本转写"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()

    result = engine.transcribe(b"fake_audio")

    assert result is not None
    assert "text" in result
    assert "sentences" in result


def test_transcribe_with_hotword(mock_model_manager):
    """测试带热词纠错的转写"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    engine.update_hotwords(["麦当劳", "肯德基"])

    result = engine.transcribe(b"fake_audio", apply_hotword=True)

    # 热词纠错应该将 "买当劳" 替换为 "麦当劳"
    assert "麦当劳" in result["text"]


def test_transcribe_with_speaker(mock_model_manager):
    """测试带说话人识别的转写"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()

    result = engine.transcribe(b"fake_audio", with_speaker=True)

    assert len(result["sentences"]) == 1
    assert "speaker" in result["sentences"][0]
    assert result["sentences"][0]["speaker"] == "说话人甲"


def test_update_hotwords(mock_model_manager):
    """测试更新热词"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()

    engine.update_hotwords(["Claude", "Bilibili"])

    assert engine._hotwords_loaded is True
    assert len(engine.corrector.hotwords) == 2


def test_transcribe_generates_transcript(mock_model_manager):
    """测试生成格式化转写稿"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()

    result = engine.transcribe(b"fake_audio", with_speaker=True)

    assert "transcript" in result
    assert "说话人甲" in result["transcript"]


def test_transcribe_without_hotword(mock_model_manager):
    """测试禁用热词纠错"""
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    engine.update_hotwords(["麦当劳"])

    result = engine.transcribe(b"fake_audio", apply_hotword=False)

    # 禁用热词时应该保留原文
    assert result["text"] == "买当劳很好吃"


def test_transcribe_auto_async_routes_by_chunk_duration_threshold(mock_model_manager):
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "short"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "long"})  # type: ignore[method-assign]

    # 10s PCM16LE (16kHz mono, 2 bytes/sample)
    audio_bytes = b"\x00" * (10 * 16000 * 2)

    out = asyncio.run(
        engine.transcribe_auto_async(audio_bytes, asr_options={"chunking": {"max_chunk_duration_s": 5.0}})
    )

    assert out["text"] == "long"
    engine.transcribe_long_audio.assert_called_once()
    engine.transcribe_async.assert_not_awaited()


def test_transcribe_auto_async_chunking_override_can_lower_threshold(mock_model_manager):
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "short"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "long"})  # type: ignore[method-assign]

    # 2s PCM16LE
    audio_bytes = b"\x00" * (2 * 16000 * 2)

    # Global threshold is high, but request explicitly sets max_chunk_duration_s small.
    out = asyncio.run(engine.transcribe_auto_async(audio_bytes, asr_options={"chunking": {"max_chunk_duration_s": 1}}))

    assert out["text"] == "long"
    engine.transcribe_long_audio.assert_called_once()
    engine.transcribe_async.assert_not_awaited()


def test_transcribe_auto_async_with_speaker_skips_chunking(mock_model_manager):
    from src.core.engine import TranscriptionEngine

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "direct"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "chunked"})  # type: ignore[method-assign]

    # 120s PCM16LE
    audio_bytes = b"\x00" * (120 * 16000 * 2)

    out = asyncio.run(engine.transcribe_auto_async(audio_bytes, with_speaker=True))

    assert out["text"] == "direct"
    engine.transcribe_async.assert_awaited_once()
    engine.transcribe_long_audio.assert_not_called()


def test_transcribe_async_with_speaker_forces_external_diarizer_when_enabled(mock_model_manager, monkeypatch):
    from src.core.engine import TranscriptionEngine

    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_enable", True, raising=False)
    monkeypatch.setattr(
        engine_mod.settings, "speaker_external_diarizer_base_url", "http://diar:8000", raising=False
    )

    async def fake_fetch(*args, **kwargs):
        return [
            {"spk": 0, "start": 0, "end": 1000},
            {"spk": 1, "start": 1000, "end": 2000},
        ]

    # Make native speaker output clearly different so the test catches regressions.
    mock_model_manager.backend.transcribe.side_effect = [
        {"text": "第一段", "sentence_info": []},
        {"text": "第二段", "sentence_info": []},
    ]

    # 2 seconds of PCM16LE @16kHz mono
    audio_bytes = b"\x00" * (2 * 16000 * 2)

    with patch.object(engine_mod, "fetch_diarizer_segments", new=fake_fetch):
        engine = TranscriptionEngine()
        out = asyncio.run(engine.transcribe_async(audio_bytes, with_speaker=True, apply_hotword=False))

    assert out.get("speaker_turns")
    assert out.get("transcript")
    assert mock_model_manager.backend.transcribe.call_count == 2
    for c in mock_model_manager.backend.transcribe.call_args_list:
        assert c.kwargs.get("with_speaker") is False


def test_transcribe_with_speaker_unsupported_backend_can_be_ignored(mock_model_manager, monkeypatch):
    from src.core.engine import TranscriptionEngine

    mock_model_manager.backend.supports_speaker = False
    mock_model_manager.backend.transcribe.return_value = {
        "text": "你好世界",
        "sentence_info": [{"text": "你好世界", "start": 0, "end": 1000}],
    }

    # New config: allow per-port deployments to ignore unsupported diarization.
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)

    engine = TranscriptionEngine()

    out = engine.transcribe(b"fake_audio", with_speaker=True)

    assert out["text"] == "你好世界"
    assert out["sentences"] == [{"text": "你好世界", "start": 0, "end": 1000}]
    assert "speaker_turns" not in out
    assert "transcript" not in out

    assert mock_model_manager.backend.transcribe.call_count == 1
    kwargs = mock_model_manager.backend.transcribe.call_args.kwargs
    assert kwargs.get("with_speaker") is False


def test_transcribe_auto_async_with_speaker_ignored_can_still_chunk_long_audio(mock_model_manager, monkeypatch):
    from src.core.engine import TranscriptionEngine

    mock_model_manager.backend.supports_speaker = False
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "direct"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "chunked"})  # type: ignore[method-assign]

    audio_bytes = b"\x00" * (120 * 16000 * 2)

    out = asyncio.run(engine.transcribe_auto_async(audio_bytes, with_speaker=True))

    assert out["text"] == "chunked"
    engine.transcribe_long_audio.assert_called_once()
    engine.transcribe_async.assert_not_awaited()


def test_transcribe_auto_async_with_speaker_and_fallback_enabled_uses_transcribe_async(mock_model_manager, monkeypatch):
    from src.core.engine import TranscriptionEngine

    mock_model_manager.backend.supports_speaker = False
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_fallback_diarization_enable", True, raising=False)
    monkeypatch.setattr(
        engine_mod.settings, "speaker_fallback_diarization_base_url", "http://diar:8000", raising=False
    )

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "direct"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "chunked"})  # type: ignore[method-assign]

    audio_bytes = b"\x00" * (120 * 16000 * 2)

    out = asyncio.run(engine.transcribe_auto_async(audio_bytes, with_speaker=True))

    assert out["text"] == "direct"
    engine.transcribe_async.assert_awaited_once()
    kwargs = engine.transcribe_async.await_args.kwargs
    assert kwargs.get("with_speaker") is True
    engine.transcribe_long_audio.assert_not_called()


def test_transcribe_auto_async_with_speaker_and_external_diarizer_enabled_uses_transcribe_async(
    mock_model_manager, monkeypatch
):
    from src.core.engine import TranscriptionEngine

    mock_model_manager.backend.supports_speaker = False
    monkeypatch.setattr(engine_mod.settings, "speaker_unsupported_behavior", "ignore", raising=False)
    monkeypatch.setattr(engine_mod.settings, "speaker_external_diarizer_enable", True, raising=False)
    monkeypatch.setattr(
        engine_mod.settings, "speaker_external_diarizer_base_url", "http://diar:8000", raising=False
    )

    engine = TranscriptionEngine()
    engine.transcribe_async = AsyncMock(return_value={"text": "direct"})  # type: ignore[method-assign]
    engine.transcribe_long_audio = Mock(return_value={"text": "chunked"})  # type: ignore[method-assign]

    audio_bytes = b"\x00" * (120 * 16000 * 2)

    out = asyncio.run(engine.transcribe_auto_async(audio_bytes, with_speaker=True))

    assert out["text"] == "direct"
    engine.transcribe_async.assert_awaited_once()
    kwargs = engine.transcribe_async.await_args.kwargs
    assert kwargs.get("with_speaker") is True
    engine.transcribe_long_audio.assert_not_called()


def test_request_post_processor_inherits_global_acronym_merge_default(mock_model_manager, monkeypatch):
    from src.core.engine import TranscriptionEngine

    monkeypatch.setattr(engine_mod.settings, "acronym_merge_enable", True, raising=False)

    engine = TranscriptionEngine()

    # Non-empty postprocess override forces request-scoped postprocessor creation.
    pp = engine._get_request_post_processor(asr_options={"postprocess": {"punc_merge_enable": False}})

    assert pp.process("A I 技术") == "AI 技术"


def test_transcribe_long_audio_post_process_runs_after_merge_itn_regression(mock_model_manager, monkeypatch):
    """Regression: post-processing must run AFTER merge for long audio.

    If ITN is applied per chunk, merging chunk texts like "一百" + "零一" would become
    "100" + "01" => "10001" (wrong). The correct behavior is merge first ("一百零一"),
    then ITN => "101".
    """
    import numpy as np

    from src.core.audio.chunker import AudioChunker
    from src.core.engine import TranscriptionEngine

    mock_model_manager.backend.supports_speaker = True
    mock_model_manager.backend.transcribe.side_effect = [
        {"text": "一百", "sentence_info": []},
        {"text": "零一", "sentence_info": []},
    ]

    engine = TranscriptionEngine()

    chunker = AudioChunker(max_chunk_duration=0.5, min_chunk_duration=0.1, overlap_duration=0.0)

    # Force a deterministic 2-chunk split without involving silence detection.
    chunk1 = np.zeros(16000, dtype=np.float32)
    chunk2 = np.zeros(16000, dtype=np.float32)
    chunker.split = Mock(
        return_value=[
            (chunk1, 0, 16000),
            (chunk2, 16000, 32000),
        ]
    )

    monkeypatch.setattr(engine, "_get_request_chunker", lambda _opts: chunker)

    # 2s float32 audio -> long audio relative to max_chunk_duration=0.5s.
    audio = np.zeros(2 * 16000, dtype=np.float32)
    out = engine.transcribe_long_audio(
        audio,
        apply_hotword=True,  # enables post_process (ITN) in correction pipeline
        max_workers=1,
        asr_options={"chunking": {"max_chunk_duration_s": 0.5, "overlap_chars": 0}},
    )

    assert out["raw_text"] == "一百零一"
    assert out["text"] == "101"


def test_transcribe_long_audio_checkpoint_resume_skips_completed_chunks(mock_model_manager, monkeypatch, tmp_path):
    """Checkpoint resume should skip chunks that already succeeded.

    This is critical for 3-4h meetings where jobs can be interrupted (restart /
    network / OOM) and must resume without redoing completed chunks.
    """
    import json
    import numpy as np

    from src.core.audio.chunker import AudioChunker
    from src.core.engine import TranscriptionEngine

    # Backend is mocked; we only want it to be called for the missing chunk.
    mock_model_manager.backend.supports_speaker = True
    mock_model_manager.backend.transcribe.side_effect = [
        {"text": "第二段", "sentence_info": []},
    ]

    engine = TranscriptionEngine()

    # Force a deterministic 2-chunk split.
    chunker = AudioChunker(max_chunk_duration=0.5, min_chunk_duration=0.1, overlap_duration=0.0)
    chunk1 = np.zeros(16000, dtype=np.float32)
    chunk2 = np.zeros(16000, dtype=np.float32)
    chunker.split = Mock(
        return_value=[
            (chunk1, 0, 16000),
            (chunk2, 16000, 32000),
        ]
    )
    monkeypatch.setattr(engine, "_get_request_chunker", lambda _opts: chunker)

    # Pre-write checkpoint for chunk 0.
    job_id = "job1"
    job_dir = tmp_path / job_id
    chunks_dir = job_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    (chunks_dir / "000000.json").write_text(
        json.dumps(
            {
                "idx": 0,
                "start_sample": 0,
                "end_sample": 16000,
                "success": True,
                "result": {"text": "第一段", "sentences": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 2s float32 audio -> long audio relative to max_chunk_duration=0.5s.
    audio = np.zeros(2 * 16000, dtype=np.float32)
    out = engine.transcribe_long_audio(
        audio,
        apply_hotword=False,
        max_workers=1,
        asr_options={
            "chunking": {
                "max_chunk_duration_s": 0.5,
                "overlap_chars": 0,
                "checkpoint_enable": True,
                "checkpoint_dir": str(tmp_path),
                "checkpoint_id": job_id,
                "resume_skip_existing": True,
            }
        },
    )

    assert out["raw_text"] == "第一段第二段"
    assert mock_model_manager.backend.transcribe.call_count == 1
    assert (chunks_dir / "000001.json").exists()
