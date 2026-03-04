# Recall/Meeting Transcription — Context Hotwords + `text_accu` Design

**Date:** 2026-02-16

**Goal:** Improve *final* (readability-first) transcript accuracy for recall/meeting transcription by:
1) separating **forced** hotword correction vs **context** hotword injection, and
2) adding a **precision merge** output (`text_accu`) for long-audio chunk overlap de-duplication.

---

## Background

Recall/meeting transcription typically suffers from:
- **domain terms / names** (产品名、项目代号、人名) being misrecognized
- **long-audio chunk overlap** causing duplicated phrases or missing boundary syllables

Industry ASR systems commonly support some form of **contextual biasing / phrase hints**
(phrase lists / custom vocabulary / speech adaptation), which is closer to “suggest” than “force”.

---

## A) Hotwords split: Forced vs Context

### Motivation

Forced replacement is useful, but too aggressive for meeting transcripts:
- it can introduce false positives (wrongly replacing valid words)
- it mixes two goals: (1) correcting *known* errors vs (2) hinting *likely* domain terms

### Design

We introduce two hotword lists:

1) **Forced hotwords** (纠错用、强制替换)
- File: `data/hotwords/hotwords.txt`
- Loaded into `PhonemeCorrector` and used in the correction pipeline.

2) **Context hotwords** (提示用、仅注入)
- File: `data/hotwords/hotwords-context.txt`
- Used only for forward injection (`hotword` / prompt hints) into ASR/remote backends.
- Not used for forced replacement.

### Injection selection rule

`TranscriptionEngine._get_injection_hotwords()`:
- if request provides `hotwords`: use it (highest priority)
- else if context hotwords exist: inject context list
- else fallback to forced list

### Hot reload

Watcher supports a configurable filename list so deployments can override file names via env vars:
- `HOTWORDS_FILE` → forced list filename
- `HOTWORDS_CONTEXT_FILE` → context list filename

---

## C) `text_accu`: Precision merge output for long audio

### Motivation

Long audio chunking uses overlap to avoid boundary truncation, but overlap introduces:
- duplicated phrases (both chunks include the same words)
- boundary misalignment (small drift between chunks)

### Design

We compute two merged texts:

1) `text` (robust): current CapsWriter-inspired `merge_by_text` heuristic
2) `text_accu` (precision): time-windowed `SequenceMatcher` alignment inspired by CapsWriter’s
   token timestamp merge, adapted for Xiyu:
   - backends do not provide stable token timestamps across models
   - we approximate timestamps at the **character** level via linear interpolation over chunk time

`text_accu` is designed to be “reading-first” (dedupe more aggressively).

### API surface

We add an optional field to the HTTP response:
- `text_accu: Optional[str]`

This keeps existing clients stable while enabling recall/meeting UIs to prefer `text_accu`.

---

## Notes / Future work

- If we later add stable word/token timestamps from certain backends, we can upgrade `text_accu`
  to do true token-level alignment (closer to CapsWriter’s `text_accu`).
- Meeting transcription quality also depends heavily on diarization & overlap speech handling.
