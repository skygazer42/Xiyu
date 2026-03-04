# Local Meeting Stack Launcher (No Docker) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a one-command **local (non-Docker) launcher** that starts a meeting-ready stack: **Xiyu PyTorch backend + external diarizer (pyannote)**, with simple `start/stop/status/logs` lifecycle management.

**Architecture:** Implement `scripts/local_stack.py` (no new deps) that starts two uvicorn-based modules (`python -m src.main`, `python -m src.diarizer_service.app`) in the background via `subprocess.Popen`, storing pidfiles + logs under `./.run/local_stack/`. In `meeting` mode, the launcher always enables `SPEAKER_EXTERNAL_DIARIZER_ENABLE=true` for the main service.

**Tech Stack:** Python stdlib (`argparse`, `subprocess`, `pathlib`, `socket`, `signal`), FastAPI/uvicorn (already in repo), pytest for unit tests.

---

### Task 01: Add failing tests for the local launcher (RED)

**Files:**
- Create: `tests/test_local_stack_launcher.py`

**Step 1: Write the failing test(s)**

Create `tests/test_local_stack_launcher.py` that imports the launcher module and tests small pure helpers:

- `is_port_open(host, port)` returns False when no listener exists
- `ensure_run_dir(root)` creates `./.run/local_stack/`

Also add a smoke test that `build_meeting_services_config()` (or equivalent) returns both services.

Example skeleton:

```python
from scripts import local_stack


def test_is_port_open_false_for_unused_port():
    assert local_stack.is_port_open("127.0.0.1", 65534) is False
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_local_stack_launcher.py -v`

Expected: FAIL because `scripts/local_stack.py` does not exist yet.

**Step 3: Commit**

```bash
git add tests/test_local_stack_launcher.py
git commit -m "test: add local stack launcher skeleton (red)"
```

---

### Task 02: Implement minimal `scripts/local_stack.py` to satisfy imports (GREEN)

**Files:**
- Create: `scripts/local_stack.py`

**Step 1: Add minimal implementation**

Implement:
- `RUN_DIR = Path(".run/local_stack")`
- `def ensure_run_dir(root: Path) -> Path`
- `def is_port_open(host: str, port: int, timeout_s: float = 0.25) -> bool`
- a minimal `main()` with argparse accepting `start|stop|status|logs` and `--mode meeting`

No subprocess management yet beyond stubs.

**Step 2: Run tests**

Run: `python -m pytest -q tests/test_local_stack_launcher.py -v`

Expected: PASS (or fewer failures, if tests include more than helpers).

**Step 3: Commit**

```bash
git add scripts/local_stack.py
git commit -m "feat: add local stack launcher helpers"
```

---

### Task 03: Add subprocess-based start/stop/status with pidfiles + logs

**Files:**
- Modify: `scripts/local_stack.py`
- Modify: `tests/test_local_stack_launcher.py`

**Step 1: Expand tests (RED)**

Mock `subprocess.Popen` so tests do not spawn uvicorn:
- `start` writes pidfiles for both services
- `start` refuses to run when a port is already open
- `stop` calls `os.kill(pid, SIGTERM)` then cleans pidfiles

**Step 2: Implement process management (GREEN)**

Add:
- `ServiceSpec` (name, host, port, python, module, env)
- `start_services(specs)` using `subprocess.Popen(..., stdout=log, stderr=log, start_new_session=True)`
- `stop_services(specs)` reading pidfiles and signaling
- `status_services(specs)` checking pidfile + process exists + port open
- `logs` reading log files with a tail option

**Step 3: Run tests**

Run: `python -m pytest -q tests/test_local_stack_launcher.py -v`

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/local_stack.py tests/test_local_stack_launcher.py
git commit -m "feat: local meeting stack start/stop/status/logs"
```

---

### Task 04: Add `.run/` gitignore + README docs

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

**Step 1: Update `.gitignore`**

Ignore `/.run/` (and optionally `/.run/**`) so pid/log files never show up.

**Step 2: Update README**

In the “不用 Docker：直接 Python 启动” section, add:

```bash
python scripts/local_stack.py start --mode meeting
python scripts/local_stack.py status
python scripts/local_stack.py logs --tail 200
python scripts/local_stack.py stop
```

Mention env overrides:
- `XIYU_PYTHON=...`
- `DIARIZER_PYTHON=...`
- `PORT_PYTORCH=...`
- `DIARIZER_PORT=...`

**Step 3: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: add local meeting stack launcher usage"
```

---

### Task 05: Final verification

**Step 1: Run targeted unit tests**

Run: `python -m pytest -q tests/test_local_stack_launcher.py`

Expected: PASS.

**Step 2: Optional: run a small existing suite (fast)**

Run: `python -m pytest -q tests/test_api_hotwords.py`

Expected: PASS.

**Step 3: Merge to main + push**

```bash
# from repo root worktree or main
# merge the feature branch then push
```
