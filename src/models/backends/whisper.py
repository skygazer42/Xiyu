"""Local Whisper backend (faster-whisper).

We use `faster-whisper` (CTranslate2) for faster and more memory-efficient Whisper
inference while keeping Xiyu's standard backend contract:
  - returns {"text": str, "sentence_info": [{text,start,end}, ...]}

Input handling:
  - Xiyu's API converts uploads into 16kHz mono PCM16LE bytes.
  - faster-whisper accepts either a file path or a float waveform (16kHz), so we
    convert raw PCM bytes into float32 waveform when needed.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Dict, Optional, List

import numpy as np

from .base import ASRBackend

logger = logging.getLogger(__name__)


class WhisperBackend(ASRBackend):
    """Local Whisper backend using `faster-whisper`."""

    def __init__(
        self,
        *,
        model: str = "large-v3",
        device: str = "cuda",
        language: Optional[str] = "zh",
        download_root: str = "",
        compute_type: str = "",
        cpu_threads: int = 0,
        num_workers: int = 1,
        # Decoding options (accuracy-first defaults for meetings).
        beam_size: int = 5,
        best_of: int = 5,
        temperature: float = 0.0,
        # Voice activity detection (helps reduce silence hallucinations).
        vad_filter: bool = True,
        vad_min_silence_duration_ms: int = 500,
        word_timestamps: bool = False,
        **_kwargs: Any,
    ) -> None:
        self.model = str(model or "large-v3")
        self.device = str(device or "cuda")
        self.language = str(language) if language is not None else None
        self.download_root = str(download_root or "")
        ct = str(compute_type or "").strip()
        if not ct:
            # Reasonable defaults per device.
            ct = "float16" if str(self.device).strip().lower().startswith("cuda") else "int8"
        self.compute_type = ct
        self.cpu_threads = int(cpu_threads or 0)
        self.num_workers = int(num_workers or 1)
        self.beam_size = int(beam_size or 0) or 5
        self.best_of = int(best_of or 0) or 5
        self.temperature = float(temperature or 0.0)
        self.vad_filter = bool(vad_filter)
        self.vad_min_silence_duration_ms = int(vad_min_silence_duration_ms or 0) or 500
        self.word_timestamps = bool(word_timestamps)

        self._loaded = False
        self._model = None

    @property
    def supports_streaming(self) -> bool:
        return False

    @property
    def supports_speaker(self) -> bool:
        return False

    @property
    def supports_hotwords(self) -> bool:
        # Best-effort: we pass hotwords into `hotwords` (preferred) and/or initial_prompt.
        return True

    def load(self) -> None:
        if self._loaded and self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Whisper backend requires faster-whisper: pip install faster-whisper"
            ) from e

        model_name = str(self.model or "").strip()
        # Backward-compat: older configs used "large" (openai-whisper). For faster-whisper,
        # prefer explicit variants.
        if model_name.lower() == "large":
            model_name = "large-v3"

        logger.info(
            "Loading faster-whisper model=%s device=%s compute_type=%s",
            model_name,
            self.device,
            self.compute_type,
        )

        init_kwargs: Dict[str, Any] = {"device": self.device}
        if self.compute_type:
            init_kwargs["compute_type"] = self.compute_type
        if self.download_root.strip():
            init_kwargs["download_root"] = self.download_root.strip()
        if self.cpu_threads > 0:
            init_kwargs["cpu_threads"] = int(self.cpu_threads)
        if self.num_workers > 0:
            init_kwargs["num_workers"] = int(self.num_workers)

        # Be robust to faster-whisper version differences.
        sig = inspect.signature(WhisperModel.__init__)
        safe_kwargs = {k: v for k, v in init_kwargs.items() if k in sig.parameters}

        self._model = WhisperModel(model_name, **safe_kwargs)  # type: ignore[call-arg]
        self._loaded = True

    def unload(self) -> None:
        self._model = None
        self._loaded = False

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        self.load()
        assert self._model is not None

        if with_speaker:
            logger.debug("Whisper backend does not support speaker diarization; ignoring with_speaker=True")

        audio: Any = audio_input
        if isinstance(audio_input, (bytes, bytearray)):
            pcm = bytes(audio_input)
            # PCM16LE @ 16kHz mono -> float32 waveform [-1, 1]
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        elif isinstance(audio_input, (str, Path)):
            audio = str(audio_input)
        elif isinstance(audio_input, np.ndarray):
            audio = np.asarray(audio_input, dtype=np.float32).reshape(-1)

        transcribe_kwargs: Dict[str, Any] = {
            "beam_size": int(self.beam_size),
            "best_of": int(self.best_of),
            "temperature": float(self.temperature),
            "vad_filter": bool(self.vad_filter),
            "word_timestamps": bool(self.word_timestamps),
        }
        if self.language:
            transcribe_kwargs["language"] = self.language
        if hotwords and str(hotwords).strip():
            hot = str(hotwords).strip()
            # Prefer the dedicated `hotwords` hint when available (faster-whisper),
            # but also provide a short initial_prompt as extra context.
            transcribe_kwargs["hotwords"] = ", ".join(_parse_hotword_terms(hot)[:50])
            transcribe_kwargs["initial_prompt"] = _format_initial_prompt(hot)

        # VAD tuning (only used when vad_filter=True).
        transcribe_kwargs["vad_parameters"] = {"min_silence_duration_ms": int(self.vad_min_silence_duration_ms)}

        # Allow per-request overrides via `asr_options.backend.*`.
        for k, v in kwargs.items():
            if v is None:
                continue
            transcribe_kwargs[k] = v

        # Be robust to faster-whisper signature differences.
        try:
            sig = inspect.signature(self._model.transcribe)  # type: ignore[attr-defined]
            transcribe_kwargs = {k: v for k, v in transcribe_kwargs.items() if k in sig.parameters}
        except Exception:
            pass

        segments_iter, info = self._model.transcribe(audio, **transcribe_kwargs)  # type: ignore[attr-defined]

        sentence_info: List[Dict[str, Any]] = []
        texts: List[str] = []
        for seg in segments_iter:
            # faster-whisper yields Segment objects with .text/.start/.end
            text = str(getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            start_s = float(getattr(seg, "start", 0.0) or 0.0)
            end_s = float(getattr(seg, "end", 0.0) or 0.0)
            sentence_info.append(
                {
                    "text": text,
                    "start": int(round(start_s * 1000.0)),
                    "end": int(round(end_s * 1000.0)),
                }
            )
            texts.append(text)

        text_out = " ".join(texts).strip()

        raw_info: Dict[str, Any] = {}
        try:
            # Keep this JSON-friendly; only include a few stable fields.
            for k in (
                "language",
                "duration",
                "language_probability",
            ):
                v = getattr(info, k, None)
                if isinstance(v, (str, int, float, bool)) or v is None:
                    raw_info[k] = v
        except Exception:
            raw_info = {"info": str(info)}

        return {"text": text_out, "sentence_info": sentence_info, "_raw": {"info": raw_info}}

    def get_info(self) -> Dict[str, Any]:
        base = super().get_info()
        base.update(
            {
                "type": "whisper",
                "device": self.device,
                "model": self.model,
                "language": self.language,
                "compute_type": self.compute_type,
                "vad_filter": self.vad_filter,
                "word_timestamps": self.word_timestamps,
            }
        )
        return base


def _parse_hotword_terms(hotwords: str) -> List[str]:
    lines = [str(s).strip() for s in str(hotwords).splitlines()]
    terms = [ln for ln in lines if ln and not ln.startswith("#")]

    seen = set()
    out: List[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _format_initial_prompt(hotwords: str) -> str:
    terms = _parse_hotword_terms(hotwords)
    if not terms:
        return str(hotwords).strip()

    # Keep the prompt short; Whisper's initial_prompt is best-effort only.
    terms = terms[:50]
    joined = ", ".join(terms[:12])
    suffix = ""
    if len(terms) > 12:
        suffix = f"（另有{len(terms) - 12}个）"
    return f"专有名词/缩写提示（若出现请保持原样）：{joined}{suffix}"
