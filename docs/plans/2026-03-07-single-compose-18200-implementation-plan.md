# 单入口部署收敛（Port 18200）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make deployment unambiguous by providing exactly one recommended Docker Compose path (Router-only public entry), defaulting to port `18200`, plus one optional script to prefetch/warm up model weights. Move verbose reference material out of `README.md` into `docs/`.

**Architecture:** Replace root `docker-compose.yml` with a single-stack multi-service compose (router + internal model services), expose only the router port, move old compose files under `docker/compose/legacy/`, simplify `.env.example` to match the default stack, add `scripts/prefetch_models_docker.sh` (prefetch + warmup via Router), and update docs to reference only the new path.

**Tech Stack:** Docker Compose, Bash, curl, Python (json checks), existing Xiyu HTTP APIs.

---

### Task 1: Create the new single-stack `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

**Steps:**
1. Define services (no profiles):
   - `xiyu-router` (public): port mapping `${PORT:-18200}:8000`
   - `qwen3-asr` (internal): no host port
   - `xiyu-qwen3` (internal): no host port
   - `xiyu-pytorch`, `xiyu-onnx`, `xiyu-sensevoice`, `xiyu-whisper` (internal): no host ports
   - `xiyu-diarizer`, `xiyu-clearvoice` (internal): no host ports
2. Ensure router env forces **no VibeVoice** defaults:
   - `ROUTER_SHORT_BACKEND=qwen3`
   - `ROUTER_LONG_BACKEND=qwen3`
   - `ROUTER_FORCE_VIBEVOICE_WHEN_WITH_SPEAKER=false`
3. Ensure all services share volumes for caches (`huggingface-cache`, `modelscope-cache`, `model-cache`) and host `./data:/app/data`.

**Verification:**
- `docker compose config` renders without errors.

---

### Task 2: Move legacy compose files out of the repo root

**Files:**
- Move to: `docker/compose/legacy/` (keep in git, but no longer default)
  - `docker-compose.models.yml`
  - `docker-compose.cpu.yml`
  - `docker-compose.onnx.yml`
  - `docker-compose.sensevoice.yml`
  - `docker-compose.remote-asr.yml`
  - `docker-compose.benchmark.yml`

**Steps:**
1. Create `docker/compose/legacy/README.md` that explains these are legacy/advanced.
2. Update any scripts that referenced moved files (or remove/deprecate those scripts from docs).

**Verification:**
- `ls` at repo root shows only `docker-compose.yml` remaining as compose entry.

---

### Task 3: Simplify `.env.example` to the minimal “default stack”

**Files:**
- Modify: `.env.example`

**Steps:**
1. Keep only keys needed by the default stack + common network mirrors:
   - `PORT=18200`
   - proxy + `HF_ENDPOINT`
   - Qwen3-ASR: `QWEN3_MODEL_ID`, `QWEN3_MAX_MODEL_LEN`, `QWEN3_GPU_MEMORY_UTILIZATION`
   - Device placement: diarizer cpu, others cuda
   - ClearVoice defaults: `CLEARVOICE_MODEL=MossFormer2_48000Hz`, `CLEARVOICE_WARMUP_ON_STARTUP=true`
   - Speaker external diarizer enable + timeouts
   - `GOV_FORMAT_ENABLE=true`
2. Remove or relocate the long list of unused `PORT_*` variables (since internal services are not published).

**Verification:**
- `docker compose --env-file .env.example config` works.

---

### Task 4: Add a single “prefetch/warmup” script (optional)

**Files:**
- Create: `scripts/prefetch_models_docker.sh`
- Modify (or delete): `scripts/bootstrap_gov_meeting.sh` (remove from docs; keep as legacy or delete)

**Steps:**
1. Script behavior:
   - Ensure `.env` exists (copy from `.env.example` if missing).
   - `docker compose up -d --build`
   - Wait for `http://localhost:${PORT:-18200}/health`
   - Trigger:
     - ClearVoice via `POST /api/v1/preprocess/enhance` (forces denoise backend clearvoice)
     - Speaker diarizer path via `POST /api/v1/transcribe` with `with_speaker=true`
     - Each backend once via `target_backend=qwen3|whisper|pytorch|sensevoice|onnx`
   - Optionally `docker compose down` (default: stop; env `KEEP_RUNNING=true` to keep running)
2. Keep it resilient: retry loops and helpful diagnostics on failure.

**Verification:**
- Running the script on a machine with Docker succeeds and prints the final URL.

---

### Task 5: Documentation restructure (README short, details into docs)

**Files:**
- Modify: `README.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/WEB_UI.md`
- Add: `docs/FRONTEND.md`
- Add: `docs/API_EXAMPLES.md`
- Add: `docs/CONFIG.md`
- Add: `docs/PROJECT_STRUCTURE.md`

**Steps:**
1. Update docs to reference only the single entry URL `http://<server-ip>:18200` by default.
2. Move the big reference blocks out of README into the new docs (edit wording to avoid inaccurate claims like “WCAG AA certified”).

**Verification:**
- `rg \"8200|8000\" README.md docs -n` shows only intentional mentions (internal ports should be explained as internal only).

---

### Task 6: Verification + release

**Steps:**
1. Run unit tests in docker image:
   - `docker run --rm -v \"$PWD:/app\" -w /app xiyu-speech-service:pytorch sh -lc 'python -m pip install -q pytest && pytest -q'`
2. Run a minimal smoke test against Router (host port `18200`) if services are running.
3. Commit, push, and tag `v0.1.17` (next patch after current).

