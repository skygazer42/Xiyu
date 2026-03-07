"""Alignment utilities (best-effort timestamps).

This package provides lightweight, dependency-minimal alignment helpers for
enterprise workflows like:
- search / locate / highlight in long meeting recordings
- post-edit UIs that need approximate token timestamps

We intentionally avoid heavyweight aligners (e.g. WhisperX) as hard deps.
"""

from .word_timestamps import build_word_timestamps

__all__ = ["build_word_timestamps"]

