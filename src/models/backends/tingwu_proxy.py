"""Proxy backend that calls another TingWu service over HTTP.

This is mainly used by the Router backend to delegate inference to an existing
model container (e.g. `tingwu-whisper`) without loading a second copy of the
model weights in the router container.

Important: We intentionally disable nested features on the proxy target:
- with_speaker=false (avoid running external diarizer multiple times)
- apply_hotword=false (avoid double correction; caller handles it)
- apply_llm=false (avoid nested LLM calls)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from src.models.backends.base import ASRBackend
from src.models.backends.remote_utils import audio_input_to_wav_bytes

logger = logging.getLogger(__name__)


class TingWuTranscribeProxyBackend(ASRBackend):
    """Call `/api/v1/transcribe` on another TingWu service instance."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 60.0,
        name: str = "tingwu-proxy",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_s = float(timeout_s)
        self.name = str(name or "tingwu-proxy")
        self._client: Optional[httpx.Client] = None

    def load(self) -> None:
        if self._client is None:
            # Do not inherit HTTP(S)_PROXY/ALL_PROXY by default.
            # These calls are usually inside a docker network.
            self._client = httpx.Client(timeout=self.timeout_s, trust_env=False)

    @property
    def supports_streaming(self) -> bool:
        return False

    @property
    def supports_hotwords(self) -> bool:
        # We don't forward hotwords to avoid nested correction/injection.
        return False

    @property
    def supports_speaker(self) -> bool:
        # Speaker turns are produced by the outer engine (external diarizer), not here.
        return False

    def transcribe(
        self,
        audio_input,
        hotwords: Optional[str] = None,
        with_speaker: bool = False,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        self.load()
        assert self._client is not None

        if not self.base_url:
            raise ValueError("TingWuTranscribeProxyBackend requires base_url")

        wav_bytes, _duration_s = audio_input_to_wav_bytes(audio_input)
        url = f"{self.base_url}/api/v1/transcribe"

        # Disable nested diarizer/corrections/LLM on the proxy target.
        data = {
            "with_speaker": "false",
            "apply_hotword": "false",
            "apply_llm": "false",
            "llm_role": "default",
        }
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}

        resp = self._client.post(url, data=data, files=files)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.text
            except Exception:
                body = ""
            body = (body or "").strip()
            if len(body) > 4096:
                body = body[:4096] + " ..."
            raise RuntimeError(
                f"TingWu proxy HTTP {e.response.status_code} for {url}: {body or '<empty body>'}"
            ) from e

        obj = resp.json()
        code = obj.get("code", 0)
        if code != 0:
            raise RuntimeError(f"TingWu proxy returned code={code!r} for {url}")

        text = str(obj.get("text") or "")
        sentence_info = []
        sentences = obj.get("sentences") or []
        if isinstance(sentences, list):
            for s in sentences:
                if not isinstance(s, dict):
                    continue
                sentence_info.append(
                    {
                        "text": str(s.get("text") or ""),
                        "start": int(s.get("start") or 0),
                        "end": int(s.get("end") or 0),
                    }
                )

        return {
            "text": text,
            "sentence_info": sentence_info,
            "_proxy": {"base_url": self.base_url},
        }

    def unload(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def get_info(self) -> Dict[str, Any]:
        base = super().get_info()
        base.update(
            {
                "type": "tingwu_proxy",
                "name": self.name,
                "base_url": self.base_url,
            }
        )
        return base

