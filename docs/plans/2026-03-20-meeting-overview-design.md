# Meeting Overview (Gov-Style) Design

> Date: 2026-03-20
>
> Status: Approved

## Goal

After a meeting transcription finishes, automatically generate a **2-5 paragraph** meeting overview in a **formal, government-style** tone (similar to official briefings / communiques).

The overview is intended for政务场景: concise, official, third-person, and **strictly grounded in the transcript**.

## Non-Goals

- Not producing a structured "minutes" (no mandatory agenda/decisions/action items schema in v1).
- Not replacing the existing transcript polishing roles (`meeting`, `policy_meeting*`) which explicitly forbid summarization.
- Not guaranteeing persistence across process restarts (v1 uses in-memory task results).

## User Experience

- Users upload audio and get the transcript quickly (existing behavior).
- Overview generation runs automatically in the background.
- Frontend shows an "overview" section when ready, and provides a toggle to enable/disable generation.

## Key Requirements

- Output: readable official overview text, **2-5 paragraphs**.
- **No hallucination / no fabrication**:
  - Must not invent numbers, names, institutions, policies, conclusions, or action items not present in the transcript.
  - If uncertain, omit details or use conservative phrasing ("会议围绕...进行了交流").
- Best-effort: overview failures must not fail the core transcription request.
- Default enabled; frontend can control enable/disable at runtime.

## Proposed Approaches (Considered)

1. Synchronous inline overview in `/api/v1/transcribe` response.
   - Rejected for v1: increases latency and risk of timeouts for long meetings.
2. **Asynchronous background overview task** (recommended).
   - Transcript returns fast, overview is fetched later using the existing `/api/v1/result` task polling.
3. Persistent job artifact (write overview under `data/outputs/` and fetch by job id).
   - Deferred: adds lifecycle/cleanup and permissions complexity.

## Architecture (Recommended v1)

### Components

- LLM invocation is already available via `src/core/llm/client.py` (`LLMClient`) and role prompts under `src/core/llm/roles/`.
- Async task infrastructure exists via `src/core/task_manager.py` and `/api/v1/result` in `src/api/routes/async_transcribe.py`.

We will add:

- A new LLM role: `gov_overview` (system prompt designed for official meeting overview summarization).
- A new task type in `TaskManager`: `meeting_overview`.
- A small service helper to construct LLM input from transcript artifacts and to chunk long inputs.

### Data Flow

1. `POST /api/v1/transcribe` (and selected endpoints) runs transcription as today.
2. If overview is enabled:
   - Submit a `meeting_overview` task with transcript payload (prefer speaker_turns/transcript if available).
   - Return `overview_task_id` in the transcription response.
3. Frontend polls `POST /api/v1/result` with `overview_task_id` until completed.
4. The completed task returns `{ "overview": "<2-5 paragraphs>" }`.

### Long Transcript Handling

To prevent exceeding the LLM context window:

- If transcript length <= `meeting_overview_max_input_chars`: single-pass summarization.
- Else: two-phase summarization:
  1. Chunk extraction: each chunk -> short factual bullet-ish "notes" (not user-facing).
  2. Final synthesis: notes -> final 2-5 paragraphs in official tone.

## API Changes

### Transcription Responses

Add optional fields to relevant response models:

- `overview_task_id: Optional[str]`
- (Optional future) `overview: Optional[str]` (reserved; not used in v1)

### Overview Retrieval

Reuse existing endpoint:

- `POST /api/v1/result` (task polling)
  - On success: `data` contains `{"overview": "..."}`.
  - On failure: task status is `FAILED` and error message is returned by existing semantics.

## Configuration and Frontend Control

Add settings (env vars) with sensible defaults:

- `MEETING_OVERVIEW_ENABLE=true` (default enabled)
- `MEETING_OVERVIEW_AUTO=true` (auto-generate after transcription)
- `MEETING_OVERVIEW_ROLE=gov_overview`
- `MEETING_OVERVIEW_MAX_INPUT_CHARS=12000`
- `MEETING_OVERVIEW_CHUNK_CHARS=6000`
- `MEETING_OVERVIEW_TURNS_PER_CHUNK=30` (if using speaker_turns)

Runtime toggle (frontend control):

- Expose `meeting_overview_enable` in `/config`:
  - Include in `MUTABLE_CONFIG_KEYS` so frontend can toggle enable/disable without restart.

Effective enablement rule:

- Overview runs only when both:
  - `LLM_ENABLE=true`
  - `MEETING_OVERVIEW_ENABLE=true`

If LLM is disabled, overview is skipped (no task submitted).

## Error Handling

- Overview is best-effort:
  - If overview generation fails, transcription still succeeds.
  - Overview task is marked `FAILED` with a concise error string.
- Add a hard timeout for LLM calls in overview generation (separate from transcription timeouts).

## Security / Privacy Considerations

- Overview generation sends transcript text to the configured LLM backend (`LLM_BASE_URL`).
- If LLM backend is remote, this is a data egress. Deployment must ensure the LLM endpoint is approved for政务数据.
- Do not log full transcript/overview at INFO level (only sizes/ids).

## Testing Strategy

- Unit tests for:
  - chunking logic and two-phase flow (LLM mocked)
  - API response includes `overview_task_id` when enabled
  - `/api/v1/result` returns overview payload when task completes
- Update `docs/API.md` to document the new field(s) and flow.

