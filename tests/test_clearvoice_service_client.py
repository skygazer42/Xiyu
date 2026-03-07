from __future__ import annotations

from unittest.mock import patch

import httpx
import numpy as np

from src.config import settings
from src.core.audio.clearvoice_service_client import clearvoice_service_enhance


def _fake_clearvoice_echo_post_factory(calls: dict):
    def _fake_post(self, url, files=None, **kwargs):
        calls["n"] = int(calls.get("n", 0)) + 1
        assert str(url).endswith("/api/v1/enhance")
        assert isinstance(files, dict)
        wav = files["file"][1]
        req = httpx.Request("POST", str(url))
        return httpx.Response(200, request=req, content=wav)

    return _fake_post


def test_clearvoice_service_enhance_single_request_when_under_max_duration(monkeypatch):
    # Force "single request" path.
    monkeypatch.setattr(settings, "clearvoice_service_max_duration_s", 10.0, raising=False)
    monkeypatch.setattr(settings, "clearvoice_chunk_duration_s", 30.0, raising=False)
    monkeypatch.setattr(settings, "clearvoice_overlap_duration_s", 0.5, raising=False)

    rng = np.random.RandomState(0)
    x = rng.uniform(-0.5, 0.5, size=(16000 * 2,)).astype(np.float32)

    calls = {"n": 0}
    with patch.object(httpx.Client, "post", new=_fake_clearvoice_echo_post_factory(calls)):
        y = clearvoice_service_enhance(x, sample_rate=16000, base_url="http://clearvoice", timeout_s=5.0)

    assert calls["n"] == 1
    assert y.shape == x.shape
    assert np.allclose(y, x, atol=2.0 / 32768.0)


def test_clearvoice_service_enhance_chunks_when_over_max_duration(monkeypatch):
    # Force chunking: max_duration smaller than input duration.
    monkeypatch.setattr(settings, "clearvoice_service_max_duration_s", 0.5, raising=False)
    monkeypatch.setattr(settings, "clearvoice_chunk_duration_s", 0.6, raising=False)
    monkeypatch.setattr(settings, "clearvoice_overlap_duration_s", 0.1, raising=False)

    rng = np.random.RandomState(1)
    x = rng.uniform(-0.5, 0.5, size=(16000 * 2,)).astype(np.float32)

    calls = {"n": 0}
    with patch.object(httpx.Client, "post", new=_fake_clearvoice_echo_post_factory(calls)):
        y = clearvoice_service_enhance(x, sample_rate=16000, base_url="http://clearvoice", timeout_s=5.0)

    assert calls["n"] >= 2
    assert y.shape == x.shape
    assert np.allclose(y, x, atol=2.0 / 32768.0)

