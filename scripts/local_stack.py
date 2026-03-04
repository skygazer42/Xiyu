from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Mapping


RUN_DIR_REL = Path(".run") / "local_stack"
DEFAULT_HOST = "127.0.0.1"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    host: str
    port: int
    python: str
    module: str
    extra_env: Mapping[str, str]


def ensure_run_dir(root: Path) -> Path:
    run_dir = root / RUN_DIR_REL
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def is_port_open(host: str, port: int, timeout_s: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        try:
            return sock.connect_ex((host, int(port))) == 0
        except OSError:
            return False


def wait_for_port(host: str, port: int, timeout_s: float = 15.0, check_interval_s: float = 0.1) -> bool:
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        if is_port_open(host, port):
            return True
        time.sleep(float(check_interval_s))
    return is_port_open(host, port)


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return int(default)
    return int(str(v).strip())


def _env_str(key: str, default: str) -> str:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return str(v)


def _repo_root() -> Path:
    # scripts/local_stack.py lives under <repo>/scripts/.
    return Path(__file__).resolve().parents[1]


def build_meeting_specs(root: Path, host: str) -> list[ServiceSpec]:
    pytorch_port = _env_int("PORT_PYTORCH", 8101)
    diarizer_port = _env_int("DIARIZER_PORT", 8300)

    xiyu_python = _env_str("XIYU_PYTHON", sys.executable)
    diarizer_python = _env_str("DIARIZER_PYTHON", sys.executable)

    diarizer = ServiceSpec(
        name="diarizer",
        host=host,
        port=diarizer_port,
        python=diarizer_python,
        module="src.diarizer_service.app",
        extra_env={
            "DIARIZER_PORT": str(diarizer_port),
            "DIARIZER_WARMUP_ON_STARTUP": os.getenv("DIARIZER_WARMUP_ON_STARTUP", "true"),
            **({"HF_TOKEN": os.getenv("HF_TOKEN", "")} if os.getenv("HF_TOKEN") else {}),
        },
    )

    pytorch = ServiceSpec(
        name="pytorch",
        host=host,
        port=pytorch_port,
        python=xiyu_python,
        module="src.main",
        extra_env={
            "ASR_BACKEND": "pytorch",
            "PORT": str(pytorch_port),
            "SPEAKER_EXTERNAL_DIARIZER_ENABLE": "true",
            "SPEAKER_EXTERNAL_DIARIZER_BASE_URL": f"http://{host}:{diarizer_port}",
        },
    )

    return [diarizer, pytorch]


def _pid_path(run_dir: Path, name: str) -> Path:
    return run_dir / f"{name}.pid"


def _log_path(run_dir: Path, name: str) -> Path:
    return run_dir / f"{name}.log"


def _read_pid(pid_path: Path) -> int | None:
    try:
        s = pid_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _open_log_file(path: Path) -> IO[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8", buffering=1)


def start_services(specs: list[ServiceSpec], repo_root: Path, run_dir: Path) -> None:
    for spec in specs:
        if is_port_open(spec.host, spec.port):
            raise RuntimeError(f"Port already in use: {spec.host}:{spec.port} ({spec.name})")

    started: list[ServiceSpec] = []
    try:
        for spec in specs:
            log_file = _open_log_file(_log_path(run_dir, spec.name))
            try:
                env = os.environ.copy()
                env.update({k: str(v) for k, v in spec.extra_env.items()})

                proc = subprocess.Popen(
                    [
                        spec.python,
                        "-m",
                        spec.module,
                        "--host",
                        str(spec.host),
                        "--port",
                        str(spec.port),
                    ],
                    cwd=str(repo_root),
                    env=env,
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True,
                )
            finally:
                # The child has inherited the file descriptor; close ours.
                log_file.close()

            _pid_path(run_dir, spec.name).write_text(f"{proc.pid}\n", encoding="utf-8")
            started.append(spec)

            if not wait_for_port(spec.host, spec.port, timeout_s=15.0):
                raise RuntimeError(f"Service did not open port in time: {spec.name} ({spec.host}:{spec.port})")
    except Exception:
        # Best-effort cleanup to avoid leaving half-started services around.
        stop_services(started, run_dir=run_dir)
        raise


def stop_services(specs: list[ServiceSpec], run_dir: Path, timeout_s: float = 5.0) -> None:
    for spec in specs:
        pid_path = _pid_path(run_dir, spec.name)
        pid = _read_pid(pid_path)
        if pid is None:
            continue

        if not pid_is_running(pid):
            pid_path.unlink(missing_ok=True)
            continue

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid_path.unlink(missing_ok=True)
            continue

        deadline = time.time() + float(timeout_s)
        while time.time() < deadline and pid_is_running(pid):
            time.sleep(0.05)

        if pid_is_running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        pid_path.unlink(missing_ok=True)


def status_services(specs: list[ServiceSpec], run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for spec in specs:
        pid = _read_pid(_pid_path(run_dir, spec.name))
        running = bool(pid is not None and pid_is_running(pid))
        port_open = is_port_open(spec.host, spec.port)
        rows.append(
            {
                "name": spec.name,
                "pid": pid,
                "running": running,
                "port_open": port_open,
                "url": f"http://{spec.host}:{spec.port}",
                "log": str(_log_path(run_dir, spec.name)),
            }
        )
    return rows


def _tail_lines(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    if n <= 0:
        return ""
    return "\n".join(lines[-n:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local multi-process launcher for Xiyu")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start local services")
    start.add_argument("--mode", default="meeting", choices=["meeting"])
    start.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host (default: {DEFAULT_HOST})")

    stop = sub.add_parser("stop", help="Stop local services")
    stop.add_argument("--host", default=DEFAULT_HOST)

    status = sub.add_parser("status", help="Show local services status")
    status.add_argument("--host", default=DEFAULT_HOST)

    logs = sub.add_parser("logs", help="Show local services logs")
    logs.add_argument("--host", default=DEFAULT_HOST)
    logs.add_argument("--tail", type=int, default=200)

    args = parser.parse_args(argv)

    repo_root = _repo_root()
    run_dir = ensure_run_dir(repo_root)
    specs = build_meeting_specs(repo_root, host=str(getattr(args, "host", DEFAULT_HOST)))

    if args.cmd == "start":
        start_services(specs, repo_root=repo_root, run_dir=run_dir)
        print("Local meeting stack started:")
        for row in status_services(specs, run_dir=run_dir):
            print(f"- {row['name']}: {row['url']} (log: {row['log']})")
        print("")
        print(f"PyTorch API: http://{args.host}:{_env_int('PORT_PYTORCH', 8101)}/docs")
        print(f"Diarizer API: http://{args.host}:{_env_int('DIARIZER_PORT', 8300)}/docs")
        return 0

    if args.cmd == "stop":
        stop_services(specs, run_dir=run_dir)
        print("Local meeting stack stopped.")
        return 0

    if args.cmd == "status":
        for row in status_services(specs, run_dir=run_dir):
            running = "running" if row["running"] else "stopped"
            port = "open" if row["port_open"] else "closed"
            pid = row["pid"] if row["pid"] is not None else "-"
            print(f"- {row['name']}: {running}, port={port}, pid={pid}, url={row['url']}")
        return 0

    if args.cmd == "logs":
        for spec in specs:
            path = _log_path(run_dir, spec.name)
            print(f"== {spec.name}: {path} ==")
            out = _tail_lines(path, int(args.tail))
            if out:
                print(out)
            else:
                print("(no logs yet)")
            print("")
        return 0

    raise AssertionError(f"Unhandled cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
