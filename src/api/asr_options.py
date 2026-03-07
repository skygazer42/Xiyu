"""Per-request ASR tuning options (`asr_options`) parsing + validation.

`asr_options` is provided as a JSON string in multipart form data.
We validate it early in the API layer to:
- fail fast with a clear 400 on bad inputs
- avoid silently ignoring typos / unknown keys
- keep the engine/backend kwargs surface predictable
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

__all__ = ["parse_asr_options"]


_TOP_LEVEL_KEYS = {"preprocess", "chunking", "backend", "postprocess", "speaker", "alignment", "debug"}

_PREPROCESS_KEYS = {
    "normalize_enable",
    "normalize_robust_rms_enable",
    "normalize_robust_rms_percentile",
    "target_db",
    "trim_silence_enable",
    "silence_threshold_db",
    "min_silence_ms",
    "denoise_enable",
    "denoise_prop",
    "denoise_backend",
    "vocal_separate_enable",
    "vocal_separate_model",
    "adaptive_enable",
    "snr_threshold",
    "remove_dc_offset",
    "highpass_enable",
    "highpass_cutoff_hz",
    "lowpass_enable",
    "lowpass_cutoff_hz",
    "bandpass_enable",
    "bandpass_low_hz",
    "bandpass_high_hz",
    "soft_limit_enable",
    "soft_limit_target",
    "soft_limit_knee",
}

_CHUNKING_KEYS = {
    "strategy",
    "max_chunk_duration_s",
    "min_chunk_duration_s",
    "overlap_duration_s",
    "silence_threshold_db",
    "min_silence_duration_s",
    "boundary_reconcile_enable",
    "boundary_reconcile_window_s",
    "max_workers",
    "infer_batch_size",
    "overlap_chars",
    # enterprise: chunk checkpointing/resume
    "checkpoint_enable",
    "checkpoint_id",
    "checkpoint_dir",
    "resume_skip_existing",
}

_POSTPROCESS_KEYS = {
    "filler_remove_enable",
    "filler_aggressive",
    "qj2bj_enable",
    "itn_enable",
    "itn_erhua_remove",
    "spacing_cjk_ascii_enable",
    "spoken_punc_enable",
    "acronym_merge_enable",
    "zh_convert_enable",
    "zh_convert_locale",
    "punc_convert_enable",
    "punc_add_space",
    "punc_restore_enable",
    "punc_restore_model",
    "punc_merge_enable",
    "trash_punc_enable",
    "trash_punc_chars",
}

_SPEAKER_KEYS = {
    "label_style",
    "turn_merge_enable",
    "turn_merge_gap_ms",
    "turn_merge_min_chars",
}

_ALIGNMENT_KEYS = {
    "enable",
    "level",
    "max_words",
}

_PREPROCESS_TYPES: Dict[str, str] = {
    "normalize_enable": "bool",
    "normalize_robust_rms_enable": "bool",
    "normalize_robust_rms_percentile": "number",
    "target_db": "number",
    "trim_silence_enable": "bool",
    "silence_threshold_db": "number",
    "min_silence_ms": "int",
    "denoise_enable": "bool",
    "denoise_prop": "number",
    "denoise_backend": "str",
    "vocal_separate_enable": "bool",
    "vocal_separate_model": "str",
    "adaptive_enable": "bool",
    "snr_threshold": "number",
    "remove_dc_offset": "bool",
    "highpass_enable": "bool",
    "highpass_cutoff_hz": "number",
    "lowpass_enable": "bool",
    "lowpass_cutoff_hz": "number",
    "bandpass_enable": "bool",
    "bandpass_low_hz": "number",
    "bandpass_high_hz": "number",
    "soft_limit_enable": "bool",
    "soft_limit_target": "number",
    "soft_limit_knee": "number",
}

_CHUNKING_TYPES: Dict[str, str] = {
    "strategy": "str",
    "max_chunk_duration_s": "number",
    "min_chunk_duration_s": "number",
    "overlap_duration_s": "number",
    "silence_threshold_db": "number",
    "min_silence_duration_s": "number",
    "boundary_reconcile_enable": "bool",
    "boundary_reconcile_window_s": "number",
    "max_workers": "int",
    "infer_batch_size": "int",
    "overlap_chars": "int",
    "checkpoint_enable": "bool",
    "checkpoint_id": "str",
    "checkpoint_dir": "str",
    "resume_skip_existing": "bool",
}

_POSTPROCESS_TYPES: Dict[str, str] = {
    "filler_remove_enable": "bool",
    "filler_aggressive": "bool",
    "qj2bj_enable": "bool",
    "itn_enable": "bool",
    "itn_erhua_remove": "bool",
    "spacing_cjk_ascii_enable": "bool",
    "spoken_punc_enable": "bool",
    "acronym_merge_enable": "bool",
    "zh_convert_enable": "bool",
    "zh_convert_locale": "str",
    "punc_convert_enable": "bool",
    "punc_add_space": "bool",
    "punc_restore_enable": "bool",
    "punc_restore_model": "str",
    "punc_merge_enable": "bool",
    "trash_punc_enable": "bool",
    "trash_punc_chars": "str",
}

_SPEAKER_TYPES: Dict[str, str] = {
    "label_style": "str",
    "turn_merge_enable": "bool",
    "turn_merge_gap_ms": "int",
    "turn_merge_min_chars": "int",
}

_ALIGNMENT_TYPES: Dict[str, str] = {
    "enable": "bool",
    "level": "str",
    "max_words": "int",
}


def parse_asr_options(asr_options_str: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse and validate `asr_options`.

    Args:
        asr_options_str: JSON string or None.

    Returns:
        Parsed dict or None.

    Raises:
        ValueError: on invalid JSON or schema violations.
    """
    if asr_options_str is None:
        return None

    s = str(asr_options_str).strip()
    if not s:
        return None

    # Basic payload size guard (avoid accidental huge form fields).
    if len(s) > 200_000:
        raise ValueError("asr_options is too large (max 200k chars)")

    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"asr_options is not valid JSON: {e.msg} (pos={e.pos})") from e

    if not isinstance(obj, dict):
        raise ValueError("asr_options must be a JSON object")

    unknown_top = [k for k in obj.keys() if k not in _TOP_LEVEL_KEYS]
    if unknown_top:
        raise ValueError(f"Unknown asr_options top-level keys: {unknown_top}")

    _validate_section(obj, "preprocess", allowed_keys=_PREPROCESS_KEYS)
    _validate_section(obj, "chunking", allowed_keys=_CHUNKING_KEYS)
    _validate_section(obj, "postprocess", allowed_keys=_POSTPROCESS_KEYS)
    _validate_section(obj, "speaker", allowed_keys=_SPEAKER_KEYS)
    _validate_section(obj, "alignment", allowed_keys=_ALIGNMENT_KEYS)
    _validate_section_types(obj, "preprocess", type_map=_PREPROCESS_TYPES)
    _validate_section_types(obj, "chunking", type_map=_CHUNKING_TYPES)
    _validate_section_types(obj, "postprocess", type_map=_POSTPROCESS_TYPES)
    _validate_section_types(obj, "speaker", type_map=_SPEAKER_TYPES)
    _validate_section_types(obj, "alignment", type_map=_ALIGNMENT_TYPES)

    chunking = obj.get("chunking") or {}
    if isinstance(chunking, dict) and "strategy" in chunking:
        strategy = chunking.get("strategy")
        if isinstance(strategy, str):
            normalized = strategy.strip().lower()
        else:
            normalized = None
        if normalized not in ("silence", "time"):
            raise ValueError("asr_options.chunking.strategy must be one of: silence, time")
        chunking["strategy"] = normalized

    backend = obj.get("backend")
    if backend is not None:
        if not isinstance(backend, dict):
            raise ValueError("asr_options.backend must be an object")
        for k, v in backend.items():
            if not isinstance(k, str):
                raise ValueError("asr_options.backend keys must be strings")
            if not _is_json_primitive_or_list(v):
                raise ValueError(
                    f"asr_options.backend[{k!r}] must be a JSON primitive or list of primitives"
                )

    debug = obj.get("debug")
    if debug is not None:
        if not isinstance(debug, dict):
            raise ValueError("asr_options.debug must be an object")
        # Keep debug section flexible for now, but avoid deeply nested blobs.
        for k, v in debug.items():
            if not isinstance(k, str):
                raise ValueError("asr_options.debug keys must be strings")
            if not _is_json_primitive_or_list(v):
                raise ValueError(
                    f"asr_options.debug[{k!r}] must be a JSON primitive or list of primitives"
                )

    # Numeric sanity checks (best-effort; deeper validation happens in engine).
    _validate_ranges(obj)

    speaker = obj.get("speaker") or {}
    if isinstance(speaker, dict) and "label_style" in speaker:
        style = speaker.get("label_style")
        if isinstance(style, str):
            normalized = style.strip().lower()
        else:
            normalized = None
        if normalized not in ("zh", "numeric"):
            raise ValueError("asr_options.speaker.label_style must be one of: zh, numeric")
        speaker["label_style"] = normalized

    alignment = obj.get("alignment") or {}
    if isinstance(alignment, dict) and "level" in alignment:
        lvl = alignment.get("level")
        if isinstance(lvl, str):
            normalized = lvl.strip().lower()
        else:
            normalized = None
        if normalized not in ("word", "char"):
            raise ValueError("asr_options.alignment.level must be one of: word, char")
        alignment["level"] = normalized

    return obj


