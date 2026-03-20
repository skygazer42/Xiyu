# Meeting Overview (Gov-Style) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically generate a 2-5 paragraph, government-style meeting overview via LLM after transcription, enabled by default and controllable from the Web UI.

**Architecture:** Add a new LLM role (`gov_overview`), a lightweight overview generator module, and integrate it into the API layer. For sync transcription, generate the overview in a background task and return `overview_task_id`. For async transcription tasks, compute the overview inside the task result. Expose `meeting_overview_enable` as a mutable config toggle and display the overview in the frontend.

**Tech Stack:** FastAPI, Pydantic v2, existing `LLMClient`, existing in-memory `TaskManager`, React + Vite (frontend), React Query + Zustand stores.

---

### Task 1: Add Settings Keys (Default Enabled)

**Files:**
- Modify: `src/config.py`

**Step 1: Write the failing test**

Add a unit test that asserts the new setting exists and defaults to enabled.

```python
def test_settings_meeting_overview_defaults(monkeypatch):
    from src.config import Settings
    s = Settings()
    assert s.meeting_overview_enable is True
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_settings_postprocess_toggles.py::test_settings_meeting_overview_defaults -q`
Expected: FAIL with AttributeError (setting missing)

**Step 3: Implement minimal settings**

In `Settings(BaseSettings)` add:
- `meeting_overview_enable: bool = True`
- `meeting_overview_auto: bool = True`
- `meeting_overview_role: str = "gov_overview"`
- `meeting_overview_max_input_chars: int = 12000`
- `meeting_overview_chunk_chars: int = 6000`

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/... -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py tests/<new-or-updated-test>.py
git commit -m "feat: add meeting overview settings"
```

---

### Task 2: Add Gov Overview LLM Role

**Files:**
- Create: `src/core/llm/roles/gov_overview.py`
- Modify: `src/core/llm/roles/__init__.py`
- Test: `tests/test_roles.py`

**Step 1: Write the failing test**

Add a test ensuring `get_role("gov_overview")` returns a role with a system prompt that enforces:
- 2-5 paragraphs
- official tone
- no fabrication

```python
def test_role_gov_overview_registered():
    from src.core.llm.roles import get_role
    r = get_role("gov_overview")
    assert r.name == "gov_overview"
    assert "2-5" in r.system_prompt or "2 到 5" in r.system_prompt
    assert "不得编造" in r.system_prompt
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_roles.py::test_role_gov_overview_registered -q`
Expected: FAIL (role not found / falls back to default)

**Step 3: Implement role**

Create `gov_overview.py`:
- register with `@RoleRegistry.register`
- `name = "gov_overview"`
- system prompt requirements:
  - formal, third-person, government comms style
  - output exactly 2-5 paragraphs (no bullets, no headings)
  - must not invent facts, numbers, names, institutions
  - if uncertain, omit details

Update roles `__init__.py` to import `GovOverviewRole` so it is registered.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_roles.py::test_role_gov_overview_registered -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/llm/roles/gov_overview.py src/core/llm/roles/__init__.py tests/test_roles.py
git commit -m "feat: add gov_overview LLM role"
```

---

### Task 3: Implement Overview Generator (Chunking + Two-Phase)

**Files:**
- Create: `src/core/meeting_overview.py`
- Test: `tests/test_meeting_overview.py`

**Step 1: Write failing tests (pure, no network)**

Mock `LLMClient.chat` so tests are deterministic.

Test cases:
- builds source text from `speaker_turns` (no timestamps)
- falls back to `sentences` or `text`
- chunking splits long text
- two-phase mode calls the LLM multiple times

```python
def test_build_source_text_prefers_speaker_turns():
    from src.core.meeting_overview import build_overview_source_text
    src = build_overview_source_text({
        "speaker_turns": [{"speaker": "说话人甲", "text": "大家好", "start": 0, "end": 1000}],
        "text": "ignored",
    })
    assert "说话人甲" in src and "大家好" in src
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -q tests/test_meeting_overview.py -q`
Expected: FAIL (module missing)

**Step 3: Implement minimal module**

Implement:
- `build_overview_source_text(result: dict) -> str`
- `chunk_text(text: str, chunk_chars: int) -> list[str]`
- `async_generate_overview(text: str, *, role: str, max_input_chars: int, chunk_chars: int) -> str`
  - if `len(text) <= max_input_chars`: single pass
  - else:
    - call LLM per chunk to extract short factual notes
    - call LLM once to synthesize final 2-5 paragraphs
- `generate_overview_sync(text: str, **kwargs) -> str` wrapper for `TaskManager` (uses `asyncio.run`)

Do not import ASR engine or model manager here. Only use `settings`, `LLMClient`, and `get_role`.

**Step 4: Run tests to verify pass**

Run: `python3 -m pytest -q tests/test_meeting_overview.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/meeting_overview.py tests/test_meeting_overview.py
git commit -m "feat: add meeting overview generator"
```

---

### Task 4: Expose Runtime Toggle via /config

**Files:**
- Modify: `src/api/routes/config.py`
- Test: `tests/test_settings_postprocess_toggles.py` or a new config test

**Step 1: Write failing test**

Ensure `meeting_overview_enable` is in the mutable list and returned by `/config` endpoints.

**Step 2: Run test (fail)**

Run: `python3 -m pytest -q tests/test_api_http.py::test_config_endpoints -q` (or create a new one)

**Step 3: Implement**

Add `meeting_overview_enable` to `MUTABLE_CONFIG_KEYS`.
No extra side effects required beyond toggling the boolean.

**Step 4: Run tests**

Expected: PASS

**Step 5: Commit**

```bash
git add src/api/routes/config.py tests/<...>.py
git commit -m "feat: allow toggling meeting overview via config"
```

