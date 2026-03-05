import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock, MagicMock, AsyncMock
import io

@pytest.fixture
def client():
    import src.core.engine as engine_mod

    with patch.object(engine_mod, "model_manager") as mock_mm:
        # App lifespan triggers `transcription_engine.warmup()`, which uses
        # `model_manager.backend`. Mock backend to avoid loading real models.
        mock_backend = MagicMock()
        mock_backend.get_info.return_value = {"name": "MockBackend", "type": "mock"}
        mock_backend.warmup.return_value = None
        mock_backend.supports_speaker = True
        mock_backend.transcribe.return_value = {
            "text": "你好世界",
            "sentence_info": [{"text": "你好世界", "start": 0, "end": 1000}]
        }
        mock_mm.backend = mock_backend

        from src.main import app
        with TestClient(app) as c:
            yield c

def test_health_check(client):
    """测试健康检查接口"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"

def test_root_endpoint(client):
    """测试根路径"""
    response = client.get("/")
    assert response.status_code == 200
    assert "name" in response.json()


def test_backend_info_endpoint(client):
    response = client.get("/api/v1/backend")
    assert response.status_code == 200
    data = response.json()

    assert data["backend"]
    assert isinstance(data["info"], dict)

    caps = data["capabilities"]
    assert caps["supports_speaker"] is True
    assert caps["supports_streaming"] is False
    assert caps["supports_hotwords"] is False
    assert caps["supports_speaker_fallback"] is False
    assert caps["supports_speaker_external"] is False
    assert caps["speaker_strategy"] == "native"

    assert data["speaker_unsupported_behavior"] in {"error", "fallback", "ignore"}

def test_transcribe_endpoint(client):
    """测试转写接口"""
    # Route uses `await transcription_engine.transcribe_auto_async(...)`, so patch that
    # symbol where it is imported/used (src.api.routes.transcribe).
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_transcribe_auto_async.return_value = {
            "text": "你好世界",
            "sentences": [{"text": "你好世界", "start": 0, "end": 1000}],
            "raw_text": "你好世界"
        }

        async def fake_process(file, preprocess_options=None):
            yield b"\x00" * 32000
        mock_process.side_effect = fake_process

        audio_content = b"fake_audio_content_wav_header"
        files = {"file": ("test.wav", io.BytesIO(audio_content), "audio/wav")}

        response = client.post("/api/v1/transcribe", files=files)

        assert response.status_code == 200
        data = response.json()
        assert "text" in data
        assert "sentences" in data

def test_transcribe_with_speaker(client):
    """测试带说话人的转写"""
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_transcribe_auto_async.return_value = {
            "text": "你好",
            "sentences": [{"text": "你好", "start": 0, "end": 500, "speaker": "说话人甲", "speaker_id": 0}],
            "transcript": "[00:00 - 00:00] 说话人甲: 你好",
            "raw_text": "你好"
        }

        async def fake_process(file, preprocess_options=None):
            yield b"\x00" * 16000
        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        data = {"with_speaker": "true"}

        response = client.post("/api/v1/transcribe", files=files, data=data)

        assert response.status_code == 200
        result = response.json()
        assert "transcript" in result


def test_transcribe_include_srt(client):
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_transcribe_auto_async.return_value = {
            "text": "你好",
            "sentences": [{"text": "你好", "start": 0, "end": 500, "speaker": "说话人甲", "speaker_id": 0}],
            "transcript": "[00:00 - 00:00] 说话人甲: 你好",
            "raw_text": "你好"
        }

        async def fake_process(file, preprocess_options=None):
            yield b"\x00" * 16000
        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        data = {"with_speaker": "true", "include_srt": "true"}

        response = client.post("/api/v1/transcribe", files=files, data=data)

        assert response.status_code == 200
        result = response.json()
        assert isinstance(result.get("srt"), str)
        assert " --> " in result["srt"]
        # SRT timestamps use comma as milliseconds separator.
        assert "," in result["srt"]
        assert "[说话人甲]" in result["srt"]


def test_transcribe_all_models_includes_srt(client):
    with patch('src.api.routes.transcribe.transcribe_all_models', new_callable=AsyncMock) as mock_all, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_all.return_value = {
            "code": 0,
            "base_backend": "pytorch",
            "llm_used": True,
            "llm_role": "policy_meeting",
            "candidates": [],
            "final": {
                "code": 0,
                "text": "你好",
                "text_accu": None,
                "sentences": [{"text": "你好", "start": 0, "end": 500, "speaker": "说话人甲", "speaker_id": 0}],
                "speaker_turns": None,
                "transcript": "[00:00 - 00:00] 说话人甲: 你好",
                "raw_text": None,
                },
            }

        async def fake_process(file, preprocess_options=None):
            # 1s PCM16LE @ 16kHz mono (2 bytes/sample)
            yield b"\x00" * (16000 * 2)
        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        response = client.post("/api/v1/transcribe/all", files=files)

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body.get("final"), dict)
        assert isinstance(body["final"].get("srt"), str)
        assert " --> " in body["final"]["srt"]
        assert "[说话人甲]" in body["final"]["srt"]


def test_transcribe_asr_options_invalid_json(client):
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        async def fake_process(file, preprocess_options=None):
            yield b"\x00" * 16000
        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        data = {"asr_options": "{not json"}

        response = client.post("/api/v1/transcribe", files=files, data=data)
        assert response.status_code == 400
        assert "asr_options" in response.json().get("detail", "")

        mock_process.assert_not_called()
        mock_transcribe_auto_async.assert_not_awaited()


def test_transcribe_asr_options_is_passed_to_engine(client):
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_transcribe_auto_async.return_value = {
            "text": "你好世界",
            "sentences": [{"text": "你好世界", "start": 0, "end": 1000}],
            "raw_text": "你好世界"
        }

        async def fake_process(file, preprocess_options=None):
            yield b"\x00" * 32000
        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        asr_options = '{"chunking":{"max_workers":1,"overlap_chars":42}}'
        data = {"asr_options": asr_options}

        response = client.post("/api/v1/transcribe", files=files, data=data)
        assert response.status_code == 200

        mock_transcribe_auto_async.assert_awaited()
        kwargs = mock_transcribe_auto_async.await_args.kwargs
        assert kwargs["asr_options"] == {"chunking": {"max_workers": 1, "overlap_chars": 42}}


def test_transcribe_asr_options_preprocess_is_passed_to_decoder(client):
    with patch('src.api.routes.transcribe.transcription_engine.transcribe_auto_async', new_callable=AsyncMock) as mock_transcribe_auto_async, \
         patch('src.api.routes.transcribe.process_audio_file') as mock_process:
        mock_transcribe_auto_async.return_value = {
            "text": "你好世界",
            "sentences": [{"text": "你好世界", "start": 0, "end": 1000}],
            "raw_text": "你好世界"
        }

        async def fake_process(file, preprocess_options=None):
            assert preprocess_options == {"normalize_enable": False, "remove_dc_offset": False}
            yield b"\x00" * 32000

        mock_process.side_effect = fake_process

        files = {"file": ("test.wav", io.BytesIO(b"fake"), "audio/wav")}
        asr_options = '{"preprocess":{"normalize_enable":false,"remove_dc_offset":false}}'
        data = {"asr_options": asr_options}

        response = client.post("/api/v1/transcribe", files=files, data=data)
        assert response.status_code == 200

        mock_transcribe_auto_async.assert_awaited()

def test_transcribe_no_file(client):
    """测试无文件上传"""
    response = client.post("/api/v1/transcribe")
    assert response.status_code == 422  # Validation error


def test_transcribe_url_asr_options_invalid_json(client):
    with patch("src.api.routes.async_transcribe.task_manager.submit") as mock_submit:
        mock_submit.return_value = "task123"

        data = {"audio_url": "https://example.com/audio.wav", "asr_options": "{not json"}
        response = client.post("/api/v1/trans/url", data=data)

        assert response.status_code == 400
        assert "asr_options" in response.json().get("detail", "")
        mock_submit.assert_not_called()


def test_transcribe_url_asr_options_is_passed_to_task_manager(client):
    with patch("src.api.routes.async_transcribe.task_manager.submit") as mock_submit:
        mock_submit.return_value = "task123"

        data = {
            "audio_url": "https://example.com/audio.wav",
            "asr_options": '{"chunking":{"max_workers":1,"overlap_chars":42}}',
        }
        response = client.post("/api/v1/trans/url", data=data)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["data"]["task_id"] == "task123"

        mock_submit.assert_called_once()
        args, kwargs = mock_submit.call_args
        assert kwargs == {}
        assert args[0] == "url_transcribe"
        payload = args[1]
        assert payload["asr_options"] == {"chunking": {"max_workers": 1, "overlap_chars": 42}}


def test_transcribe_video_asr_options_invalid_json(client):
    with (
        patch("src.api.routes.async_transcribe.extract_audio_from_video") as mock_extract,
        patch(
            "src.api.routes.async_transcribe.transcription_engine.transcribe_auto_async",
            new_callable=AsyncMock,
        ) as mock_transcribe_auto_async,
        patch(
            "src.api.routes.async_transcribe.process_audio_file",
            create=True,
        ) as mock_process_audio_file,
    ):
        mock_extract.return_value = True

        files = {"file": ("test.mp4", io.BytesIO(b"fake_video"), "video/mp4")}
        data = {"asr_options": "{not json"}
        response = client.post("/api/v1/trans/video", files=files, data=data)

        assert response.status_code == 400
        assert "asr_options" in response.json().get("detail", "")

        mock_transcribe_auto_async.assert_not_awaited()
        mock_process_audio_file.assert_not_called()


def test_transcribe_video_passes_hotwords_and_asr_options_and_returns_speaker_turns(client):
    engine_result = {
        "text": "你好",
        "text_accu": "你好",
        "sentences": [{"text": "你好", "start": 0, "end": 500, "speaker": "说话人1", "speaker_id": 0}],
        "speaker_turns": [
            {
                "speaker": "说话人1",
                "speaker_id": 0,
                "start": 0,
                "end": 500,
                "text": "你好",
                "sentence_count": 1,
            }
        ],
        "transcript": "[00:00 - 00:00] 说话人1: 你好",
        "raw_text": "你好",
    }

    async def fake_process(file, preprocess_options=None):
        assert preprocess_options == {"normalize_enable": False}
        yield b"\x00" * 16000

    with (
        patch("src.api.routes.async_transcribe.extract_audio_from_video") as mock_extract,
        patch(
            "src.api.routes.async_transcribe.transcription_engine.transcribe_auto_async",
            new_callable=AsyncMock,
        ) as mock_transcribe_auto_async,
        patch(
            "src.api.routes.async_transcribe.process_audio_file",
            create=True,
        ) as mock_process_audio_file,
    ):
        def _fake_extract(_in: str, out: str) -> bool:
            # Ensure the "extracted" file exists for the current implementation.
            with open(out, "wb") as f:
                f.write(b"fake_wav_bytes")
            return True

        mock_extract.side_effect = _fake_extract
        mock_transcribe_auto_async.return_value = engine_result
        mock_process_audio_file.side_effect = fake_process

        files = {"file": ("test.mp4", io.BytesIO(b"fake_video"), "video/mp4")}
        asr_options = '{"preprocess":{"normalize_enable":false},"speaker":{"label_style":"numeric"}}'
        data = {
            "with_speaker": "true",
            "apply_hotword": "true",
            "apply_llm": "false",
            "llm_role": "default",
            "hotwords": "张三 李四",
            "asr_options": asr_options,
        }

        response = client.post("/api/v1/trans/video", files=files, data=data)
        assert response.status_code == 200

        body = response.json()
        assert body["code"] == 0
        assert body["text"] == "你好"
        assert body["speaker_turns"][0]["speaker_id"] == 0

        mock_transcribe_auto_async.assert_awaited()
        kwargs = mock_transcribe_auto_async.await_args.kwargs
        assert kwargs["hotwords"] == "张三 李四"
        assert kwargs["asr_options"] == {"preprocess": {"normalize_enable": False}, "speaker": {"label_style": "numeric"}}
