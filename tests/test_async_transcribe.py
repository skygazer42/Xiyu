"""测试异步转写 API 和任务管理器"""
import pytest
import time
from unittest.mock import patch, MagicMock


class TestTaskManager:
    """任务管理器测试"""

    def test_submit_and_get_result(self):
        """测试提交和获取结果"""
        from src.core.task_manager import TaskManager, TaskStatus

        manager = TaskManager()
        manager.register_handler("test", lambda payload: {"result": payload["value"] * 2})
        manager.start()

        task_id = manager.submit("test", {"value": 21})
        assert task_id is not None

        # 等待处理
        for _ in range(50):
            result = manager.get_result(task_id, delete=False)
            if result and result.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        result = manager.get_result(task_id)
        assert result.status == TaskStatus.COMPLETED
        assert result.result == {"result": 42}
        manager.stop()

    def test_failed_task(self):
        """测试失败任务"""
        from src.core.task_manager import TaskManager, TaskStatus

        def fail_handler(payload):
            raise ValueError("test error")

        manager = TaskManager()
        manager.register_handler("fail", fail_handler)
        manager.start()

        task_id = manager.submit("fail", {})

        for _ in range(50):
            result = manager.get_result(task_id, delete=False)
            if result and result.status == TaskStatus.FAILED:
                break
            time.sleep(0.1)

        result = manager.get_result(task_id)
        assert result.status == TaskStatus.FAILED
        assert "test error" in result.error
        manager.stop()

    def test_get_nonexistent_task(self):
        """测试获取不存在的任务"""
        from src.core.task_manager import TaskManager

        manager = TaskManager()
        assert manager.get_result("nonexistent") is None

    def test_get_status(self):
        """测试获取任务状态"""
        from src.core.task_manager import TaskManager, TaskStatus

        manager = TaskManager()
        manager.register_handler("slow", lambda p: time.sleep(0.1) or {"ok": True})
        manager.start()

        task_id = manager.submit("slow", {})
        # 刚提交应该是 PENDING 或 PROCESSING
        status = manager.get_status(task_id)
        assert status in (TaskStatus.PENDING, TaskStatus.PROCESSING)

        # 等待完成
        for _ in range(50):
            if manager.get_status(task_id) == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        assert manager.get_status(task_id) == TaskStatus.COMPLETED
        manager.stop()

    def test_delete_on_get(self):
        """测试获取后删除"""
        from src.core.task_manager import TaskManager, TaskStatus

        manager = TaskManager()
        manager.register_handler("quick", lambda p: {"done": True})
        manager.start()

        task_id = manager.submit("quick", {})

        for _ in range(50):
            r = manager.get_result(task_id, delete=False)
            if r and r.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        # 第一次获取并删除
        result = manager.get_result(task_id, delete=True)
        assert result is not None

        # 第二次应该为 None
        assert manager.get_result(task_id) is None
        manager.stop()


class TestAsyncTranscribeHelpers:
    """异步转写辅助函数测试"""

    def test_ms_to_srt_time(self):
        """测试时间格式转换"""
        from src.api.routes.async_transcribe import ms_to_srt_time

        assert ms_to_srt_time(0) == "00:00:00.000"
        assert ms_to_srt_time(1500) == "00:00:01.500"
        assert ms_to_srt_time(61000) == "00:01:01.000"
        assert ms_to_srt_time(3661500) == "01:01:01.500"

    def test_ms_to_srt_time_edge_cases(self):
        """测试时间格式转换边界情况"""
        from src.api.routes.async_transcribe import ms_to_srt_time

        assert ms_to_srt_time(999) == "00:00:00.999"
        assert ms_to_srt_time(3600000) == "01:00:00.000"