---

### Task 5: Add API Response Fields (overview / overview_task_id)

**Files:**
- Modify: `src/api/schemas.py`
- Modify: `frontend/src/lib/api/types.ts`
- Test: `tests/test_api_http.py`

**Step 1: Write failing test**

Update an API test to accept the new optional fields and ensure schema validation is correct.

**Step 2: Implement backend schema**

In `TranscribeResponse` add:
- `overview: Optional[str] = None`
- `overview_task_id: Optional[str] = None`

**Step 3: Implement frontend types**

In `TranscribeResponse` TS interface add:
- `overview?: string | null`
- `overview_task_id?: string | null`

**Step 4: Run tests**

Run: `python3 -m pytest -q tests/test_api_http.py::test_transcribe_endpoint -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/api/schemas.py frontend/src/lib/api/types.ts tests/test_api_http.py
git commit -m "feat: add meeting overview fields to API responses"
```

---

### Task 6: Integrate Overview into Sync Transcription (Background Task)

**Files:**
- Modify: `src/api/routes/transcribe.py`
- Modify: `src/api/routes/async_transcribe.py` (result typing only if needed)
- Modify: `src/core/task_manager.py` (register handler if using TaskManager)
- Test: `tests/test_api_http.py` + new test for overview wiring

**Step 1: Write failing test**

Test behavior:
- when `settings.llm_enable=True` and `settings.meeting_overview_enable=True`,
  `POST /api/v1/transcribe` returns `overview_task_id`.
- the task result via `/api/v1/result` returns `{"overview": "..."}` when finished.

Mock overview generation to avoid network.

**Step 2: Implement**

- Register `meeting_overview` handler on startup/import (recommended: in `src/core/meeting_overview.py`, or in a small import from `transcribe.py`).
- In `transcribe_audio(...)` after transcription success:
  - build `source_text`
  - submit task: `task_manager.submit("meeting_overview", {"text": source_text, "role": settings.meeting_overview_role, ...})`
  - set `overview_task_id` in response

Ensure failures in overview submission do not fail transcription.

**Step 3: Run tests**

Run: `python3 -m pytest -q tests/test_api_http.py::test_transcribe_endpoint -q`
Run: `python3 -m pytest -q tests/<new_test_file>.py -q`
Expected: PASS

**Step 4: Commit**

```bash
git add src/api/routes/transcribe.py src/core/meeting_overview.py tests/<...>.py
git commit -m "feat: auto-generate meeting overview for sync transcribe"
```

---

### Task 7: Integrate Overview into Async Transcription Tasks (Inline in Result)

**Files:**
- Modify: `src/api/routes/async_transcribe.py`
- Test: `tests/test_async_transcribe.py`

**Step 1: Write failing test**

When async transcription completes successfully and overview is enabled, the returned `data` includes `overview` text (no second task id required).

**Step 2: Implement**

In `_handle_url_transcribe` and `_handle_file_transcribe`:
- after `result = transcription_engine.transcribe_long_audio(...)`:
  - build overview source text from result
  - call the overview generator (sync wrapper) and set `overview` in returned dict
- Wrap in try/except so overview failure does not fail the task.

**Step 3: Run tests**

Run: `python3 -m pytest -q tests/test_async_transcribe.py -q`

**Step 4: Commit**

```bash
git add src/api/routes/async_transcribe.py tests/test_async_transcribe.py
git commit -m "feat: auto-generate meeting overview for async tasks"
```

---

### Task 8: Frontend Toggle (Config Page)

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx`

**Step 1: Implement UI**

Add a new section or item (recommended near LLM settings):
- key: `meeting_overview_enable`
- label: `会议概览`
- description: `转写完成后自动生成 2-5 段政务口径概览（需启用 LLM）`

**Step 2: Manual verify**

Run (frontend): `cd frontend && npm run dev`
Verify:
- Config page can toggle meeting overview
- Save triggers `/config` update

**Step 3: Commit**

```bash
git add frontend/src/pages/ConfigPage.tsx
git commit -m "feat(frontend): add meeting overview toggle"
```

---

### Task 9: Frontend Display (Overview Card)

**Files:**
- Modify: `frontend/src/pages/TranscribePage.tsx`
- Modify: `frontend/src/lib/api/types.ts` (task result union, if needed)

**Step 1: Implement UI**

Behavior:
- If `result.overview` exists: show it in a card above transcript.
- Else if `result.overview_task_id` exists: poll `/api/v1/result` until it returns `{overview}` and then display.
- Show "生成中" status and allow retry (optional v1: a simple refresh button).

**Step 2: Manual verify**

Use a mocked backend or real backend to confirm:
- After transcription, overview shows (immediate or after polling).

**Step 3: Commit**

```bash
git add frontend/src/pages/TranscribePage.tsx frontend/src/lib/api/types.ts
git commit -m "feat(frontend): show meeting overview after transcription"
```

---

### Task 10: Update API Docs

**Files:**
- Modify: `docs/API.md`

**Steps:**

- Document `overview` and `overview_task_id` in `/api/v1/transcribe` response.
- Document polling flow using `/api/v1/result` for `overview_task_id`.
- Mention config toggle `meeting_overview_enable` and dependency on `LLM_ENABLE`.

**Commit**

```bash
git add docs/API.md
git commit -m "docs: document meeting overview API"
```

---

### Task 11: Final Verification

Run targeted tests:

- `python3 -m pytest -q tests/test_api_http.py -q`
- `python3 -m pytest -q tests/test_meeting_overview.py -q`
- `python3 -m pytest -q tests/test_async_transcribe.py -q`

Optional (if environment supports full deps): `python3 -m pytest -q`

---

