def test_transcribe_response_accepts_overview_fields():
    from src.api.schemas import TranscribeResponse

    obj = TranscribeResponse(
        code=0,
        text="你好",
        sentences=[{"text": "你好", "start": 0, "end": 1000}],
        overview="会议围绕有关工作进行了交流。",
        overview_task_id="task_123",
    )

    assert obj.overview and "会议" in obj.overview
    assert obj.overview_task_id == "task_123"

