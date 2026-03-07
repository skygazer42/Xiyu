# Gov Meeting Bootstrap Stack Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide a one-command “政务会议默认栈” bootstrap that brings up the recommended multi-model deployment (Router + Qwen3-ASR-1.7B + Whisper large-v3 + FunASR + SenseVoice + ClearVoice + external diarizer), **without VibeVoice**, and pre-triggers model downloads/warmups as much as possible.

**Architecture:** Add a repository-local shell script that (1) creates `.env` from `.env.example` when missing, (2) starts the required Compose profiles from `docker-compose.models.yml` with `--build`, (3) waits for key health endpoints to be ready and triggers minimal warmups/downloads (remote `/v1/models`, ClearVoice `/api/v1/enhance`, then run the existing smoke script). Update `.env.example` defaults to better match this stack (enable ClearVoice warmup) and document the recommended “no VibeVoice” startup path in `docs/MODELS.md` and `README.md`.

**Tech Stack:** Bash, Docker Compose, curl, python3, existing `scripts/smoke_all_endpoints.sh`.

---

### Task 1: Document the “no VibeVoice” default stack

**Files:**
- Modify: `docs/MODELS.md`
- Modify: `README.md`

**Steps:**
1. Add a “政务会议推荐（不启 VibeVoice）” section with:
   - One-line command: `./scripts/bootstrap_gov_meeting.sh`
   - Equivalent Compose command (for people who don’t want scripts)
2. Ensure existing “all” instructions clearly mention it includes VibeVoice (so users don’t accidentally pull/run it).

**Verification:**
- Read docs quickly for consistency with ports: Router `:8200`, Qwen3 wrapper `:8201`, diarizer `:8300`, ClearVoice `:8400`.

---

### Task 2: Adjust `.env.example` defaults for the meeting stack

**Files:**
- Modify: `.env.example`

**Steps:**
1. Keep Router default port as `PORT_XIYU_ROUTER=8200` and ensure Router routes long/short to Qwen3 by default (no VibeVoice).
2. Set `CLEARVOICE_WARMUP_ON_STARTUP=true` so the ClearVoice microservice downloads/loads weights on container startup.

**Verification:**
- `rg "CLEARVOICE_WARMUP_ON_STARTUP" .env.example` shows `true`.

---

### Task 3: Add a bootstrap script for the default stack (no VibeVoice)

**Files:**
- Create: `scripts/bootstrap_gov_meeting.sh`

**Behavior:**
- If `.env` missing: copy from `.env.example`.
- Start `docker-compose.models.yml` with profiles:
  - `router qwen3 pytorch onnx sensevoice whisper diarizer clearvoice`
- Wait for:
  - Router: `http://localhost:${PORT_XIYU_ROUTER}/health`
  - Qwen3-ASR remote server: `http://localhost:${PORT_QWEN3_ASR}/v1/models`
  - Diarizer: `http://localhost:${PORT_DIARIZER}/health`
  - ClearVoice: `http://localhost:${PORT_CLEARVOICE}/health` and run one `POST /api/v1/enhance` to trigger weights
- Run smoke test:
  - `PORTS="<router + local backends + qwen3 wrapper>" scripts/smoke_all_endpoints.sh`

**Verification:**
- `bash scripts/bootstrap_gov_meeting.sh` prints the Web UI address and exits 0 when services are up.

---

### Task 4: Verification + release

**Files:**
- (none)

**Steps:**
1. Run unit tests in the Docker image:
   - `docker run --rm -v "$PWD:/app" -w /app xiyu-speech-service:pytorch sh -lc 'python -m pip install -q pytest && pytest -q'`
2. Commit changes and push to `origin/main`.
3. Tag: bump patch version (next tag after `v0.1.15`).

