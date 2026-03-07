import numpy as np

from src.core.audio.preprocessor import AudioPreprocessor


def _sine(freq_hz: float, duration_s: float, sr: int, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(round(duration_s * sr)), dtype=np.float32) / float(sr)
    return (amp * np.sin(2.0 * np.pi * float(freq_hz) * t)).astype(np.float32)


def test_highpass_attenuates_low_frequency_energy():
    sr = 16000
    audio = _sine(50.0, 2.0, sr, amp=0.5)

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        highpass_enable=True,
        highpass_cutoff_hz=200.0,
    )

    out = pre.process(audio, sample_rate=sr, validate=False)
    before_rms = float(np.sqrt(np.mean(audio ** 2)))
    after_rms = float(np.sqrt(np.mean(out ** 2)))

    assert after_rms < before_rms * 0.4


def test_highpass_keeps_high_frequency_energy_reasonable():
    sr = 16000
    audio = _sine(1000.0, 2.0, sr, amp=0.5)

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        highpass_enable=True,
        highpass_cutoff_hz=200.0,
    )

    out = pre.process(audio, sample_rate=sr, validate=False)
    before_rms = float(np.sqrt(np.mean(audio ** 2)))
    after_rms = float(np.sqrt(np.mean(out ** 2)))

    assert after_rms > before_rms * 0.8


def test_lowpass_attenuates_high_frequency_energy():
    sr = 16000
    audio = _sine(6000.0, 2.0, sr, amp=0.5)

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        lowpass_enable=True,
        lowpass_cutoff_hz=1000.0,
    )

    out = pre.process(audio, sample_rate=sr, validate=False)
    before_rms = float(np.sqrt(np.mean(audio ** 2)))
    after_rms = float(np.sqrt(np.mean(out ** 2)))

    assert after_rms < before_rms * 0.2


def test_lowpass_keeps_low_frequency_energy_reasonable():
    sr = 16000
    audio = _sine(500.0, 2.0, sr, amp=0.5)

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        lowpass_enable=True,
        lowpass_cutoff_hz=1000.0,
    )

    out = pre.process(audio, sample_rate=sr, validate=False)
    before_rms = float(np.sqrt(np.mean(audio ** 2)))
    after_rms = float(np.sqrt(np.mean(out ** 2)))

    assert after_rms > before_rms * 0.8


def test_bandpass_keeps_midband_and_attenuates_outside_energy():
    sr = 16000

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        bandpass_enable=True,
        bandpass_low_hz=300.0,
        bandpass_high_hz=3400.0,
    )

    low = _sine(100.0, 2.0, sr, amp=0.5)
    mid = _sine(1000.0, 2.0, sr, amp=0.5)
    high = _sine(6000.0, 2.0, sr, amp=0.5)

    out_low = pre.process(low, sample_rate=sr, validate=False)
    out_mid = pre.process(mid, sample_rate=sr, validate=False)
    out_high = pre.process(high, sample_rate=sr, validate=False)

    low_before = float(np.sqrt(np.mean(low ** 2)))
    mid_before = float(np.sqrt(np.mean(mid ** 2)))
    high_before = float(np.sqrt(np.mean(high ** 2)))

    low_after = float(np.sqrt(np.mean(out_low ** 2)))
    mid_after = float(np.sqrt(np.mean(out_mid ** 2)))
    high_after = float(np.sqrt(np.mean(out_high ** 2)))

    assert low_after < low_before * 0.4
    assert mid_after > mid_before * 0.8
    assert high_after < high_before * 0.4


def test_soft_limiter_reduces_clipping_ratio():
    # Build a heavily clipped waveform (flat at +/-1.0).
    audio = np.zeros((16000,), dtype=np.float32)
    audio[::2] = 1.0
    audio[1::2] = -1.0

    pre = AudioPreprocessor(
        normalize_enable=False,
        trim_silence_enable=False,
        denoise_enable=False,
        adaptive_enable=False,
        remove_dc_offset=False,
        soft_limit_enable=True,
        soft_limit_target=0.98,
        soft_limit_knee=3.0,
    )

    out = pre.process(audio, sample_rate=16000, validate=False)
    before_clip = float(np.mean(np.abs(audio) >= 0.999))
    after_clip = float(np.mean(np.abs(out) >= 0.999))

    assert before_clip > 0.9
    assert after_clip < 0.01
