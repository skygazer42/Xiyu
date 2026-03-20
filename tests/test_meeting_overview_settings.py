def test_meeting_overview_settings_defaults():
    from src.config import settings

    assert hasattr(settings, "meeting_overview_enable")
    assert settings.meeting_overview_enable is True

    assert hasattr(settings, "meeting_overview_auto")
    assert settings.meeting_overview_auto is True

    assert hasattr(settings, "meeting_overview_role")
    assert isinstance(settings.meeting_overview_role, str)
    assert settings.meeting_overview_role

    assert hasattr(settings, "meeting_overview_max_input_chars")
    assert int(settings.meeting_overview_max_input_chars) > 0

    assert hasattr(settings, "meeting_overview_chunk_chars")
    assert int(settings.meeting_overview_chunk_chars) > 0

