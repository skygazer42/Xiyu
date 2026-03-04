from unittest.mock import Mock
import sys

import numpy as np


def test_whisper_backend_transcribe_pcm_bytes(monkeypatch):
    fake_whisper = Mock()
    fake_model = Mock()
    fake_model.transcribe.return_value = {
        "text": "hello",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "hello"},
        ],
    }
    fake_whisper.load_model.return_value = fake_model

    # Ensure the backend imports our stub instead of a real dependency.
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    from src.models.backends.whisper import WhisperBackend

    backend = WhisperBackend(model="small", device="cuda", language="zh")
    backend.load()

    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    out = backend.transcribe(pcm)

    assert out["text"] == "hello"
    assert out["sentence_info"] == [{"text": "hello", "start": 0, "end": 1000}]


def test_whisper_backend_hotwords_are_passed_via_initial_prompt(monkeypatch):
    fake_whisper = Mock()
    fake_model = Mock()
    fake_model.transcribe.return_value = {"text": "ok", "segments": []}
    fake_whisper.load_model.return_value = fake_model

    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    from src.models.backends.whisper import WhisperBackend

    backend = WhisperBackend(model="small", device="cuda", language="zh")
    backend.load()

    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    _ = backend.transcribe(pcm, hotwords="OpenAI\nXiyu")

    called_kwargs = fake_model.transcribe.call_args.kwargs
    assert "initial_prompt" in called_kwargs
    prompt = str(called_kwargs["initial_prompt"])
    assert "专有名词" in prompt
    assert "OpenAI" in prompt
    assert "Xiyu" in prompt