def _validate_section(obj: Dict[str, Any], key: str, *, allowed_keys: set[str]) -> None:
    section = obj.get(key)
    if section is None:
        return
    if not isinstance(section, dict):
        raise ValueError(f"asr_options.{key} must be an object")

    unknown = [k for k in section.keys() if k not in allowed_keys]
    if unknown:
        raise ValueError(f"Unknown asr_options.{key} keys: {unknown}")


def _validate_section_types(obj: Dict[str, Any], key: str, *, type_map: Dict[str, str]) -> None:
    section = obj.get(key)
    if section is None:
        return
    if not isinstance(section, dict):
        return

    for k, v in section.items():
        expected = type_map.get(k)
        if expected is None:
            continue
        if expected == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"asr_options.{key}.{k} must be a boolean")
        elif expected == "str":
            if not isinstance(v, str):
                raise ValueError(f"asr_options.{key}.{k} must be a string")
        elif expected == "int":
            if not _is_int(v):
                raise ValueError(f"asr_options.{key}.{k} must be an integer")
        elif expected == "number":
            if not _is_number(v):
                raise ValueError(f"asr_options.{key}.{k} must be a number")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_json_primitive_or_list(v: Any) -> bool:
    if v is None or isinstance(v, (str, int, float, bool)):
        return True
    if isinstance(v, list):
        return all(x is None or isinstance(x, (str, int, float, bool)) for x in v)
    return False


