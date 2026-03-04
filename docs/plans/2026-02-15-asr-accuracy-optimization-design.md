# Xiyu ASR Accuracy Optimization (File Transcription) — Design

**Date:** 2026-02-15  
**Owner:** Codex (with user approval)  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Reference projects:** CapsWriter-Offline (`/Users/luke/code/CapsWriter-Offline`), FunASR_API, SenseVoiceApi

## Goal

Improve **final transcription accuracy** for **file/video transcription** (HTTP APIs), prioritizing:

- Fewer **repetitions/duplication** when long audio is chunked
- Better handling of **chunk boundary drift** (partial words, clipped syllables)
- Higher correctness for **domain terms** without increasing false positives
- Better **numeric/format correctness** (ITN, punctuation, spacing) as part of “user-visible accuracy”

Time/latency is explicitly **not** the primary constraint for this track.

## Non-goals (for this track)

- Building a full public-facing security/auth story (separate concern)
- Replacing ASR models or training/fine-tuning models (may be later “upper bound” work)
- Achieving perfect sentence-level timestamp deduplication (nice-to-have; focus first on final text)

## Current state (Xiyu)

- File upload APIs convert media to **16kHz mono PCM16LE bytes** via FFmpeg (`src/api/dependencies.py`).
- `TranscriptionEngine` supports multiple backends, hotword/rule corrections, optional LLM polish (`src/core/engine.py`).
- Long-audio chunking exists (`TranscriptionEngine.transcribe_long_audio`, `src/core/audio/chunker.py`) but the HTTP API does not currently route to it automatically.
- Chunk merge currently uses **exact suffix/prefix overlap** heuristics, which fails under real ASR drift and causes repeated text on chunk boundaries (`src/core/audio/chunker.py`).
- Hotwords are currently used for both **model injection context** and **forced correction**, which can increase mis-correction risk when domain lists grow (`src/core/engine.py`).

## Key transferable insights from CapsWriter-Offline

CapsWriter-Offline implements product-grade “final text accuracy” techniques that are largely model-agnostic:

1) **Robust text merging for streaming/chunked ASR**
   - Merge is not limited to exact suffix/prefix matches.
   - Uses a tail-window search, allows skipping a small “noise prefix” in the new chunk, and can drop a small “drift tail” in the previous chunk.
   - Includes a fuzzy matching fallback when exact matching fails.

2) **Separate “context hotwords” from “forced replacement hotwords”**
   - Context hotwords improve ASR via injection/hints.
   - Forced hotwords are applied as post-correction and should be managed separately to reduce false positives.

3) **Defer irreversible formatting until after merging**
   - Apply ITN/punctuation/spacing at the final stage to avoid breaking overlap detection across chunks.

We will re-implement behaviors (not copy code verbatim), and validate with tests.

## Chosen approach

Start with a deterministic pipeline “foundation” that raises final text accuracy without changing model weights:

1) Decode → preprocess → ASR → (if long) chunk + robust merge → correction pipeline → final output
2) Introduce accuracy-oriented presets via per-request `asr_options`, but keep defaults safe and testable.

## Proposed pipeline (file transcription)

For file transcribe APIs:

1. Decode with FFmpeg to 16kHz mono PCM16LE
2. Convert to numpy waveform and compute duration
3. If duration exceeds threshold:
   - Chunk waveform with overlap
   - Transcribe each chunk without irreversible formatting
   - Merge chunk texts with robust “merge-by-text” algorithm
   - Apply correction pipeline to final text (and sentence texts)
4. Else:
   - Use current direct transcription path

## Per-request tuning via `asr_options` (HTTP)

To support fast A/B iteration and “accuracy-first” tuning without restarting services,
the HTTP transcription APIs accept an optional `asr_options` JSON string (multipart form field).

Key design rules:

- **Port selects model**: each backend/model runs in its own container and listens on its own port.
  The container’s `ASR_BACKEND` is fixed (pytorch/onnx/sensevoice/gguf/qwen3/vibevoice/router).
  `asr_options` is **not** allowed to switch backends at runtime.
- **Per-request overrides only**: options are applied to the *current request* and must not mutate
  global `settings` or shared singletons. This prevents cross-request leakage.
- **Validated + allowlisted**: `asr_options` is parsed + validated (unknown keys rejected) to avoid
  silent “options not applied” behavior and to keep the surface maintainable.
- **Route params win**: top-level request flags like `with_speaker`, `apply_hotword`, `apply_llm`
  keep their current semantics and are not overridden by `asr_options`.

Sections (v1):

- `preprocess`: audio preprocessing overrides (normalize/trim/denoise/vocal-sep, etc.)
- `chunking`: long-audio chunking + merge parameters (max chunk seconds, overlap, overlap_chars, workers)
- `backend`: backend-specific kwargs (filtered to avoid reserved keys like `input`, `hotword`, etc.)
- `postprocess`: post-processor overrides (ITN/spacing/punc restore/merge, etc.)

Hotwords:

- **Context hotwords**: used for ASR injection only
- **Force hotwords**: used for phoneme correction/rules/LLM prompt hints

## 20-plan roadmap (prioritized)

### Phase 1 — Final text stability (highest ROI)
01. Unify file transcription pipeline entry points (avoid divergent logic)
02. Add audio diagnostics (duration/rms/peak/snr/clipping ratio) for debug & tuning
03. Add robust “merge-by-text” utility (tail window search + skip prefix noise + fuzzy fallback)
04. Integrate robust merge into long-audio chunk merge
05. Route long audio to chunking automatically in HTTP API

### Phase 2 — Audio preprocessing (accuracy-first)
06. Add “accuracy-first” presets via `asr_options` for heavier preprocessing
07. Remove DC offset + add optional high-pass filtering
08. Add optional band-pass filtering + soft limiting (clipping mitigation)
09. Improve loudness normalization strategy (robust to very quiet/loud inputs)
10. Upgrade denoise gating (only enable when SNR low; fallback strategy)

### Phase 3 — Better segmentation & boundaries
11. Add time-based chunking strategy (CapsWriter-style fixed segments + overlap)
12. Boundary re-decode/overlap reconciliation (reduce missing/duplicated boundary tokens)
13. Merge sentence timestamps more robustly (reduce duplicate sentence artifacts)
14. Output per-chunk diagnostics for quality triage

### Phase 4 — Hotwords, rules, and formatting correctness
15. Separate context hotwords vs forced hotwords (file + watcher support)
16. Default injection to context hotwords (fallback to force list if context not configured)
17. Add optional built-in “spoken punctuation commands” rule set
18. Improve mixed English acronym merging (reduce “A I” / “V S Code” artifacts)
19. Ensure post-processing is consistent for full text + sentence texts + transcripts

### Phase 5 — Upper bound (time-for-accuracy)
20. Add an optional “precision mode” with multi-backend re-check (Paraformer/SenseVoice/remote) and strict LLM correction fallback

## Success criteria

For a representative long file (e.g. 5-minute video):

- Final text contains **materially fewer boundary duplicates** and fewer “stutter repeats”
- Domain term correctness improves without introducing obvious wrong replacements
- Numeric ITN and formatting are correct (when enabled) without degrading merge quality

Quantitative metrics (CER/WER, error taxonomy) will be added once we finalize the golden sample + reference TXT format.
