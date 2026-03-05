"""
音频预处理器
提供:
- 降噪 (noise reduction)
- 音量归一化 (RMS normalization)
- 静音裁剪 (silence trimming)
- 格式验证
"""

import logging
import numpy as np
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

__all__ = ['AudioPreprocessor']


class AudioPreprocessor:
    """
    音频预处理器

    支持:
    - 基于频谱减法的降噪
    - RMS 音量归一化到目标电平
    - 首尾静音裁剪
    - 音频格式验证
    - 自适应预处理 (根据 SNR 智能选择)

    用法:
        processor = AudioPreprocessor(target_db=-20.0, denoise_enable=True)
        audio = processor.process(audio_data, sample_rate)
    """

    def __init__(
        self,
        target_db: float = -20.0,
        silence_threshold_db: float = -40.0,
        min_silence_ms: int = 500,
        normalize_enable: bool = True,
        normalize_robust_rms_enable: bool = False,
        normalize_robust_rms_percentile: float = 95.0,
        trim_silence_enable: bool = False,
        denoise_enable: bool = False,
        denoise_prop: float = 0.8,
        denoise_backend: str = "noisereduce",
        vocal_separate_enable: bool = False,
        vocal_separate_model: str = "htdemucs",
        device: str = "cpu",
        adaptive_enable: bool = False,
        snr_threshold: float = 20.0,
        remove_dc_offset: bool = True,
        highpass_enable: bool = False,
        highpass_cutoff_hz: float = 80.0,
        soft_limit_enable: bool = False,
        soft_limit_target: float = 0.98,
        soft_limit_knee: float = 2.0,
    ):
        """
        初始化预处理器

        Args:
            target_db: 目标音量电平 (dB)，默认 -20 dB
            silence_threshold_db: 静音判定阈值 (dB)，默认 -40 dB
            min_silence_ms: 最小静音时长 (毫秒)，默认 500ms
            normalize_enable: 是否启用音量归一化
            normalize_robust_rms_enable: 是否启用“鲁棒 RMS”（优先对活跃段对齐，避免长静音导致过度放大）
            normalize_robust_rms_percentile: 鲁棒 RMS 取样分位（95 表示使用最响的约 5% 帧计算 RMS）
            trim_silence_enable: 是否启用静音裁剪
            denoise_enable: 是否启用降噪
            denoise_prop: 降噪强度 (0-1)，默认 0.8
            denoise_backend: 降噪后端 ("noisereduce", "deepfilter", "deepfilter3")
            vocal_separate_enable: 是否启用人声分离
            vocal_separate_model: Demucs 模型名称
            device: 设备 ("cpu" 或 "cuda")
            adaptive_enable: 是否启用自适应预处理
            snr_threshold: SNR 阈值 (低于此值启用降噪)
            remove_dc_offset: 是否移除 DC offset (均值偏移)
            highpass_enable: 是否启用高通滤波（抑制低频轰鸣/风噪）
            highpass_cutoff_hz: 高通截止频率 (Hz)
            soft_limit_enable: 是否启用软限幅（降低削波尖锐度）
            soft_limit_target: 软限幅目标峰值 (0-1)
            soft_limit_knee: 软限幅曲线强度（越大越“硬”）
        """
        self.target_db = target_db
        self.silence_threshold_db = silence_threshold_db
        self.min_silence_ms = min_silence_ms
        self.normalize_enable = normalize_enable
        self.normalize_robust_rms_enable = normalize_robust_rms_enable
        self.normalize_robust_rms_percentile = normalize_robust_rms_percentile
        self.trim_silence_enable = trim_silence_enable
        self.denoise_enable = denoise_enable
        self.denoise_prop = denoise_prop
        self.denoise_backend = denoise_backend
        self.vocal_separate_enable = vocal_separate_enable
        self.vocal_separate_model = vocal_separate_model
        self.device = device
        self.adaptive_enable = adaptive_enable
        self.snr_threshold = snr_threshold
        self.remove_dc_offset = remove_dc_offset
        self.highpass_enable = highpass_enable
        self.highpass_cutoff_hz = highpass_cutoff_hz
        self.soft_limit_enable = soft_limit_enable
        self.soft_limit_target = soft_limit_target
        self.soft_limit_knee = soft_limit_knee

        # 预计算阈值
        self.target_rms = self._db_to_amplitude(target_db)
        self.silence_threshold = self._db_to_amplitude(silence_threshold_db)

        # 懒加载 noisereduce
        self._nr = None

        # 懒加载 DeepFilterNet
        self._deep_denoiser = None

        # 懒加载人声分离
        self._vocal_separator = None

    @property
    def nr(self):
        """懒加载 noisereduce 模块"""
        if self._nr is None:
            try:
                import noisereduce as nr
                self._nr = nr
            except ImportError:
                logger.warning(
                    "noisereduce not installed, denoising disabled. "
                    "Install with: pip install noisereduce"
                )
                self.denoise_enable = False
                return None
        return self._nr

    @property
    def deep_denoiser(self):
        """懒加载 DeepFilterNet 降噪器"""
        if self._deep_denoiser is None:
            try:
                from src.core.audio.deep_denoise import DeepDenoiser
                self._deep_denoiser = DeepDenoiser(device=self.device)
                if not self._deep_denoiser.is_available():
                    logger.warning("DeepFilterNet not available, falling back to noisereduce")
                    self._deep_denoiser = None
            except Exception as e:
                logger.warning(f"Failed to load DeepFilterNet: {e}")
                self._deep_denoiser = None
        return self._deep_denoiser

    @property
    def vocal_separator(self):
        """懒加载人声分离器"""
        if self._vocal_separator is None:
            try:
                from src.core.audio.vocal_separator import VocalSeparator
                self._vocal_separator = VocalSeparator(
                    model_name=self.vocal_separate_model,
                    device=self.device
                )
                if not self._vocal_separator.is_available():
                    logger.warning("Demucs not available, vocal separation disabled")
                    self._vocal_separator = None
                    self.vocal_separate_enable = False
            except Exception as e:
                logger.warning(f"Failed to load vocal separator: {e}")
                self._vocal_separator = None
                self.vocal_separate_enable = False
        return self._vocal_separator

    @staticmethod
    def _db_to_amplitude(db: float) -> float:
        """dB 转振幅"""
        return 10 ** (db / 20)

    @staticmethod
    def _amplitude_to_db(amplitude: float) -> float:
        """振幅转 dB"""
        return 20 * np.log10(amplitude + 1e-10)

    @staticmethod
    def _highpass_filter_single_pole(
        audio: np.ndarray,
        *,
        cutoff_hz: float,
        sample_rate: int,
    ) -> np.ndarray:
        """Simple single-pole high-pass filter (numpy-only).

        This is intentionally lightweight (no scipy dependency) and mainly targets
        low-frequency rumble / DC-ish drift that hurts VAD + ASR stability.
        """
        if len(audio) == 0:
            return audio

        cutoff = float(cutoff_hz)
        if not (cutoff > 0.0):
            return audio

        # RC high-pass: y[n] = alpha * (y[n-1] + x[n] - x[n-1])
        dt = 1.0 / float(sample_rate or 16000)
        rc = 1.0 / (2.0 * np.pi * cutoff)
        alpha = rc / (rc + dt)

        out = np.empty_like(audio, dtype=np.float32)
        out[0] = float(audio[0])
        prev_x = float(audio[0])
        prev_y = float(out[0])
        for i in range(1, len(audio)):
            x = float(audio[i])
            y = alpha * (prev_y + x - prev_x)
            out[i] = y
            prev_x = x
            prev_y = y
        return out.astype(np.float32, copy=False)

    @staticmethod
    def _soft_limit_tanh(
        audio: np.ndarray,
        *,
        target: float,
        knee: float,
    ) -> np.ndarray:
        """Soft limiter using a tanh curve, scaled to `target` peak."""
        if len(audio) == 0:
            return audio

        t = float(target)
        k = float(knee)
        if not (0.0 < t <= 1.0):
            return audio
        if not (k > 0.0):
            return audio

        denom = float(np.tanh(k)) or 1.0
        out = np.tanh(k * audio) / denom
        out = out * t
        return out.astype(np.float32, copy=False)

    def get_rms(self, audio: np.ndarray) -> float:
        """计算 RMS 值"""
        return np.sqrt(np.mean(audio ** 2))

    def get_rms_db(self, audio: np.ndarray) -> float:
        """计算 RMS dB 值"""
        rms = self.get_rms(audio)
        return self._amplitude_to_db(rms)

    def _estimate_robust_rms(
        self,
        audio: np.ndarray,
        *,
        sample_rate: int,
        percentile: float,
    ) -> float:
        """Estimate a "robust" RMS using only the loudest frames.

        This is mainly to avoid long silences dragging RMS down and causing the
        whole file to be over-amplified (which can hurt ASR on noisy recordings).

        `percentile=95` roughly means: use the loudest ~5% frames to estimate RMS.
        """
        if len(audio) == 0:
            return 0.0

        sr = int(sample_rate) if int(sample_rate or 0) > 0 else 16000
        frame_length = int(sr * 0.025)  # 25ms
        hop_length = int(sr * 0.010)    # 10ms
        if frame_length <= 0 or hop_length <= 0:
            return float(self.get_rms(audio))

        num_frames = (len(audio) - frame_length) // hop_length + 1
        if num_frames <= 0:
            return float(self.get_rms(audio))

        frame_energies = np.empty((num_frames,), dtype=np.float32)
        for i in range(num_frames):
            start = i * hop_length
            end = start + frame_length
            frame = audio[start:end]
            frame_energies[i] = float(np.mean(frame ** 2))

        p = float(percentile)
        if not np.isfinite(p):
            return float(self.get_rms(audio))
        p = max(0.0, min(100.0, p))

        keep_fraction = (100.0 - p) / 100.0
        keep_top = int(np.ceil(num_frames * keep_fraction))
        keep_top = max(1, min(num_frames, keep_top))

        if keep_top >= num_frames:
            energy = float(np.mean(frame_energies))
            return float(np.sqrt(energy))

        # Select the loudest frames by count (not by value) to avoid issues when
        # many frames are exactly 0 (pure silence spans).
        idx = np.argpartition(frame_energies, -keep_top)[-keep_top:]
        selected = frame_energies[idx]
        energy = float(np.mean(selected))
        if not np.isfinite(energy) or energy < 0.0:
            return float(self.get_rms(audio))
        return float(np.sqrt(energy))

    def normalize_volume(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """
        RMS 音量归一化

        将音频归一化到目标 RMS 电平。

        Args:
            audio: 音频数据 (numpy array)
            sample_rate: 采样率 (未使用，保留接口)

        Returns:
            归一化后的音频
        """
        if len(audio) == 0:
            return audio

        # Default to global RMS, but allow a more robust estimate that focuses
        # on the loudest frames (speech/music), which is often better for long
        # recordings with lots of silence.
        global_rms = float(self.get_rms(audio))
        current_rms = global_rms
        if self.normalize_robust_rms_enable:
            robust_rms = self._estimate_robust_rms(
                audio,
                sample_rate=sample_rate,
                percentile=self.normalize_robust_rms_percentile,
            )
            if np.isfinite(robust_rms) and robust_rms > 0.0:
                current_rms = float(robust_rms)

        if current_rms < 1e-10:
            # 静音或接近静音，不处理
            return audio

        # 计算增益
        gain = self.target_rms / current_rms

        # 应用增益，限制最大增益避免过度放大噪声
        max_gain = 10.0  # 最大 20dB 增益
        gain = min(gain, max_gain)

        # 归一化
        normalized = audio * gain

        # 防止削波
        max_val = np.max(np.abs(normalized))
        if max_val > 0.99:
            normalized = normalized * (0.99 / max_val)

        return normalized.astype(audio.dtype)

    def denoise(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """
        降噪处理

        根据配置的 backend 使用不同的降噪算法：
        - noisereduce: 频谱减法，轻量快速
        - deepfilter: DeepFilterNet 深度学习，质量更高
        - deepfilter3: DeepFilterNet v3，最高质量 (PESQ 3.5+)

        Args:
            audio: 音频数据 (numpy array)
            sample_rate: 采样率

        Returns:
            降噪后的音频
        """
        if len(audio) == 0:
            return audio

        backend = str(self.denoise_backend or "").strip().lower()

        # 使用 ClearVoice (ClearerVoice-Studio) - local or via microservice.
        if backend.startswith("clearvoice"):
            try:
                # Prefer a dedicated service when configured (keeps ASR containers lighter).
                from src.config import settings

                base_url = str(getattr(settings, "clearvoice_service_base_url", "") or "").strip()
                if base_url:
                    from src.core.audio.clearvoice_service_client import clearvoice_service_enhance

                    return clearvoice_service_enhance(audio, sample_rate=sample_rate)

                from src.core.audio.clearvoice_denoise import clearvoice_enhance

                return clearvoice_enhance(audio, sample_rate=sample_rate)
            except Exception as e:
                # Fail fast: user explicitly requested ClearVoice denoise.
                logger.error("ClearVoice denoise failed: %s", e, exc_info=True)
                raise

        # 使用 DeepFilterNet (v2 或 v3)
        if backend in ("deepfilter", "deepfilter3"):
            if self.deep_denoiser is not None:
                return self.deep_denoiser.enhance(audio, sample_rate)
            logger.warning("DeepFilterNet not available, falling back to noisereduce")

        # 使用 noisereduce
        if self.nr is None:
            return audio

        try:
            denoised = self.nr.reduce_noise(
                y=audio,
                sr=sample_rate,
                stationary=False,  # 非平稳噪声
                prop_decrease=self.denoise_prop,
            )
            return denoised.astype(audio.dtype)
        except Exception as e:
            logger.warning(f"Denoising failed: {e}")
            return audio

    def trim_silence(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> np.ndarray:
        """
        裁剪首尾静音

        使用帧能量分析检测静音段。

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            裁剪后的音频
        """
        if len(audio) == 0:
            return audio

        # 帧参数
        frame_length_ms = 25  # 25ms 帧
        hop_length_ms = 10    # 10ms 步长
        frame_length = int(sample_rate * frame_length_ms / 1000)
        hop_length = int(sample_rate * hop_length_ms / 1000)

        # 最小静音帧数
        min_silence_frames = int(self.min_silence_ms / hop_length_ms)

        # 计算帧能量
        num_frames = (len(audio) - frame_length) // hop_length + 1
        if num_frames <= 0:
            return audio

        frame_energies = np.zeros(num_frames)
        for i in range(num_frames):
            start = i * hop_length
            end = start + frame_length
            frame = audio[start:end]
            frame_energies[i] = np.sqrt(np.mean(frame ** 2))

        # 查找非静音区域
        threshold = self.silence_threshold
        is_speech = frame_energies > threshold

        if not np.any(is_speech):
            # 全是静音
            return audio

        # 找到第一个和最后一个语音帧
        speech_indices = np.where(is_speech)[0]
        first_speech = speech_indices[0]
        last_speech = speech_indices[-1]

        # 转换为样本索引，保留一些边界
        margin_frames = 5  # 保留边界帧
        start_frame = max(0, first_speech - margin_frames)
        end_frame = min(num_frames - 1, last_speech + margin_frames)

        start_sample = start_frame * hop_length
        end_sample = min(len(audio), (end_frame + 1) * hop_length + frame_length)

        return audio[start_sample:end_sample]

    def validate(self, audio: np.ndarray, sample_rate: int) -> Tuple[bool, str]:
        """
        验证音频格式

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            (是否有效, 错误信息)
        """
        if audio is None:
            return False, "音频数据为空"

        if len(audio) == 0:
            return False, "音频长度为0"

        if sample_rate < 8000:
            return False, f"采样率过低: {sample_rate}Hz (最低 8000Hz)"

        if sample_rate > 48000:
            return False, f"采样率过高: {sample_rate}Hz (最高 48000Hz)"

        # 检查音频时长
        duration = len(audio) / sample_rate
        if duration < 0.1:
            return False, f"音频时长过短: {duration:.2f}s (最短 0.1s)"

        if duration > 3600:
            return False, f"音频时长过长: {duration:.2f}s (最长 3600s)"

        return True, ""

    def estimate_snr(self, audio: np.ndarray, sample_rate: int = 16000) -> float:
        """
        估算信噪比 (SNR)

        使用简化的 VAD 方法：将信号分为语音段和非语音段，
        计算两者能量比作为 SNR 估计。

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            估算的 SNR (dB)
        """
        if len(audio) == 0:
            return 0.0

        # 帧参数
        frame_length = int(sample_rate * 0.025)  # 25ms
        hop_length = int(sample_rate * 0.010)    # 10ms

        # 计算帧能量
        num_frames = (len(audio) - frame_length) // hop_length + 1
        if num_frames <= 0:
            return 0.0

        frame_energies = np.zeros(num_frames)
        for i in range(num_frames):
            start = i * hop_length
            end = start + frame_length
            frame = audio[start:end]
            frame_energies[i] = np.mean(frame ** 2)

        if len(frame_energies) == 0:
            return 0.0

        # 使用能量阈值区分语音和噪声
        threshold = np.percentile(frame_energies, 30)  # 假设 30% 是噪声

        speech_energy = frame_energies[frame_energies > threshold]
        noise_energy = frame_energies[frame_energies <= threshold]

        if len(speech_energy) == 0 or len(noise_energy) == 0:
            return 30.0  # 默认返回较高 SNR

        avg_speech = np.mean(speech_energy)
        avg_noise = np.mean(noise_energy)

        if avg_noise < 1e-10:
            return 50.0  # 几乎无噪声

        snr = 10 * np.log10(avg_speech / avg_noise)
        return max(0.0, min(50.0, snr))  # 限制范围

    def process(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        validate: bool = True,
    ) -> np.ndarray:
        """
        执行完整预处理流程

        处理顺序:
        1. 格式验证 (可选)
        2. 自适应预处理决策 (如果启用)
        3. 人声分离 (如果启用)
        4. 降噪 (如果启用或自适应判定需要)
        5. 静音裁剪 (如果启用)
        6. 音量归一化 (如果启用)

        Args:
            audio: 音频数据
            sample_rate: 采样率
            validate: 是否进行格式验证

        Returns:
            处理后的音频

        Raises:
            ValueError: 格式验证失败时抛出
        """
        if validate:
            is_valid, error = self.validate(audio, sample_rate)
            if not is_valid:
                raise ValueError(f"音频验证失败: {error}")

        # 0. Remove DC offset (helps ASR stability on some recordings).
        if self.remove_dc_offset and len(audio) > 0:
            audio = audio - float(np.mean(audio))

        # 0.1 Optional high-pass (reduce low-frequency rumble).
        if self.highpass_enable:
            audio = self._highpass_filter_single_pole(
                audio,
                cutoff_hz=self.highpass_cutoff_hz,
                sample_rate=sample_rate,
            )

        # 0.2 Optional soft limiting (reduce hard-clipping harshness).
        if self.soft_limit_enable:
            audio = self._soft_limit_tanh(
                audio,
                target=self.soft_limit_target,
                knee=self.soft_limit_knee,
            )

        # 自适应预处理：根据 SNR 决定是否需要降噪
        should_denoise = self.denoise_enable
        should_trim = self.trim_silence_enable

        if self.adaptive_enable:
            snr = self.estimate_snr(audio, sample_rate)
            logger.debug(f"Estimated SNR: {snr:.1f} dB (threshold: {self.snr_threshold} dB)")

            # Adaptive denoising:
            # - Low SNR: enable denoise (even if denoise_enable=False) to improve intelligibility.
            # - High SNR: disable denoise (even if denoise_enable=True) to avoid damaging clean audio.
            if snr < self.snr_threshold:
                should_denoise = True
                logger.debug(f"Adaptive: enabling denoising (SNR={snr:.1f} < {self.snr_threshold})")
            else:
                should_denoise = False
                logger.debug(f"Adaptive: skipping denoising (SNR={snr:.1f} >= {self.snr_threshold})")

            # 检测是否有大量静音
            rms_db = self.get_rms_db(audio)
            if rms_db < -30:
                should_trim = True
                logger.debug(f"Adaptive: enabling silence trimming (RMS={rms_db:.1f} dB)")

        # 1. 人声分离 (应在降噪之前)
        if self.vocal_separate_enable and self.vocal_separator is not None:
            audio = self.vocal_separator.separate_vocals(audio, sample_rate)

        # 2. 降噪 (应在其他处理之前)
        if should_denoise:
            audio = self.denoise(audio, sample_rate)

        # 3. 静音裁剪
        if should_trim:
            audio = self.trim_silence(audio, sample_rate)

        # 4. 音量归一化
        if self.normalize_enable:
            audio = self.normalize_volume(audio, sample_rate)

        return audio

    def get_audio_info(self, audio: np.ndarray, sample_rate: int) -> dict:
        """
        获取音频信息

        Args:
            audio: 音频数据
            sample_rate: 采样率

        Returns:
            音频信息字典
        """
        if len(audio) == 0:
            return {
                'duration': 0.0,
                'samples': 0,
                'sample_rate': sample_rate,
                'rms_db': float('-inf'),
                'peak_db': float('-inf'),
                'dc_offset': 0.0,
                'clipping_ratio': 0.0,
            }

        duration = len(audio) / sample_rate
        rms = self.get_rms(audio)
        peak = np.max(np.abs(audio))
        dc_offset = float(np.mean(audio))

        # Best-effort clipping detection for float waveforms in [-1, 1].
        clip_threshold = 0.999
        clipping_ratio = float(np.mean(np.abs(audio) >= clip_threshold))

        return {
            'duration': duration,
            'samples': len(audio),
            'sample_rate': sample_rate,
            'rms_db': self._amplitude_to_db(rms),
            'peak_db': self._amplitude_to_db(peak),
            'dc_offset': dc_offset,
            'clipping_ratio': clipping_ratio,
        }


if __name__ == '__main__':
    # 测试
    print("=== AudioPreprocessor 测试 ===")

    # 创建测试音频
    sample_rate = 16000
    duration = 2.0
    t = np.linspace(0, duration, int(sample_rate * duration))

    # 低音量正弦波 + 首尾静音
    silence_duration = 0.3
    silence_samples = int(sample_rate * silence_duration)
    signal = np.sin(2 * np.pi * 440 * t) * 0.01  # -40dB 左右
    audio = np.concatenate([
        np.zeros(silence_samples),
        signal,
        np.zeros(silence_samples),
    ]).astype(np.float32)

    processor = AudioPreprocessor(
        target_db=-20.0,
        normalize_enable=True,
        trim_silence_enable=True,
    )

    print(f"\n原始音频:")
    info = processor.get_audio_info(audio, sample_rate)
    print(f"  时长: {info['duration']:.2f}s")
    print(f"  RMS: {info['rms_db']:.1f} dB")
    print(f"  Peak: {info['peak_db']:.1f} dB")

    processed = processor.process(audio, sample_rate)

    print(f"\n处理后音频:")
    info = processor.get_audio_info(processed, sample_rate)
    print(f"  时长: {info['duration']:.2f}s")
    print(f"  RMS: {info['rms_db']:.1f} dB")
    print(f"  Peak: {info['peak_db']:.1f} dB")

    print("\n✓ 测试完成")
