from unittest.mock import patch


def test_qwen3_remote_backend_calls_audio_transcriptions_and_returns_text():
    from src.models.backends.qwen3_remote import Qwen3RemoteBackend

    backend = Qwen3RemoteBackend(
        base_url="http://fake",
        model="Qwen/Qwen3-ASR-0.6B",
        api_key="EMPTY",
        timeout_s=1.0,
    )

    class Resp:
        status_code = 200

        def json(self):
            return {"text": "ok"}

        def raise_for_status(self):
            return None

    with patch("httpx.Client.post", return_value=Resp()) as post:
        out = backend.transcribe(b"\x00\x00" * 16000, hotwords="foo bar")
        assert out["text"] == "ok"
        assert "sentence_info" in out

        assert post.called
        called_url = post.call_args.args[0]
        assert called_url == "http://fake/v1/audio/transcriptions"
        called_data = post.call_args.kwargs["data"]
        assert called_data["model"] == "Qwen/Qwen3-ASR-0.6B"


def test_qwen3_remote_backend_formats_hotwords_as_a_context_hint():
    from src.models.backends.qwen3_remote import Qwen3RemoteBackend

    backend = Qwen3RemoteBackend(
        base_url="http://fake",
        model="Qwen/Qwen3-ASR-0.6B",
        api_key="EMPTY",
        timeout_s=1.0,
    )

    class Resp:
        status_code = 200

        def json(self):
            return {"text": "ok"}

        def raise_for_status(self):
            return None

    with patch("httpx.Client.post", return_value=Resp()) as post:
        _ = backend.transcribe(b"\x00\x00" * 16000, hotwords="OpenAI\nXiyu")
        called_data = post.call_args.kwargs["data"]
        prompt = called_data.get("prompt", "")
        assert "专有名词" in prompt
        assert "OpenAI" in prompt
        assert "Xiyu" in prompt
