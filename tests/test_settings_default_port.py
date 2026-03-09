def test_settings_default_public_port_is_18200(monkeypatch):
    from src.config import Settings

    monkeypatch.delenv("PORT", raising=False)

    s = Settings()

    assert s.port == 18200
