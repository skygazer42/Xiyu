# Deployment Docs (From Zero → Full Stack) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add complete deployment documentation that takes users from a fresh machine to a working Xiyu stack, covering Linux GPU + Docker Compose, macOS/Windows CPU + Docker Desktop, and local Python (no Docker) including the meeting stack launcher.

**Architecture:** Write three focused docs under `docs/`:
- `docs/DEPLOYMENT.md` (main “from 0 → running” guide)
- `docs/MODELS.md` (multi-backend profiles, remote ASR servers, caching, speaker strategies)
- `docs/TROUBLESHOOTING.md` (common failures + fixes)

Keep `README.md` as a quick start and add links to these docs.

**Tech Stack:** Markdown only. No runtime code changes required (optional small script usage doc updates).

---

### Task 01: Add `docs/DEPLOYMENT.md` skeleton (Linux GPU + Mac/Windows CPU + local Python)

**Files:**
- Create: `docs/DEPLOYMENT.md`

**Step 1: Write the doc skeleton**

Include sections:
- 目标/适用范围
- 快速验证（启动后访问 `/health`、`/docs`、UI）
- Linux（GPU 推荐）从 0：驱动/容器工具/验证 GPU
- macOS/Windows（Docker Desktop，CPU）从 0：差异说明
- Docker Compose 启动（GPU/CPU）
- 不用 Docker：单进程启动
- 不用 Docker：一键会议栈启动（`scripts/local_stack.py`）

**Step 2: Quick check**

Run: `rg -n "DEPLOYMENT" README.md docs -S`

Expected: new file exists and headings render.

**Step 3: Commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: add deployment guide"
```

---

### Task 02: Add `docs/MODELS.md` (profiles/ports/remote servers/speaker strategy)

**Files:**
- Create: `docs/MODELS.md`

**Step 1: Write the doc**

Include:
- `docker-compose.models.yml` philosophy (one backend per port; frontend selects Base URL)
- Profile examples (`pytorch/onnx/sensevoice/gguf/whisper/qwen3/diarizer/all`)
- Port table (match `.env.example`)
- Whisper weights caching (`WHISPER_DOWNLOAD_ROOT` under `./data/models/whisper`)
- Qwen3-ASR server (`qwen3-asr`) vs wrapper (`xiyu-qwen3`)
- VibeVoice/Router requirements (`VIBEVOICE_REPO_PATH`)
- Speaker options:
  - backend-native
  - external diarizer (pyannote)
  - fallback diarization

**Step 2: Commit**

```bash
git add docs/MODELS.md
git commit -m "docs: add models and profiles guide"
```

---

### Task 03: Add `docs/TROUBLESHOOTING.md` (GPU/download/ports/diarizer/perf)

**Files:**
- Create: `docs/TROUBLESHOOTING.md`

**Step 1: Write the doc**

Must include:
- GPU not detected checks (`nvidia-smi`, `docker run --gpus all ... nvidia-smi`)
- Model download issues (proxy envs, HF_TOKEN, volumes, disk)
- Port conflicts (how to change `.env`, how to check listeners)
- diarizer access issues (pyannote gated models)
- VRAM guidance (don’t start all GPU-heavy backends at once)

**Step 2: Commit**

```bash
git add docs/TROUBLESHOOTING.md
git commit -m "docs: add troubleshooting guide"
```

---

### Task 04: Link docs from `README.md` and keep README concise

**Files:**
- Modify: `README.md`

**Step 1: Add a short “Docs” section**

Add links to:
- `docs/DEPLOYMENT.md`
- `docs/MODELS.md`
- `docs/TROUBLESHOOTING.md`
- (Optional) `docs/API.md`

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: link full deployment docs from README"
```

---

### Task 05: Optional polish

**Files:**
- Modify (optional): `scripts/start.sh`

**Step 1: Update the usage help**

If needed, update the `models` usage string to include `all|whisper|diarizer`.

**Step 2: Commit**

```bash
git add scripts/start.sh
git commit -m "chore: update start.sh models help"
```

---

### Task 06: Final verification + merge

**Step 1: Run doc sanity checks**

- Ensure new files exist: `ls -la docs/DEPLOYMENT.md docs/MODELS.md docs/TROUBLESHOOTING.md`
- Grep for broken relative links: `rg -n "\\]\\((?!https?://)" docs README.md`

**Step 2: Merge to main + push**

Fast-forward merge the docs branch into `main` and push.
