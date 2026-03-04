# Local Meeting Stack Launcher (No Docker) — Design

**Date:** 2026-02-20  
**Scope:** Xiyu (`/Users/luke/code/xiyu`)  
**Primary goal:** provide a **one-command local (non-Docker) launcher** for the meeting transcription scenario:

- **PyTorch backend** (`ASR_BACKEND=pytorch`) for high accuracy + speaker utilities
- **External diarizer service** (pyannote) for stable speaker turns (`说话人1/2/3…`)

This feature is intentionally scoped to the “meeting mode” default path the user selected: **mode=meeting**.

---

## Current state

Xiyu already supports “no Docker” startup, but requires manual multi-process management:

- Main service: `ASR_BACKEND=pytorch PORT=8101 python -m src.main`
- Diarizer: `HF_TOKEN=... DIARIZER_PORT=8300 python -m src.diarizer_service.app`
- Then wire them together via:
  - `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`
  - `SPEAKER_EXTERNAL_DIARIZER_BASE_URL=http://localhost:8300`

This works, but is cumbersome for day-to-day usage.

---

## Problems to solve

1) **Too many commands**
   - Users must remember ports, env vars, and two separate commands.

2) **No lifecycle management**
   - Hard to stop the correct processes.
   - Logs are scattered in terminal output.

3) **Hard to validate “am I ready?”**
   - No quick status output (port available? process alive? diarizer reachable?).

---

## Goals

1) Provide a **single launcher command** with start/stop/status/logs:
   - `python scripts/local_stack.py start --mode meeting`
   - `python scripts/local_stack.py stop`
   - `python scripts/local_stack.py status`
   - `python scripts/local_stack.py logs`

2) **Meeting mode defaults**:
   - Always start both services (pytorch + diarizer).
   - Always enable external diarizer by default for the main service.

3) Keep it **safe and lightweight**:
   - Do not auto-install pip deps.
   - Do not modify the user’s environment.
   - Avoid new runtime dependencies (no `python-dotenv` etc.).

4) Be practical on macOS/Linux:
   - Port checks + clean shutdown (SIGTERM then SIGKILL).
   - PID files and log files in a single predictable directory.

---

## Non-goals (v1)

- Starting remote model servers (e.g. `qwen3-asr`, `vibevoice-asr`) without Docker.
  - Xiyu can run a *wrapper* backend locally, but the remote ASR server setup is external to this repo.
- Acting as a full supervisor (systemd/launchd replacement).
- Windows-specific process management edge cases (script should still be best-effort portable).
- Building the frontend (`npm run build`) or managing Node dependencies.

---

## Proposed approach

Add a Python launcher script: `scripts/local_stack.py`

### CLI surface

Commands:
- `start --mode meeting`
- `stop`
- `status`
- `logs [--tail N]`

Mode:
- `meeting` (v1 only; future modes can be added later)

### Services started in `meeting` mode

1) **Diarizer service**
   - Command: `<DIARIZER_PYTHON> -m src.diarizer_service.app --host <host> --port <port>`
   - Env (passthrough + defaults):
     - `DIARIZER_PORT=8300` (default)
     - `DIARIZER_WARMUP_ON_STARTUP=true` (default)
     - `HF_TOKEN` passed through if set (optional)

2) **Xiyu main service (PyTorch)**
   - Command: `<XIYU_PYTHON> -m src.main --host <host> --port <port>`
   - Env (passthrough + defaults):
     - `ASR_BACKEND=pytorch`
     - `PORT=8101` (default)
     - `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true`
     - `SPEAKER_EXTERNAL_DIARIZER_BASE_URL=http://localhost:8300`

### Output files

Create a runtime directory under repo root:

- PID files: `./.run/local_stack/*.pid`
- Logs: `./.run/local_stack/*.log`

The launcher:
- writes pidfiles on successful start
- refuses to start if the target port is already in use
- on stop: reads pidfiles, sends SIGTERM, waits briefly, then SIGKILL

Add `/.run/` to `.gitignore` to avoid accidental commits.

### Error handling principles

The launcher should fail fast with actionable messages:
- If python executable is missing → print the expected path/override env var.
- If required import fails at runtime → tell user which `pip install -r ...` to run.
- If port is in use → show which port and suggest setting env var to override.

### Dependency / venv strategy

The launcher supports using separate Python interpreters:

- `XIYU_PYTHON=/path/to/python`
- `DIARIZER_PYTHON=/path/to/python`

This enables the recommended setup:
- `.venv` for main service
- `.venv-diarizer` for diarizer

---

## Success criteria

From a fresh shell:

1) `python scripts/local_stack.py start --mode meeting` starts both services in background.
2) `python scripts/local_stack.py status` reports both processes alive and ports reachable.
3) `python scripts/local_stack.py logs --tail 200` shows logs for both services.
4) `python scripts/local_stack.py stop` stops both services cleanly and removes pidfiles.

---

## Testing strategy

Add lightweight unit tests that do not actually spawn uvicorn:
- Patch `subprocess.Popen` with a stub object.
- Verify pidfile/logfile paths are created.
- Verify “port already in use” prevents spawn.
- Verify stop sends signals to the pid.

The core logic should be written in small functions to keep tests simple.
