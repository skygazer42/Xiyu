import sys
import types
from types import SimpleNamespace

import numpy as np


def _install_faster_whisper_stub(monkeypatch):
    """Install a lightweight `faster_whisper` stub so unit tests don't load real models."""

    captured = {"last_kwargs": None}

    class FakeWhisperModel:
        def __init__(
            self,
            model_size_or_path: str,
            device: str = "auto",
            compute_type: str = "default",
            cpu_threads: int = 0,
            num_workers: int = 1,
            download_root: str | None = None,
            **_kwargs,
        ):
            self.model_size_or_path = model_size_or_path
            self.device = device
            self.compute_type = compute_type
            self.cpu_threads = cpu_threads
            self.num_workers = num_workers
            self.download_root = download_root

        def transcribe(
            self,
            audio,
            *,
            language=None,
            beam_size=None,
            best_of=None,
            temperature=None,
            vad_filter=None,
            vad_parameters=None,
            initial_prompt=None,
            hotwords=None,
            word_timestamps=None,
            **_kwargs,
        ):
            captured["last_kwargs"] = {
                "language": language,
                "beam_size": beam_size,
                "best_of": best_of,
                "temperature": temperature,
                "vad_filter": vad_filter,
                "vad_parameters": vad_parameters,
                "initial_prompt": initial_prompt,
                "hotwords": hotwords,
                "word_timestamps": word_timestamps,
            }

            seg = SimpleNamespace(text="hello", start=0.0, end=1.0)
            info = SimpleNamespace(language="en", duration=1.0, language_probability=0.9)
            return iter([seg]), info

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return captured


def test_whisper_backend_transcribe_pcm_bytes(monkeypatch):
    captured = _install_faster_whisper_stub(monkeypatch)

    from src.models.backends.whisper import WhisperBackend

    backend = WhisperBackend(model="small", device="cpu", language="zh", vad_filter=False)
    backend.load()

    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    out = backend.transcribe(pcm)

    assert out["text"] == "hello"
    assert out["sentence_info"] == [{"text": "hello", "start": 0, "end": 1000}]
    assert isinstance(captured["last_kwargs"], dict)


def test_whisper_backend_hotwords_are_passed_via_initial_prompt(monkeypatch):
    captured = _install_faster_whisper_stub(monkeypatch)

    from src.models.backends.whisper import WhisperBackend

    backend = WhisperBackend(model="small", device="cpu", language="zh", vad_filter=False)
    backend.load()

    pcm = (np.zeros(16000, dtype=np.int16)).tobytes()
    _ = backend.transcribe(pcm, hotwords="OpenAI\nXiyu")

    called_kwargs = captured["last_kwargs"]
    assert called_kwargs is not None
    assert "initial_prompt" in called_kwargs
    prompt = str(called_kwargs["initial_prompt"])
    assert "专有名词" in prompt
    assert "OpenAI" in prompt
    assert "Xiyu" in prompt

    # Do not pass faster-whisper's `hotwords` hint: it can trigger decoding-length
    # errors on longer audio with many injected terms. Keep injection via initial_prompt.
    assert called_kwargs.get("hotwords") in (None, "")
