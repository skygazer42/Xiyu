"""Remote Qwen3-ASR backend (OpenAI-compatible API).

Different deployments expose different endpoints:
1) `qwen-asr-serve` (official Qwen3-ASR image) typically implements
   `/v1/audio/transcriptions` (Whisper/OpenAI style).
2) Some vLLM multimodal deployments implement `/v1/chat/completions` with
   `audio_url` inputs.

We prefer `/v1/audio/transcriptions` and fall back to chat-completions when the
endpoint is not available.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict, Optional, List

import httpx

from src.models.backends.base import ASRBackend
from src.models.backends.remote_utils import audio_input_to_wav_bytes

logger = logging.getLogger(__name__)


def _strip_qwen3_asr_wrappers(text: str) -> str:
    """Strip Qwen3-ASR server wrapper markers from transcript text.

    Some Qwen3-ASR server variants return output like:
      "language Chinese<asr_text>...language Chinese<asr_text>..."

    These markers can appear multiple times (e.g. streaming concatenation).
    We remove *all* occurrences to keep the transcript clean.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if "<asr_text>" not in s:
        return s

    import re

    # Remove the common "language XXX<asr_text>" prefix.
    s = re.sub(r"language\s+[A-Za-z0-9_-]+\s*<asr_text>", "", s, flags=re.IGNORECASE)
    # Remove any remaining tags.
    s = s.replace("<asr_text>", "")
    return s.strip()


