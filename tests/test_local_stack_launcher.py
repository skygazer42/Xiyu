import socket
from pathlib import Path


def test_is_port_open_false_for_unused_port():
    from scripts import local_stack

    # Bind to a random free port, close it, then validate it's closed. This avoids
    # assuming a hardcoded port is unused.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    host, port = server.getsockname()
    server.close()

    assert local_stack.is_port_open(host, port) is False


def test_is_port_open_true_for_bound_socket():
    from scripts import local_stack

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    try:
        host, port = server.getsockname()
        assert local_stack.is_port_open(host, port) is True
    finally:
        server.close()


def test_ensure_run_dir_creates_local_stack_dir(tmp_path: Path):
    from scripts import local_stack

    run_dir = local_stack.ensure_run_dir(tmp_path)
    assert run_dir.exists()
    assert run_dir.is_dir()
    assert run_dir.name == "local_stack"


def test_build_meeting_specs_has_two_services(tmp_path: Path, monkeypatch):
    from scripts import local_stack

    monkeypatch.delenv("XIYU_PYTHON", raising=False)
    monkeypatch.delenv("DIARIZER_PYTHON", raising=False)
    monkeypatch.setenv("PORT_PYTORCH", "18101")
    monkeypatch.setenv("DIARIZER_PORT", "18300")

    specs = local_stack.build_meeting_specs(tmp_path, host="127.0.0.1")
    assert [s.name for s in specs] == ["diarizer", "pytorch"]


def test_start_services_writes_pidfiles_and_logs(tmp_path: Path, monkeypatch):
    from scripts import local_stack

    monkeypatch.setenv("PORT_PYTORCH", "18101")
    monkeypatch.setenv("DIARIZER_PORT", "18300")

    run_dir = local_stack.ensure_run_dir(tmp_path)
    specs = local_stack.build_meeting_specs(tmp_path, host="127.0.0.1")

    monkeypatch.setattr(local_stack, "is_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(local_stack, "wait_for_port", lambda *args, **kwargs: True)

    popen_calls: list[dict] = []

    class _Proc:
        def __init__(self, pid: int):
            self.pid = pid

    def fake_popen(args, **kwargs):
        popen_calls.append({"args": args, "kwargs": kwargs})
        return _Proc(pid=12345 + len(popen_calls))

    monkeypatch.setattr(local_stack.subprocess, "Popen", fake_popen)

    local_stack.start_services(specs, repo_root=tmp_path, run_dir=run_dir)

    assert (run_dir / "diarizer.pid").exists()
    assert (run_dir / "pytorch.pid").exists()
    assert (run_dir / "diarizer.log").exists()
    assert (run_dir / "pytorch.log").exists()
    assert len(popen_calls) == 2


def test_stop_services_sends_signals_and_removes_pidfiles(tmp_path: Path, monkeypatch):
    from scripts import local_stack

    monkeypatch.setenv("PORT_PYTORCH", "18101")
    monkeypatch.setenv("DIARIZER_PORT", "18300")

    run_dir = local_stack.ensure_run_dir(tmp_path)
    specs = local_stack.build_meeting_specs(tmp_path, host="127.0.0.1")

    (run_dir / "diarizer.pid").write_text("111\n", encoding="utf-8")
    (run_dir / "pytorch.pid").write_text("222\n", encoding="utf-8")

    running_calls: dict[int, int] = {}

    def fake_pid_is_running(pid: int) -> bool:
        # First few calls per pid: running, then it exits.
        running_calls[pid] = running_calls.get(pid, 0) + 1
        return running_calls[pid] < 3

    kill_calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    monkeypatch.setattr(local_stack, "pid_is_running", fake_pid_is_running)
    monkeypatch.setattr(local_stack.os, "kill", fake_kill)

    local_stack.stop_services(specs, run_dir=run_dir, timeout_s=0.01)

    assert not (run_dir / "diarizer.pid").exists()
    assert not (run_dir / "pytorch.pid").exists()
    assert any(pid == 111 for pid, _sig in kill_calls)
    assert any(pid == 222 for pid, _sig in kill_calls)