def test_handle_url_transcribe_returns_engine_schema():
    """URL 异步转写任务应返回与 HTTP /transcribe 一致的 schema。

    重点：sentences/speaker_turns 的时间戳必须保持为毫秒整数，且保留 speaker_id，
    否则前端时间轴与说话人统计无法复用。
    """
    from unittest.mock import MagicMock, patch

    import src.api.routes.async_transcribe as async_mod

    fake_engine_result = {
        "code": 0,
        "text": "你好",
        "text_accu": "你好",
        "sentences": [
            {"text": "你好", "start": 0, "end": 500, "speaker": "说话人1", "speaker_id": 0},
        ],
        "speaker_turns": [
            {
                "speaker": "说话人1",
                "speaker_id": 0,
                "start": 0,
                "end": 500,
                "text": "你好",
                "sentence_count": 1,
            }
        ],
        "transcript": "[00:00 - 00:00] 说话人1: 你好",
        "raw_text": "你好",
    }

    # Mock HTTP download and ffmpeg conversion; engine is mocked so audio bytes don't matter.
    mock_resp = MagicMock()
    mock_resp.content = b"RIFF....WAVEfmt "  # dummy bytes
    mock_resp.raise_for_status.return_value = None

    mock_httpx_client = MagicMock()
    mock_httpx_client.__enter__.return_value = mock_httpx_client
    mock_httpx_client.__exit__.return_value = None
    mock_httpx_client.get.return_value = mock_resp

    def _fake_convert(_in: str, out: str) -> bool:
        # Ensure the "converted" file exists for reading.
        with open(out, "wb") as f:
            f.write(b"fake_wav_bytes")
        return True

    with (
        patch.object(async_mod, "httpx") as mock_httpx,
        # URL handler uses the newer in-memory ffmpeg conversion helper.
        patch.object(async_mod, "_convert_path_to_pcm16le_bytes", return_value=b"\x00" * (16000 * 2)),
        patch.object(async_mod, "convert_audio_to_pcm", side_effect=_fake_convert),
        patch.object(async_mod.transcription_engine, "transcribe_long_audio", return_value=fake_engine_result),
    ):
        mock_httpx.Client.return_value = mock_httpx_client

        out = async_mod._handle_url_transcribe(
            {"url": "https://example.com/audio.wav", "with_speaker": True, "apply_hotword": True}
        )

    assert out["code"] == 0
    assert isinstance(out["sentences"][0]["start"], int)
    assert isinstance(out["sentences"][0]["end"], int)
    assert out["sentences"][0]["speaker_id"] == 0
    assert isinstance(out["speaker_turns"][0]["start"], int)
    assert isinstance(out["speaker_turns"][0]["end"], int)


def test_handle_url_transcribe_includes_overview_when_enabled(monkeypatch):
    """异步 URL 转写任务完成后应自动生成会议概览（当启用开关时）。"""
    from unittest.mock import MagicMock, patch

    import src.api.routes.async_transcribe as async_mod

    monkeypatch.setattr(async_mod.settings, "llm_enable", True, raising=False)
    monkeypatch.setattr(async_mod.settings, "meeting_overview_enable", True, raising=False)
    monkeypatch.setattr(async_mod.settings, "meeting_overview_auto", True, raising=False)
    monkeypatch.setattr(async_mod.settings, "meeting_overview_role", "gov_overview", raising=False)

    fake_engine_result = {
        "code": 0,
        "text": "你好",
        "sentences": [{"text": "你好", "start": 0, "end": 500}],
        "speaker_turns": None,
        "raw_text": "你好",
    }

    mock_resp = MagicMock()
    mock_resp.content = b"RIFF....WAVEfmt "
    mock_resp.raise_for_status.return_value = None

    mock_httpx_client = MagicMock()
    mock_httpx_client.__enter__.return_value = mock_httpx_client
    mock_httpx_client.__exit__.return_value = None
    mock_httpx_client.get.return_value = mock_resp

    with (
        patch.object(async_mod, "httpx") as mock_httpx,
        patch.object(async_mod, "_convert_path_to_pcm16le_bytes", return_value=b"\x00" * (16000 * 2)),
        patch.object(async_mod.transcription_engine, "transcribe_long_audio", return_value=fake_engine_result),
        patch("src.core.meeting_overview.generate_meeting_overview_sync", return_value="OVERVIEW"),
    ):
        mock_httpx.Client.return_value = mock_httpx_client
        out = async_mod._handle_url_transcribe({"url": "https://example.com/audio.wav"})

    assert out.get("overview") == "OVERVIEW"
