# Deployment Docs (From Zero → Full Stack) — Design

**Date:** 2026-02-20  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary goal:** ship complete deployment documentation that takes a user **from 0 to a working full stack**:

- Linux + NVIDIA GPU (Docker Compose recommended path)
- macOS / Windows (Docker Desktop; CPU-first path)
- Local Python (no Docker) option, including **one-command meeting stack** launcher
- Multi-model “one backend per port” deployment (`docker-compose.models.yml`)
- Remote ASR servers (Qwen3-ASR / VibeVoice-ASR) + Xiyu wrappers
- Caching strategy (weights in volumes/host mounts, not baked into images)

---

## Current state

- `README.md` contains a solid quick start and many details, but it is becoming long.
- `docs/API.md` documents endpoints well.
- We now also have `scripts/local_stack.py` (meeting mode local launcher) and `docker-compose.models.yml --profile all`.

---

## Problems to solve

1) **No single “from zero” deployment doc**
   - Users must piece together commands across README/compose files.

2) **Platform differences are not explicit**
   - Linux GPU is different from macOS/Windows Docker Desktop.
   - Windows can be CPU (Docker Desktop) or GPU (WSL2 + NVIDIA), but this needs clear disclaimers.

3) **Full stack is multi-layer**
   - Xiyu service(s) vs remote ASR servers.
   - Speaker diarization may come from backend native speaker models OR an external diarizer service.

---

## Goals

1) Provide a clean documentation entry point:
   - `docs/DEPLOYMENT.md` (main line)

2) Keep docs maintainable by splitting the “big surface area”:
   - `docs/MODELS.md` for multi-model + remote ASR specifics
   - `docs/TROUBLESHOOTING.md` for common failures

3) Keep README shorter:
   - keep quick start
   - link to the three docs for the full story

4) Concrete commands:
   - install/verify steps (where possible)
   - how to validate the service is working (`/health`, `/docs`)
   - how to locate logs / caches

---

## Non-goals (v1)

- Prescriptive driver versions (too variable).
- Full production hardening guide (TLS, auth, rate limiting, backups). We’ll add “production notes” but keep it lightweight.

---

## Proposed doc structure

### 1) `docs/DEPLOYMENT.md`

The end-to-end “happy path” with clear platform branches:

1. Requirements & hardware notes
2. Install Docker/Compose (and GPU toolkit on Linux)
3. Clone repo + `.env` setup
4. Start service (GPU / CPU)
5. Verify service (health/docs/UI)
6. Optional: local Python usage (single service)
7. Optional: local meeting stack one-command launcher (`scripts/local_stack.py`)

### 2) `docs/MODELS.md`

Multi-backend and “full stack” content:

- `docker-compose.models.yml` profiles and ports (including `--profile all`)
- Whisper large download caching
- Qwen3-ASR and VibeVoice-ASR as “remote OpenAI-compatible servers”
- How Xiyu wrappers connect to them
- Speaker strategies (native, external diarizer, fallback diarization)

### 3) `docs/TROUBLESHOOTING.md`

High-signal troubleshooting checklist:

- GPU not detected
- model downloads (proxy, HF_TOKEN, disk space)
- port conflicts
- diarizer access restrictions
- performance tips for 48GB VRAM machines (don’t start every GPU-heavy backend at once)

---

## Success criteria

- A new user can get from a fresh machine to:
  - `docker compose up -d` working on Linux GPU
  - `docker compose -f docker-compose.cpu.yml up -d` working on Mac/Windows
  - `python scripts/local_stack.py start --mode meeting` working on bare metal
- They know how to:
  - switch backends by port + Base URL
  - enable speaker output via external diarizer
  - avoid re-downloading weights (volumes/mounts)
