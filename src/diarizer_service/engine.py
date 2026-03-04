from __future__ import annotations

import io
import logging
import threading
import wave
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


class DiarizerEngine:
    """External diarization engine (pyannote), with lazy imports.

    This module must stay importable without heavyweight ML deps so unit tests for
    the main Xiyu service can run without installing `pyannote.audio`.
    """

    def __init__(
        self,
        *,
        model_id: str,
        device: str = "cuda",
        hf_token: Optional[str] = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> None:
        self.model_id = str(model_id or "").strip()
        if not self.model_id:
            raise ValueError("model_id must be non-empty")

        self.device = str(device or "cpu").strip() or "cpu"
        self.hf_token = str(hf_token).strip() if hf_token else None

        self.num_speakers = int(num_speakers) if isinstance(num_speakers, int) else None
        self.min_speakers = int(min_speakers) if isinstance(min_speakers, int) else None
        self.max_speakers = int(max_speakers) if isinstance(max_speakers, int) else None

        # Best-effort validation (avoid surprising crashes on startup).
        if self.num_speakers is not None and self.num_speakers <= 0:
            self.num_speakers = None
        if self.min_speakers is not None and self.min_speakers <= 0:
            self.min_speakers = None
        if self.max_speakers is not None and self.max_speakers <= 0:
            self.max_speakers = None

        self._load_lock = threading.Lock()
        self._loaded = False
        self._pipeline = None

    def load(self) -> None:
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:
                return

            # Compatibility patch:
            # - pyannote.audio Pipeline.from_pretrained (3.x) passes `use_auth_token=...`
            # - huggingface_hub (>=1.0) removed `use_auth_token` in favor of `token`
            # Without this shim, diarization fails at runtime with:
            #   hf_hub_download() got an unexpected keyword argument 'use_auth_token'
            try:
                import inspect

                import huggingface_hub  # type: ignore[import-not-found]
                from huggingface_hub import hf_hub_download as _orig_hf_hub_download  # type: ignore[import-not-found]

                if "use_auth_token" not in inspect.signature(_orig_hf_hub_download).parameters:

                    def _hf_hub_download_compat(*args, use_auth_token=None, token=None, **kwargs):
                        if token is None and use_auth_token is not None:
                            token = use_auth_token
                        return _orig_hf_hub_download(*args, token=token, **kwargs)

                    # Patch both common import paths before importing pyannote.audio.
                    huggingface_hub.hf_hub_download = _hf_hub_download_compat  # type: ignore[attr-defined]
                    try:
                        import huggingface_hub.file_download as _file_download  # type: ignore[import-not-found]

                        _file_download.hf_hub_download = _hf_hub_download_compat  # type: ignore[attr-defined]
                    except Exception:
                        pass
            except Exception:
                # Best-effort only: if this fails for any reason, we still try to proceed.
                pass

            # Lazy heavy imports.
            from pyannote.audio import Pipeline  # type: ignore[import-not-found]

            import torch

            # PyTorch 2.6 changed `torch.load(..., weights_only=...)` default to
            # `True`, which can break loading some HF checkpoints unless certain
            # globals are allowlisted.
            #
            # pyannote pipelines/models are trusted here (downloaded from HF with
            # an explicit user token), so we allowlist TorchVersion to unblock
            # weights-only unpickling.
            try:
                from torch.torch_version import TorchVersion  # type: ignore[attr-defined]

                if hasattr(torch, "serialization") and hasattr(torch.serialization, "add_safe_globals"):
                    torch.serialization.add_safe_globals([TorchVersion])  # type: ignore[arg-type]
            except Exception:
                pass

            # pyannote checkpoints may pickle a few lightweight helper classes.
            # Allowlist them for weights-only loading mode.
            try:
                from pyannote.audio.core.task import Specifications  # type: ignore[import-not-found]

                if hasattr(torch, "serialization") and hasattr(torch.serialization, "add_safe_globals"):
                    torch.serialization.add_safe_globals([Specifications])  # type: ignore[arg-type]
            except Exception:
                pass

            # If the checkpoint still fails under weights-only mode, prefer the
            # legacy behavior (weights_only=False) for pyannote models we trust.
            # This matches PyTorch's own guidance for trusted checkpoints.
            try:
                import inspect

                _orig_torch_load = torch.load
                if "weights_only" in inspect.signature(_orig_torch_load).parameters:

                    def _torch_load_compat(*args, **kwargs):
                        # Force legacy behavior for trusted checkpoints.
                        # Some upstream libraries explicitly pass weights_only=True.
                        kwargs["weights_only"] = False
                        return _orig_torch_load(*args, **kwargs)

                    torch.load = _torch_load_compat  # type: ignore[assignment]
            except Exception:
                pass

            pipeline = Pipeline.from_pretrained(self.model_id, use_auth_token=self.hf_token)
            if pipeline is None:
                raise RuntimeError("failed to load diarization pipeline")

            # Best-effort: move to requested device; fall back silently.
            try:
                pipeline.to(torch.device(self.device))
            except Exception:
                try:
                    pipeline.to(self.device)
                except Exception as e:
                    logger.warning(f"Failed to move diarizer pipeline to device={self.device!r}: {e}")

            self._pipeline = pipeline
            self._loaded = True

    def diarize(self, wav_bytes: bytes) -> List[Dict[str, int]]:
        """Run diarization and return raw segments [{spk,start,end}, ...] in ms."""
        if not wav_bytes:
            return []

        self.load()
        if self._pipeline is None:
            raise RuntimeError("diarizer pipeline not loaded")

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sample_rate = wf.getframerate() or 0
            nframes = wf.getnframes()
            pcm = wf.readframes(nframes)

        if channels != 1:
            raise ValueError("Only mono WAV is supported")
        if sampwidth != 2:
            raise ValueError("Only 16-bit PCM WAV is supported")
        if sample_rate <= 0:
            raise ValueError("Invalid WAV sample_rate")

        # Convert PCM16LE to float32 waveform tensor expected by pyannote.
        import numpy as np
        import torch

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32, copy=False)
        if audio.size == 0:
            return []
        audio = audio / 32768.0
        waveform = torch.from_numpy(audio).unsqueeze(0)

        call_kwargs: Dict[str, int] = {}
        if self.num_speakers is not None:
            call_kwargs["num_speakers"] = self.num_speakers
        if self.min_speakers is not None:
            call_kwargs["min_speakers"] = self.min_speakers
        if self.max_speakers is not None:
            call_kwargs["max_speakers"] = self.max_speakers

        try:
            diarization = self._pipeline({"waveform": waveform, "sample_rate": sample_rate}, **call_kwargs)
        except TypeError:
            # Some pipeline variants might not accept speaker bounds; fall back to defaults.
            diarization = self._pipeline({"waveform": waveform, "sample_rate": sample_rate})

        items = []
        try:
            it = diarization.itertracks(yield_label=True)
        except Exception:
            it = []

        for segment, _track, label in it:
            try:
                start_s = float(segment.start)
                end_s = float(segment.end)
            except Exception:
                continue
            items.append((start_s, end_s, str(label)))

        items.sort(key=lambda x: (x[0], x[1], x[2]))

        speaker_mapping: Dict[str, int] = {}
        out: List[Dict[str, int]] = []
        for start_s, end_s, label in items:
            if label not in speaker_mapping:
                speaker_mapping[label] = len(speaker_mapping)
            spk_id = speaker_mapping[label]

            start_ms = int(start_s * 1000)
            end_ms = int(end_s * 1000)
            if start_ms < 0:
                start_ms = 0
            if end_ms <= start_ms:
                continue

            out.append({"spk": spk_id, "start": start_ms, "end": end_ms})

        return out
