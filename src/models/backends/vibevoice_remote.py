"""Remote VibeVoice-ASR backend (vLLM OpenAI-compatible chat completions API).

VibeVoice's vLLM plugin exposes `/v1/chat/completions` with `audio_url` inputs and
returns a JSON-formatted transcription with keys like:
  - Start time
  - End time
  - Speaker ID
  - Content
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.models.backends.base import ASRBackend
from src.models.backends.remote_utils import audio_input_to_wav_bytes

logger = logging.getLogger(__name__)


class VibeVoiceRemoteBackend(ASRBackend):
    """Call a remote VibeVoice-ASR server.

    The official deployment uses `/v1/chat/completions` (OpenAI-compatible).
    Some alternative deployments may implement `/v1/audio/transcriptions`; we
    keep a switch for that for flexibility.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "EMPTY",
        timeout_s: float = 600.0,
        use_chat_completions_fallback: bool = True,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_s = float(timeout_s)
        self.use_chat_completions_fallback = bool(use_chat_completions_fallback)
        self._client: Optional[httpx.Client] = None
        self._remote_max_model_len: Optional[int] = None

    def load(self) -> None:
        if self._client is None:
            # Do not inherit HTTP(S)_PROXY/ALL_PROXY by default.
            # These backends usually talk to localhost/docker networks, and some
            # environments set unsupported proxy schemes (e.g. "socks://") that
            # cause httpx client init to fail before sending any request.
            self._client = httpx.Client(timeout=self.timeout_s, trust_env=False)

    @property
    def supports_streaming(self) -> bool:
        return False

    @property
    def supports_hotwords(self) -> bool:
        return True

    @property
    def supports_speaker(self) -> bool:
        # VibeVoice-ASR jointly performs diarization.
        return True

    def transcribe(self, audio_input, hotwords: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        with_speaker = bool(kwargs.get("with_speaker", False))
        # Prefer chat completions for the official vLLM plugin deployment.
        if self.use_chat_completions_fallback:
            return self._transcribe_chat_completions(audio_input, hotwords=hotwords, with_speaker=with_speaker)
        return self._transcribe_audio_transcriptions(audio_input, hotwords=hotwords)

    def _transcribe_chat_completions(self, audio_input, hotwords: Optional[str], *, with_speaker: bool) -> Dict[str, Any]:
        self.load()
        assert self._client is not None

        audio_bytes, duration_s = audio_input_to_wav_bytes(audio_input)

        mime = "audio/wav"
        if isinstance(audio_input, (str, Path)):
            mime = _guess_mime_type(audio_input)

        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        data_url = f"data:{mime};base64,{audio_b64}"

        if with_speaker:
            show_keys = ["Start time", "End time", "Speaker ID", "Content"]
            json_only = (
                "Return ONLY valid JSON (no extra commentary, no markdown/code fences). "
                "Prefer a JSON array of objects."
            )
            if duration_s > 0:
                if hotwords:
                    prompt_text = (
                        f"This is a {duration_s:.2f} seconds audio, with extra info: {hotwords.strip()}\n\n"
                        f"Please transcribe it with these keys: {', '.join(show_keys)}. {json_only}"
                    )
                else:
                    prompt_text = (
                        f"This is a {duration_s:.2f} seconds audio, please transcribe it with these keys: "
                        + ", ".join(show_keys)
                        + f". {json_only}"
                    )
            else:
                if hotwords:
                    prompt_text = (
                        f"Please transcribe this audio with extra info: {hotwords.strip()}\n\n"
                        f"Return JSON with keys: {', '.join(show_keys)}. {json_only}"
                    )
                else:
                    prompt_text = (
                        f"Please transcribe this audio and return JSON with keys: {', '.join(show_keys)}. {json_only}"
                    )
        else:
            # NOTE: When using external diarizer, TingWu already owns the speaker
            # segmentation and only needs plain text per segment. Asking the
            # model to output JSON often results in malformed/incomplete JSON
            # (e.g. the model starts a JSON string but never closes it), which
            # then pollutes the transcript.
            no_json = "Return ONLY the transcript text (no JSON, no timestamps, no speaker labels, no markdown)."
            no_tags = "Do not include bracketed non-speech tags like [Music], [Laughter]."
            if duration_s > 0:
                if hotwords:
                    prompt_text = (
                        f"This is a {duration_s:.2f} seconds audio. Extra info: {hotwords.strip()}\n\n"
                        f"Please transcribe it. {no_json} {no_tags}"
                    )
                else:
                    prompt_text = (
                        f"This is a {duration_s:.2f} seconds audio.\n\n"
                        f"Please transcribe it. {no_json} {no_tags}"
                    )
            else:
                if hotwords:
                    prompt_text = (
                        f"Extra info: {hotwords.strip()}\n\n"
                        f"Please transcribe this audio. {no_json} {no_tags}"
                    )
                else:
                    prompt_text = f"Please transcribe this audio. {no_json} {no_tags}"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant that transcribes audio input into text output. "
                        "Follow the user's output format requirements strictly."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": data_url}},
                        {"type": "text", "text": prompt_text},
                    ],
                },
            ],
            # vLLM enforces: max_tokens + prompt_tokens <= max_model_len.
            # Some models have max_model_len=4096, and our prompt is non-empty, so
            # hard-coding 4096 will fail with HTTP 400. We pick a safe default
            # and additionally cap it by the server-reported max_model_len.
            "max_tokens": self._pick_max_tokens(prompt_text),
            "temperature": 0.0,
            "top_p": 1.0,
            "stream": False,
        }

        url = f"{self.base_url}/v1/chat/completions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        resp = self._client.post(url, json=payload, headers=headers)
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

            # Best-effort retry for the common vLLM error:
            # "'max_tokens' ... is too large ... maximum context length is N ... request has M input tokens ..."
            # In that case compute a smaller max_tokens and retry once.
            if e.response.status_code == 400:
                retry_tokens = _maybe_extract_allowed_max_tokens(body)
                if retry_tokens is not None:
                    payload["max_tokens"] = retry_tokens
                    resp2 = self._client.post(url, json=payload, headers=headers)
                    try:
                        resp2.raise_for_status()
                    except httpx.HTTPStatusError:
                        pass
                    else:
                        raw = resp2.json()
                        content = _extract_chat_content(raw)
                        return _postprocess_vibevoice_content(content, with_speaker=with_speaker)

            raise RuntimeError(
                f"VibeVoice-ASR HTTP {e.response.status_code} for {url}: {body or '<empty body>'}"
            ) from e

        raw = resp.json()
        content = _extract_chat_content(raw)
        return _postprocess_vibevoice_content(content, with_speaker=with_speaker)

    def _pick_max_tokens(self, prompt_text: str) -> int:
        """Pick a safe max_tokens value for vLLM chat completions.

        vLLM validates `max_tokens` against the model's `max_model_len` and the
        prompt token count. We do not depend on a tokenizer here, so we keep a
        conservative safety margin.
        """
        # Default generation budget for transcripts.
        default_budget = 2048

        max_model_len = self._get_remote_max_model_len()
        if max_model_len is None:
            # No signal from server; use a safe value for 4k-context models.
            return 1024

        # Reserve some room for system/user prompt tokens and tool overhead.
        safety_prompt_tokens = 512
        # Additionally reserve a bit more if prompt is huge (hotwords can be long).
        if prompt_text and len(prompt_text) > 2000:
            safety_prompt_tokens = 1024

        budget = max(64, min(default_budget, int(max_model_len) - safety_prompt_tokens))
        return budget

    def _get_remote_max_model_len(self) -> Optional[int]:
        if self._remote_max_model_len is not None:
            return self._remote_max_model_len
        self.load()
        assert self._client is not None

        url = f"{self.base_url}/v1/models"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = self._client.get(url, headers=headers)
            resp.raise_for_status()
            obj = resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch VibeVoice /v1/models from {url}: {e}")
            return None

        max_len: Optional[int] = None
        try:
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, list) and data:
                # Prefer exact match by id; otherwise fallback to first entry.
                picked = None
                for item in data:
                    if isinstance(item, dict) and item.get("id") == self.model:
                        picked = item
                        break
                if picked is None and isinstance(data[0], dict):
                    picked = data[0]
                if isinstance(picked, dict):
                    mml = picked.get("max_model_len")
                    if isinstance(mml, int) and mml > 0:
                        max_len = mml
        except Exception:
            max_len = None

        self._remote_max_model_len = max_len
        return max_len

    def _transcribe_audio_transcriptions(self, audio_input, hotwords: Optional[str]) -> Dict[str, Any]:
        """Optional OpenAI transcription endpoint support (if a server provides it)."""
        self.load()
        assert self._client is not None

        audio_bytes, _duration_s = audio_input_to_wav_bytes(audio_input)
        url = f"{self.base_url}/v1/audio/transcriptions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = {"model": self.model}
        if hotwords:
            data["prompt"] = hotwords
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}

        resp = self._client.post(url, data=data, files=files, headers=headers)
        resp.raise_for_status()
        obj = resp.json()
        if isinstance(obj, dict):
            text = str(obj.get("text") or obj.get("transcript") or "")
        else:
            text = str(obj)
        return {"text": text, "sentence_info": []}


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
        return "video/mp4"
    return "application/octet-stream"