def _validate_ranges(obj: Dict[str, Any]) -> None:
    preprocess = obj.get("preprocess") or {}
    if isinstance(preprocess, dict):
        if "denoise_prop" in preprocess:
            prop = preprocess.get("denoise_prop")
            if isinstance(prop, (int, float)) and not (0.0 <= float(prop) <= 1.0):
                raise ValueError("asr_options.preprocess.denoise_prop must be within [0, 1]")
        if "normalize_robust_rms_percentile" in preprocess:
            pct = preprocess.get("normalize_robust_rms_percentile")
            if isinstance(pct, (int, float)) and not (0.0 <= float(pct) <= 100.0):
                raise ValueError(
                    "asr_options.preprocess.normalize_robust_rms_percentile must be within [0, 100]"
                )
        if "highpass_cutoff_hz" in preprocess:
            cutoff = preprocess.get("highpass_cutoff_hz")
            if isinstance(cutoff, (int, float)) and not (float(cutoff) > 0.0):
                raise ValueError("asr_options.preprocess.highpass_cutoff_hz must be > 0")
        if "lowpass_cutoff_hz" in preprocess:
            cutoff = preprocess.get("lowpass_cutoff_hz")
            if isinstance(cutoff, (int, float)) and not (float(cutoff) > 0.0):
                raise ValueError("asr_options.preprocess.lowpass_cutoff_hz must be > 0")
        if "bandpass_low_hz" in preprocess or "bandpass_high_hz" in preprocess:
            low = preprocess.get("bandpass_low_hz")
            high = preprocess.get("bandpass_high_hz")
            if isinstance(low, (int, float)) and not (float(low) > 0.0):
                raise ValueError("asr_options.preprocess.bandpass_low_hz must be > 0")
            if isinstance(high, (int, float)) and not (float(high) > 0.0):
                raise ValueError("asr_options.preprocess.bandpass_high_hz must be > 0")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                if not (float(low) < float(high)):
                    raise ValueError(
                        "asr_options.preprocess.bandpass_low_hz must be < bandpass_high_hz"
                    )
        if "soft_limit_target" in preprocess:
            target = preprocess.get("soft_limit_target")
            if isinstance(target, (int, float)) and not (0.0 < float(target) <= 1.0):
                raise ValueError("asr_options.preprocess.soft_limit_target must be within (0, 1]")
        if "soft_limit_knee" in preprocess:
            knee = preprocess.get("soft_limit_knee")
            if isinstance(knee, (int, float)) and not (float(knee) > 0.0):
                raise ValueError("asr_options.preprocess.soft_limit_knee must be > 0")

    chunking = obj.get("chunking") or {}
    if isinstance(chunking, dict):
        for k in ("max_chunk_duration_s", "min_chunk_duration_s", "overlap_duration_s", "min_silence_duration_s"):
            if k in chunking:
                v = chunking.get(k)
                if isinstance(v, (int, float)) and float(v) < 0.0:
                    raise ValueError(f"asr_options.chunking.{k} must be >= 0")
        if "boundary_reconcile_window_s" in chunking:
            bw = chunking.get("boundary_reconcile_window_s")
            if isinstance(bw, (int, float)) and float(bw) < 0.0:
                raise ValueError("asr_options.chunking.boundary_reconcile_window_s must be >= 0")
        if "max_workers" in chunking:
            mw = chunking.get("max_workers")
            if isinstance(mw, int) and mw < 1:
                raise ValueError("asr_options.chunking.max_workers must be >= 1")
        if "overlap_chars" in chunking:
            oc = chunking.get("overlap_chars")
            if isinstance(oc, int) and oc < 0:
                raise ValueError("asr_options.chunking.overlap_chars must be >= 0")

    speaker = obj.get("speaker") or {}
    if isinstance(speaker, dict):
        if "turn_merge_gap_ms" in speaker:
            gap = speaker.get("turn_merge_gap_ms")
            if isinstance(gap, int) and gap < 0:
                raise ValueError("asr_options.speaker.turn_merge_gap_ms must be >= 0")
        if "turn_merge_min_chars" in speaker:
            mc = speaker.get("turn_merge_min_chars")
            if isinstance(mc, int) and mc < 0:
                raise ValueError("asr_options.speaker.turn_merge_min_chars must be >= 0")

    alignment = obj.get("alignment") or {}
    if isinstance(alignment, dict):
        if "max_words" in alignment:
            mw = alignment.get("max_words")
            if isinstance(mw, int) and mw <= 0:
                raise ValueError("asr_options.alignment.max_words must be > 0")
