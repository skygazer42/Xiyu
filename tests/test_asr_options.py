import pytest

from src.api.asr_options import parse_asr_options


def test_asr_options_chunking_strategy_normalizes():
    opts = parse_asr_options('{"chunking":{"strategy":" TIME "}}')
    assert opts is not None
    assert opts["chunking"]["strategy"] == "time"


def test_asr_options_chunking_strategy_rejects_unknown():
    with pytest.raises(ValueError, match="chunking\\.strategy"):
        parse_asr_options('{"chunking":{"strategy":"vad"}}')


def test_asr_options_preprocess_highpass_and_limiter_keys_allowed():
    opts = parse_asr_options(
        '{"preprocess":{"highpass_enable":true,"highpass_cutoff_hz":200,"soft_limit_enable":true,"soft_limit_target":0.98,"soft_limit_knee":3.0}}'
    )
    assert opts is not None
    assert opts["preprocess"]["highpass_enable"] is True
    assert opts["preprocess"]["highpass_cutoff_hz"] == 200
    assert opts["preprocess"]["soft_limit_enable"] is True


def test_asr_options_preprocess_highpass_cutoff_must_be_positive():
    with pytest.raises(ValueError, match="highpass_cutoff_hz"):
        parse_asr_options('{"preprocess":{"highpass_enable":true,"highpass_cutoff_hz":-1}}')


def test_asr_options_chunking_boundary_reconcile_keys_allowed():
    opts = parse_asr_options('{"chunking":{"boundary_reconcile_enable":true,"boundary_reconcile_window_s":0.5}}')
    assert opts is not None
    assert opts["chunking"]["boundary_reconcile_enable"] is True
    assert opts["chunking"]["boundary_reconcile_window_s"] == 0.5


def test_asr_options_chunking_infer_batch_size_key_allowed():
    opts = parse_asr_options('{"chunking":{"infer_batch_size":4}}')
    assert opts is not None
    assert opts["chunking"]["infer_batch_size"] == 4


def test_asr_options_chunking_boundary_reconcile_window_must_be_non_negative():
    with pytest.raises(ValueError, match="boundary_reconcile_window_s"):
        parse_asr_options('{"chunking":{"boundary_reconcile_enable":true,"boundary_reconcile_window_s":-0.1}}')


def test_asr_options_postprocess_spoken_punc_and_acronym_keys_allowed():
    opts = parse_asr_options('{"postprocess":{"spoken_punc_enable":true,"acronym_merge_enable":true}}')
    assert opts is not None
    assert opts["postprocess"]["spoken_punc_enable"] is True
    assert opts["postprocess"]["acronym_merge_enable"] is True


def test_asr_options_speaker_keys_allowed():
    opts = parse_asr_options('{"speaker":{"label_style":"numeric","turn_merge_enable":true,"turn_merge_gap_ms":800}}')
    assert opts is not None
    assert opts["speaker"]["label_style"] == "numeric"
    assert opts["speaker"]["turn_merge_enable"] is True
    assert opts["speaker"]["turn_merge_gap_ms"] == 800


def test_asr_options_speaker_rejects_unknown_keys():
    with pytest.raises(ValueError, match="asr_options\\.speaker"):
        parse_asr_options('{"speaker":{"wat":1}}')
