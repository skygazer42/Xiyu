import asyncio


def test_build_overview_source_text_prefers_speaker_turns():
    from src.core.meeting_overview import build_overview_source_text

    src = build_overview_source_text(
        {
            "speaker_turns": [
                {"speaker": "说话人甲", "speaker_id": 0, "start": 0, "end": 1000, "text": "大家好", "sentence_count": 1},
                {"speaker": "说话人乙", "speaker_id": 1, "start": 1000, "end": 2000, "text": "收到", "sentence_count": 1},
            ],
            "sentences": [{"text": "不应使用", "start": 0, "end": 1000}],
            "text": "不应使用",
        }
    )

    assert "说话人甲" in src
    assert "大家好" in src
    assert "说话人乙" in src
    assert "收到" in src
    # Should not leak timestamps into the LLM input (noise).
    assert "1000" not in src


def test_build_overview_source_text_falls_back_to_sentences_then_text():
    from src.core.meeting_overview import build_overview_source_text

    src = build_overview_source_text(
        {
            "speaker_turns": None,
            "sentences": [{"text": "第一句", "start": 0, "end": 1000}, {"text": "第二句", "start": 1000, "end": 2000}],
            "text": "忽略这段",
        }
    )
    assert "第一句" in src and "第二句" in src

    src2 = build_overview_source_text({"text": "只有全文", "sentences": []})
    assert src2.strip() == "只有全文"


def test_chunk_text_respects_max_chars():
    from src.core.meeting_overview import chunk_text

    text = "\n".join([f"段落{i} " + ("A" * 30) for i in range(10)])
    chunks = chunk_text(text, chunk_chars=80)
    assert len(chunks) >= 2
    assert all(isinstance(c, str) and c.strip() for c in chunks)
    assert all(len(c) <= 120 for c in chunks), "chunks should be bounded"


def test_generate_overview_two_phase_calls_llm_multiple_times_for_long_input():
    from src.core.meeting_overview import generate_meeting_overview

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, stream=False, use_cache=True, cancel_token=None):
            self.calls += 1
            # Distinguish note-extraction vs final synthesis by user prompt marker.
            user = ""
            try:
                user = messages[-1].content
            except Exception:
                user = ""
            if "提取" in user and "要点" in user:
                yield "NOTE"
            else:
                yield "FINAL_OVERVIEW"

    fake = FakeLLM()
    long_text = "会议内容。" * 10000
    out = asyncio.run(
        generate_meeting_overview(
            long_text,
            llm_client=fake,
            role="gov_overview",
            max_input_chars=2000,
            chunk_chars=800,
        )
    )

    assert "FINAL_OVERVIEW" in out
    assert fake.calls >= 2, "long input should trigger multi-call summarization"