class Qwen3RemoteBackend(ASRBackend):
    """Call a remote Qwen3-ASR server (audio transcriptions preferred)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "EMPTY",
        timeout_s: float = 60.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = float(timeout_s)
        self._client: Optional[httpx.Client] = None

    def load(self) -> None:
        # Lazy init so tests can patch httpx.Client.post without needing a real server.
        if self._client is None:
            # Do not inherit HTTP(S)_PROXY/ALL_PROXY from the environment by default.
            # Remote ASR servers are usually on localhost/docker networks, and proxy
            # settings (especially invalid schemes like "socks://") can break client
            # init before we even send the request.
            self._client = httpx.Client(timeout=self.timeout_s, trust_env=False)

    @property
    def supports_streaming(self) -> bool:
        return False

    @property
    def supports_speaker(self) -> bool:
        return False

    def transcribe(self, audio_input, hotwords: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        self.load()
        assert self._client is not None

        # Prefer the Whisper/OpenAI-style transcription endpoint.
        try:
            return self._transcribe_audio_transcriptions(audio_input, hotwords=hotwords, model=self.model)
        except httpx.HTTPStatusError as e:
            # Common failure modes:
            # - 404: endpoint not implemented on this server (fall back to chat-completions).
            # - 400/404: model name mismatch (resolve via /v1/models, retry once).
            if e.response.status_code in (400, 404):
                resolved = self._resolve_first_model_id()
                if resolved and resolved != self.model:
                    try:
                        out = self._transcribe_audio_transcriptions(audio_input, hotwords=hotwords, model=resolved)
                        self.model = resolved
                        return out
                    except httpx.HTTPStatusError as e2:
                        e = e2

            if e.response.status_code == 404:
                # Endpoint missing; try chat-completions variant.
                return self._transcribe_chat_completions_with_fallback(audio_input, hotwords=hotwords)

            url = f"{self.base_url}/v1/audio/transcriptions"
            raise RuntimeError(
                f"Qwen3-ASR HTTP {e.response.status_code} for {url}: {_response_body_head(e.response)}"
            ) from e

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _resolve_first_model_id(self) -> Optional[str]:
        """Resolve the actually-served model id from the remote server.

        Many vLLM deployments use a local snapshot path as the served model name.
        If users configure `model=Qwen/Qwen3-ASR-...` it may not match. Querying
        `/v1/models` lets us retry with the correct id.
        """
        assert self._client is not None
        url = f"{self.base_url}/v1/models"
        resp = self._client.get(url, headers=self._auth_headers())
        resp.raise_for_status()
        obj = resp.json()
        if not isinstance(obj, dict):
            return None
        data = obj.get("data")
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if isinstance(first, dict):
            mid = first.get("id")
            if mid:
                return str(mid)
        return None

    def _transcribe_audio_transcriptions(
        self,
        audio_input,
        *,
        hotwords: Optional[str],
        model: str,
    ) -> Dict[str, Any]:
        assert self._client is not None
        audio_bytes, _duration_s = audio_input_to_wav_bytes(audio_input)

        url = f"{self.base_url}/v1/audio/transcriptions"
        data: Dict[str, str] = {"model": str(model)}
        if hotwords:
            hint = _format_hotwords_hint(hotwords) or str(hotwords)
            data["prompt"] = hint
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}

        resp = self._client.post(url, data=data, files=files, headers=self._auth_headers())
        resp.raise_for_status()

        obj = resp.json()
        text = _extract_text_from_transcriptions(obj)
        return {"text": text, "sentence_info": []}

    def _transcribe_chat_completions_with_fallback(
        self,
        audio_input,
        *,
        hotwords: Optional[str],
    ) -> Dict[str, Any]:
        """Fallback path for deployments that implement audio via chat-completions."""
        assert self._client is not None
        try:
            return self._transcribe_chat_completions(audio_input, hotwords=hotwords, model=self.model)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 404):
                resolved = self._resolve_first_model_id()
                if resolved and resolved != self.model:
                    try:
                        out = self._transcribe_chat_completions(audio_input, hotwords=hotwords, model=resolved)
                        self.model = resolved
                        return out
                    except httpx.HTTPStatusError as e2:
                        e = e2
            url = f"{self.base_url}/v1/chat/completions"
            raise RuntimeError(
                f"Qwen3-ASR HTTP {e.response.status_code} for {url}: {_response_body_head(e.response)}"
            ) from e

    def _transcribe_chat_completions(
        self,
        audio_input,
        *,
        hotwords: Optional[str],
        model: str,
    ) -> Dict[str, Any]:
        assert self._client is not None
        wav_bytes, duration_s = audio_input_to_wav_bytes(audio_input)

        url = f"{self.base_url}/v1/chat/completions"

        mime = "audio/wav"
        if isinstance(audio_input, (str, Path)):
            mime = _guess_mime_type(audio_input)

        audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
        data_url = f"data:{mime};base64,{audio_b64}"

        # Some deployments support audio_url inputs in chat completions.
        user_content = [
            {"type": "audio_url", "audio_url": {"url": data_url}},
        ]
        if hotwords:
            hint = _format_hotwords_hint(hotwords)
            user_content.append({"type": "text", "text": hint or str(hotwords)})
        elif duration_s > 0:
            user_content.append({"type": "text", "text": f"Audio duration: {duration_s:.2f}s"})

        payload = {
            "model": str(model),
            "messages": [{"role": "user", "content": user_content}],
            "temperature": 0.0,
            "stream": False,
        }

        resp = self._client.post(url, json=payload, headers=self._auth_headers())
        resp.raise_for_status()

        obj = resp.json()
        text = _extract_text_from_chat_completion(obj)
        return {"text": text, "sentence_info": []}


def _parse_hotword_terms(hotwords: str) -> List[str]:
    lines = [str(s).strip() for s in str(hotwords).splitlines()]
    terms = [ln for ln in lines if ln and not ln.startswith("#")]

    # De-dupe while preserving order.
    seen = set()
    out: List[str] = []
    for t in terms:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _format_hotwords_hint(hotwords: str) -> str:
    """Format hotwords into an instruction-like hint for chat-completions ASR models.

    NOTE: For FunASR-like backends, hotwords are passed as a raw list. For the
    chat-completions API, being explicit improves proper-noun recall.
    """
    terms = _parse_hotword_terms(hotwords)
    if not terms:
        return ""

    # Keep it concise: very long hints can harm decoding.
    terms = terms[:50]
    if len(terms) <= 8:
        joined = ", ".join(terms)
        return f"专有名词/缩写提示（若出现请保持原样转写）：{joined}"

    bullets = "\n".join(f"- {t}" for t in terms)
    return "专有名词/缩写提示（若出现请保持原样转写）：\n" + bullets


def _guess_mime_type(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".wav":
        return "audio/wav"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".flac":
        return "audio/flac"
    if ext in (".ogg", ".opus"):
        return "audio/ogg"
    if ext in (".m4a", ".mp4", ".m4v", ".mov", ".webm"):
        # vLLM can decode audio from videos too (ffmpeg), but the exact container may vary.
        return "video/mp4"
    return "application/octet-stream"


def _extract_text_from_transcriptions(obj: object) -> str:
    """Extract transcription text from an OpenAI-style audio transcription response."""
    if isinstance(obj, dict):
        # OpenAI uses {"text": "..."}; some servers use {"transcript": "..."}.
        text = obj.get("text")
        if text is None:
            text = obj.get("transcript")
        if text is None:
            text = obj.get("content")
        return _strip_qwen3_asr_wrappers(str(text or ""))
    return str(obj).strip()


def _extract_text_from_chat_completion(obj: object) -> str:
    """Extract transcription text from an OpenAI-style chat completion response."""
    if not isinstance(obj, dict):
        return str(obj).strip()

    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        choice0 = choices[0] or {}
        if isinstance(choice0, dict):
            msg = choice0.get("message") or {}
            if isinstance(msg, dict):
                content = msg.get("content")
                if content is not None:
                    return _strip_qwen3_asr_wrappers(str(content))

    # Fallback: try common alternative keys.
    for k in ("text", "transcript", "content"):
        if k in obj:
            return _strip_qwen3_asr_wrappers(str(obj.get(k) or ""))

    return ""


def _response_body_head(resp: httpx.Response, *, limit: int = 4096) -> str:
    body = ""
    try:
        body = (resp.text or "").strip()
    except Exception:
        body = ""
    if not body:
        return "<empty body>"
    if len(body) > limit:
        return body[:limit] + " ..."
    return body
