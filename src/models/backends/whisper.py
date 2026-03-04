"""Local Whisper backend (openai-whisper).

This backend runs Whisper inference locally (GPU preferred) and adapts outputs
to Xiyu's standard backend contract:
  - returns {"text": str, "sentence_info": [{text,start,end}, ...]}

Input handling:
  - Xiyu's API converts uploads into 16kHz mono PCM16LE bytes.
  - Whisper expects either a file path or a float waveform, so we convert when
    receiving raw PCM bytes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, List

import numpy as np

from .base import ASRBackend

logger = logging.getLogger(__name__)


class WhisperBackend(ASRBackend):
    """Local Whisper backend using `openai-whisper`."""

    def __init__(
        self,
        *,
        model: str = "large",
        device: str = "cuda",
        language: Optional[str] = "zh",
        download_root: str = "",
        **_kwargs: Any,
    ) -> None:
        self.model = str(model or "large")
        self.device = str(device or "cuda")
        self.language = str(language) if language is not None else None
        self.download_root = str(download_root or "")

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
        # Best-effort: we pass hotwords into initial_prompt when available.
        return True

    def load(self) -> None:
        if self._loaded and self._model is not None:
            return

        try:
            import whisper  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Whisper backend requires openai-whisper: pip install openai-whisper"
            ) from e

        logger.info(f"Loading Whisper model={self.model} device={self.device}")
        kwargs: Dict[str, Any] = {"device": self.device}
        if self.download_root.strip():
            kwargs["download_root"] = self.download_root.strip()
        self._model = whisper.load_model(self.model, **kwargs)  # type: ignore[attr-defined]
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

        audio = audio_input
        if isinstance(audio_input, (bytes, bytearray)):
            pcm = bytes(audio_input)
            # PCM16LE @ 16kHz mono -> float32 waveform [-1, 1]
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        elif isinstance(audio_input, (str, Path)):
            audio = str(audio_input)

        transcribe_kwargs: Dict[str, Any] = {}
        if self.language:
            transcribe_kwargs["language"] = self.language
        if hotwords and str(hotwords).strip():
            # Whisper uses "initial_prompt" as a context hint. Being explicit
            # improves proper-noun recall for meetings.
            transcribe_kwargs["initial_prompt"] = _format_initial_prompt(str(hotwords).strip())

        # Allow per-request overrides via `asr_options.backend.*`.
        for k, v in kwargs.items():
            if v is None:
                continue
            transcribe_kwargs[k] = v

        result: Dict[str, Any] = self._model.transcribe(audio, **transcribe_kwargs)  # type: ignore[no-any-return]

        segments = result.get("segments") or []
        sentence_info = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            start_s = float(seg.get("start", 0.0) or 0.0)
            end_s = float(seg.get("end", 0.0) or 0.0)
            sentence_info.append(
                {
                    "text": text,
                    "start": int(round(start_s * 1000.0)),
                    "end": int(round(end_s * 1000.0)),
                }
            )

        text_out = str(result.get("text") or "").strip()
        if not text_out and sentence_info:
            text_out = " ".join(s["text"] for s in sentence_info).strip()

        return {"text": text_out, "sentence_info": sentence_info, "_raw": result}

    def get_info(self) -> Dict[str, Any]:
        base = super().get_info()
        base.update(
            {
                "type": "whisper",
                "device": self.device,
                "model": self.model,
                "language": self.language,
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