def _extract_chat_content(obj: object) -> str:
    if not isinstance(obj, dict):
        return str(obj)
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0] or {}
        if isinstance(c0, dict):
            msg = c0.get("message") or {}
            if isinstance(msg, dict):
                content = msg.get("content")
                if content is not None:
                    return str(content)
    return ""


def _maybe_extract_allowed_max_tokens(body: str) -> Optional[int]:
    """Parse vLLM max_tokens error body and return a retry max_tokens."""
    if not body:
        return None
    # Example message:
    # "'max_tokens' ... value=4096. This model's maximum context length is 4096 tokens and your request has 150 input tokens (4096 > 4096 - 150)."
    import re

    m_ctx = re.search(r"maximum context length is\\s+(\\d+)\\s+tokens", body)
    m_in = re.search(r"request has\\s+(\\d+)\\s+input tokens", body)
    if not m_ctx or not m_in:
        return None
    try:
        ctx = int(m_ctx.group(1))
        inp = int(m_in.group(1))
    except Exception:
        return None
    # Keep a small cushion to avoid exact-boundary failures.
    allowed = ctx - inp - 16
    if allowed <= 0:
        return 1
    return allowed


def _postprocess_vibevoice_content(content: str, *, with_speaker: bool) -> Dict[str, Any]:
    """Convert vLLM chat completion `content` into TingWu backend output.

    When `with_speaker` is False (typical external diarizer pipeline), we prefer
    returning plain text to avoid brittle JSON formatting.
    """
    s = str(content or "").strip()
    if not s:
        return {"text": "", "sentence_info": []}

    segments = _parse_vibevoice_segments(s)
    if segments:
        sentence_info: List[Dict[str, Any]] = []
        texts: List[str] = []
        for seg in segments:
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            texts.append(text)
            if with_speaker:
                start_ms = _time_to_ms(seg.get("start_time"))
                end_ms = _time_to_ms(seg.get("end_time"))
                spk = seg.get("speaker_id")
                sentence_info.append({"text": text, "start": start_ms, "end": end_ms, "spk": spk})

        return {"text": " ".join(texts).strip(), "sentence_info": sentence_info if with_speaker else []}

    # No segments parsed; return best-effort plain text.
    if not with_speaker:
        # Some deployments still output JSON-like blobs even when asked for
        # plain text. If the JSON is malformed/incomplete, `_parse_vibevoice_segments`
        # will fail; salvage `Content` fields so we don't leak raw JSON into
        # the transcript.
        extracted = _extract_jsonish_content_values(s)
        if extracted:
            s = " ".join(x for x in extracted if str(x).strip()).strip()

        # Strip some common bracketed non-speech tags.
        try:
            import re

            s = re.sub(r"\\[(?:music|laughter|applause|noise|silence)\\]", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\\s{2,}", " ", s).strip()
        except Exception:
            pass
    return {"text": s, "sentence_info": []}


def _extract_jsonish_content_values(text: str) -> List[str]:
    """Extract `Content` values from JSON-like output (best-effort).

    This is a fallback for cases where the model starts emitting JSON segments
    but produces malformed/incomplete JSON that `json.loads()` can't parse.
    """
    s = str(text or "")
    if not s:
        return []

    # Fast path: skip if no Content-like key.
    if '"Content"' not in s and '"content"' not in s:
        return []

    keys = ('"Content"', '"content"', '"text"')
    out: List[str] = []
    i = 0
    while i < len(s):
        # Find next key occurrence.
        j = -1
        key_hit = None
        for k in keys:
            pos = s.find(k, i)
            if pos != -1 and (j == -1 or pos < j):
                j = pos
                key_hit = k
        if j == -1 or key_hit is None:
            break

        # Find ':' after the key.
        colon = s.find(":", j + len(key_hit))
        if colon == -1:
            break

        # Find opening quote for the value.
        q0 = s.find('"', colon + 1)
        if q0 == -1:
            i = colon + 1
            continue

        # Scan until the next unescaped quote.
        buf: List[str] = []
        esc = False
        end = None
        for t in range(q0 + 1, len(s)):
            ch = s[t]
            if esc:
                # Preserve common escapes; downstream post-process will normalize whitespace.
                if ch == "n":
                    buf.append("\n")
                elif ch == "t":
                    buf.append("\t")
                elif ch == "r":
                    buf.append("\r")
                else:
                    buf.append(ch)
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                end = t
                break
            buf.append(ch)

        if end is None:
            # Unterminated string: take the rest.
            out.append("".join(buf).strip())
            break

        out.append("".join(buf).strip())
        i = end + 1

    # De-dupe while preserving order.
    seen = set()
    uniq: List[str] = []
    for x in out:
        x2 = str(x).strip()
        if not x2:
            continue
        if x2 in seen:
            continue
        seen.add(x2)
        uniq.append(x2)
    return uniq


def _parse_vibevoice_segments(text: str) -> List[Dict[str, Any]]:
    """Parse the model JSON output into a normalized list of segments."""
    if not text:
        return []

    json_str = _extract_json_str(text)
    if not json_str:
        return []

    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError:
        # Some deployments return JSON "as a string", resulting in escaped quotes:
        #   [{\"Start\":0,\"End\":1.0,...}]
        # Try to unescape common patterns and parse again.
        fixed = str(json_str).strip()
        if (fixed.startswith('"') and fixed.endswith('"')) or (fixed.startswith("'") and fixed.endswith("'")):
            fixed = fixed[1:-1]

        # Unescape a few times to handle both `\"` and `\\\"` style outputs.
        for _ in range(3):
            if '\\"' not in fixed and "\\n" not in fixed and "\\t" not in fixed and "\\r" not in fixed:
                break
            fixed = fixed.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
            fixed = fixed.replace('\\"', '"').replace("\\'", "'")

        try:
            obj = json.loads(fixed)
        except json.JSONDecodeError:
            return []

    if isinstance(obj, dict):
        items = [obj]
    elif isinstance(obj, list):
        items = obj
    else:
        return []

    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        out.append(
            {
                "start_time": item.get("start_time", item.get("Start time", item.get("Start"))),
                "end_time": item.get("end_time", item.get("End time", item.get("End"))),
                "speaker_id": item.get("speaker_id", item.get("Speaker ID", item.get("Speaker"))),
                "text": item.get("text", item.get("Content", item.get("content"))),
            }
        )

    # Drop empty items
    return [x for x in out if isinstance(x.get("text"), (str, int, float)) and str(x.get("text")).strip()]


def _extract_json_str(text: str) -> str:
    """Best-effort extraction of JSON from the model output."""
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    # Prefer a JSON array that starts like `[{...}]`.
    #
    # VibeVoice outputs sometimes include bracketed tokens like `[Music]` /
    # `[Laughter]` in the free-form transcript. A naive "first [ ... last ]"
    # slice will then start at `[Music]` and fail to JSON-parse.
    try:
        import re

        hits = list(re.finditer(r"\[\s*{", text))
        if hits:
            start = hits[-1].start()
            depth = 0
            end = -1
            for i in range(start, len(text)):
                ch = text[i]
                if ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end != -1:
                return text[start:end].strip()
    except Exception:
        # Best-effort only; fall back to the generic heuristics below.
        pass

    # Common case: the model outputs some extra text + a final JSON array.
    # Prefer a simple "first [ ... last ]" slice over naive bracket-depth
    # matching, because bracket chars can appear inside JSON strings.
    if "[" in text and "]" in text:
        start = text.find("[")
        end = text.rfind("]")
        if 0 <= start < end:
            return text[start : end + 1].strip()

    # Find the first JSON-looking bracket.
    lb = text.find("[")
    lc = text.find("{")
    starts = [x for x in (lb, lc) if x != -1]
    if not starts:
        return ""
    start = min(starts)

    # Naive bracket matching (good enough for typical model output).
    depth = 0
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end != -1:
        return text[start:end]
    return text[start:].strip()


def _time_to_ms(v: object) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(round(float(v) * 1000.0))

    s = str(v).strip()
    if not s:
        return 0
    low = s.lower()
    if low.endswith("s"):
        low = low[:-1].strip()
    if ":" in low:
        parts = low.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0
        if len(nums) == 2:
            minutes, seconds = nums
            return int(round((minutes * 60.0 + seconds) * 1000.0))
        if len(nums) == 3:
            hours, minutes, seconds = nums
            return int(round((hours * 3600.0 + minutes * 60.0 + seconds) * 1000.0))
        return 0
    try:
        return int(round(float(low) * 1000.0))
    except ValueError:
        return 0
