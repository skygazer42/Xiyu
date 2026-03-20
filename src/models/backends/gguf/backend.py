"""GGUF 后端主实现

移植自 CapsWriter-Offline，封装为 Xiyu ASRBackend 接口。
"""

import os
import time
import ctypes
import logging
import numpy as np
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

from ..base import ASRBackend
from .dataclasses import GGUFConfig, Timings
from .onnx_utils import load_onnx_models, encode_audio
from .ctc_utils import load_ctc_tokens, decode_ctc, align_timestamps
from .llama_cpp import llama_lib, llama_token, llama_batch, ByteDecoder, get_token_embeddings_gguf
from src.config import settings

logger = logging.getLogger(__name__)


class GGUFBackend(ASRBackend):
    """GGUF 量化模型后端

    使用 ONNX (encoder/CTC) + llama.cpp (GGUF decoder) 进行语音识别。
    移植自 CapsWriter-Offline。

    需要的模型文件:
    - encoder ONNX: Fun-ASR-Nano-Encoder-Adaptor.(fp16|fp32|int8).onnx
    - CTC ONNX: Fun-ASR-Nano-CTC.(int8|fp16|fp32).onnx
    - decoder GGUF: Fun-ASR-Nano-Decoder.q8_0.gguf
    - tokens.txt: CTC 词表

    还需要 llama.cpp 动态库:
    - Windows: ggml.dll, ggml-base.dll, llama.dll
    - Linux: libggml.so, libggml-base.so, libllama.so
    """

    def __init__(
        self,
        encoder_path: str,
        ctc_path: str,
        decoder_path: str,
        tokens_path: str,
        lib_dir: Optional[str] = None,
        n_predict: int = 512,
        n_threads: Optional[int] = None,
        n_threads_batch: Optional[int] = None,
        n_ubatch: int = 512,
        **kwargs
    ):
        """初始化 GGUF 后端

        Args:
            encoder_path: Encoder ONNX 模型路径
            ctc_path: CTC ONNX 模型路径
            decoder_path: Decoder GGUF 模型路径
            tokens_path: CTC tokens 文件路径
            lib_dir: llama.cpp 库目录
            n_predict: 最大生成 token 数
            n_threads: 推理线程数
            n_threads_batch: 批处理线程数
            n_ubatch: llama.cpp ubatch 大小
        """
        resolved_encoder_path = self._resolve_artifact_path(
            encoder_path,
            kind="encoder",
            candidate_filenames=[
                # GGUF is typically CPU-only; prefer int8 to avoid fp16 CPU NaNs
                # on some onnxruntime CPU kernels.
                "Fun-ASR-Nano-Encoder-Adaptor.int8.onnx",
                "Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx",
                "Fun-ASR-Nano-Encoder-Adaptor.fp32.onnx",
            ],
        )
        resolved_ctc_path = self._resolve_artifact_path(
            ctc_path,
            kind="ctc",
            candidate_filenames=[
                "Fun-ASR-Nano-CTC.int8.onnx",
                "Fun-ASR-Nano-CTC.fp16.onnx",
                "Fun-ASR-Nano-CTC.fp32.onnx",
            ],
        )
        resolved_decoder_path = self._resolve_artifact_path(
            decoder_path,
            kind="decoder",
            candidate_filenames=[
                "Fun-ASR-Nano-Decoder.q8_0.gguf",
            ],
        )
        resolved_tokens_path = self._resolve_artifact_path(
            tokens_path,
            kind="tokens",
            candidate_filenames=[
                "tokens.txt",
            ],
        )

        self.config = GGUFConfig(
            encoder_onnx_path=resolved_encoder_path,
            ctc_onnx_path=resolved_ctc_path,
            decoder_gguf_path=resolved_decoder_path,
            tokens_path=resolved_tokens_path,
            n_predict=n_predict,
            n_threads=n_threads,
            n_threads_batch=n_threads_batch,
            n_ubatch=n_ubatch,
        )

        # 默认库目录为模型目录下的 bin 子目录
        if lib_dir is None:
            lib_dir = Path(resolved_decoder_path).parent / "bin"
        self.lib_dir = self._resolve_lib_dir(lib_dir)

        # 运行时对象
        self._encoder_sess = None
        self._ctc_sess = None
        self._model = None
        self._ctx = None
        self._ctx_n_ctx = None
        self._ctx_n_batch = None
        self._vocab = None
        self._eos_token = None
        self._embedding_table = None
        self._ctc_id2token = None

        self._loaded = False
        self._stop_tokens = [151643, 151645]  # Qwen2.5 stop tokens

    @staticmethod
    def _resolve_artifact_path(
        raw_path: str,
        *,
        kind: str,
        candidate_filenames: List[str],
    ) -> str:
        """Resolve GGUF artifact paths for common layouts.

        Users may provide absolute paths via env vars (Docker), or relative paths
        in local runs. Additionally, the model artifacts are commonly placed
        under:

          data/models/Fun-ASR-Nano-GGUF/

        This helper keeps backward compatibility with older defaults that used
        `data/models/` directly.
        """
        raw = str(raw_path or "").strip()
        if not raw:
            return raw

        # Candidate directories to probe when the configured path doesn't exist.
        candidate_dirs: List[Path] = [
            settings.models_dir / "Fun-ASR-Nano-GGUF",
            settings.models_dir,
        ]

        attempted: List[Path] = []

        def _probe(path: Path) -> Optional[Path]:
            attempted.append(path)
            if path.is_file():
                return path
            if path.is_dir():
                for name in candidate_filenames:
                    p = path / name
                    attempted.append(p)
                    if p.is_file():
                        return p
            return None

        # 1) Try the configured value as-is (absolute or relative).
        p0 = Path(raw)
        found = _probe(p0)
        if found:
            return str(found)

        # 2) If relative, try common anchors: repo base, data_dir, models_dir.
        if not p0.is_absolute():
            anchors = [settings.base_dir, settings.data_dir, settings.models_dir]
            for anchor in anchors:
                found = _probe(anchor / p0)
                if found:
                    logger.info(f"[gguf] Resolved {kind} path: {raw} -> {found}")
                    return str(found)

        # 3) Search by filename in common model directories.
        for d in candidate_dirs:
            for name in candidate_filenames:
                found = _probe(d / name)
                if found:
                    logger.info(f"[gguf] Resolved {kind} path: {raw} -> {found}")
                    return str(found)

        # Keep original for error reporting, but normalize relative paths to data_dir.
        if not p0.is_absolute():
            normalized = settings.data_dir / p0
            logger.warning(
                f"[gguf] {kind} artifact not found at configured path '{raw}'. "
                f"Tried {len(attempted)} candidates; keeping '{normalized}'."
            )
            return str(normalized)

        logger.warning(
            f"[gguf] {kind} artifact not found at configured path '{raw}'. "
            f"Tried {len(attempted)} candidates; keeping '{raw}'."
        )
        return raw

    @staticmethod
    def _resolve_lib_dir(raw_dir: str | Path) -> Path:
        """Resolve llama.cpp shared library directory for common layouts.

        Historically the project used `data/models/bin` for llama.cpp libs
        (host-provided builds). Newer Docker images bundle the shared libs under
        `/app/llama_cpp/lib` and expose `GGUF_LIB_DIR` accordingly.
        """
        raw = str(raw_dir or "").strip()
        if not raw:
            return Path(raw_dir)

        p0 = Path(raw)
        attempted: List[Path] = []

        def _probe_dir(path: Path) -> Optional[Path]:
            attempted.append(path)
            if path.is_dir():
                return path
            return None

        # 1) Try the configured value as-is (absolute or relative).
        found = _probe_dir(p0)
        if found:
            return found

        # 2) If relative, try common anchors: repo base, data_dir, models_dir.
        if not p0.is_absolute():
            anchors = [settings.base_dir, settings.data_dir, settings.models_dir]
            for anchor in anchors:
                found = _probe_dir(anchor / p0)
                if found:
                    logger.info(f"[gguf] Resolved lib dir: {raw} -> {found}")
                    return found

        # 3) Docker image bundled libs.
        for d in (Path("/app/llama_cpp/lib"),):
            found = _probe_dir(d)
            if found:
                logger.info(f"[gguf] Resolved lib dir: {raw} -> {found}")
                return found

        # Keep original for error reporting, but normalize relative paths to data_dir.
        if not p0.is_absolute():
            normalized = settings.data_dir / p0
            logger.warning(
                f"[gguf] lib_dir not found at configured path '{raw}'. "
                f"Tried {len(attempted)} candidates; keeping '{normalized}'."
            )
            return normalized

        logger.warning(
            f"[gguf] lib_dir not found at configured path '{raw}'. "
            f"Tried {len(attempted)} candidates; keeping '{p0}'."
        )
        return p0

    def load(self) -> None:
        """加载模型"""
        if self._loaded:
            return

        t_start = time.perf_counter()

        # 1. 加载 ONNX 模型
        logger.info("[1/5] Loading ONNX models...")
        self._encoder_sess, self._ctc_sess, _ = load_onnx_models(
            self.config.encoder_onnx_path,
            self.config.ctc_onnx_path
        )

        # 2. 初始化 llama.cpp
        logger.info("[2/5] Initializing llama.cpp...")
        llama_lib.init(self.lib_dir)

        # 3. 加载 GGUF 模型
        logger.info("[3/5] Loading GGUF decoder...")
        model_params = llama_lib.llama_model_default_params()
        model_path = Path(self.config.decoder_gguf_path).resolve()
        self._model = llama_lib.llama_model_load_from_file(
            str(model_path).encode('utf-8'),
            model_params
        )
        if not self._model:
            raise RuntimeError(f"Failed to load GGUF model: {model_path}")

        self._vocab = llama_lib.llama_model_get_vocab(self._model)
        self._eos_token = llama_lib.llama_vocab_eos(self._vocab)

        # 4. 加载 embedding 权重
        logger.info("[4/5] Loading embeddings...")
        self._embedding_table = get_token_embeddings_gguf(self.config.decoder_gguf_path)

        # 5. 创建上下文
        logger.info("[5/5] Creating LLM context...")
        self._ctx = self._create_context()

        # 加载 CTC 词表
        self._ctc_id2token = load_ctc_tokens(self.config.tokens_path)

        self._loaded = True
        t_cost = time.perf_counter() - t_start
        logger.info(f"GGUF backend loaded in {t_cost:.2f}s")

    def _create_context(self):
        """创建 LLM 上下文"""
        ctx_params = llama_lib.llama_context_default_params()
        # NOTE: llama.cpp enforces `n_tokens_all <= n_batch` for each decode call.
        # We inject one embedding token per audio frame, so long audio chunks can
        # exceed the default 2048 and trigger a hard assert (process abort).
        #
        # Make this configurable via env:
        #   GGUF_N_CTX / GGUF_N_BATCH
        # and keep a sane lower bound for compatibility.
        try:
            n_ctx = int(getattr(settings, "gguf_n_ctx", 2048) or 2048)
        except Exception:
            n_ctx = 2048
        if n_ctx <= 0:
            n_ctx = 2048

        try:
            n_batch = int(getattr(settings, "gguf_n_batch", n_ctx) or n_ctx)
        except Exception:
            n_batch = n_ctx
        if n_batch <= 0:
            n_batch = n_ctx
        if n_batch > n_ctx:
            n_batch = n_ctx

        ctx_params.n_ctx = n_ctx
        ctx_params.n_batch = n_batch
        ctx_params.n_ubatch = self.config.n_ubatch
        ctx_params.embeddings = False
        ctx_params.no_perf = True
        ctx_params.n_threads = self.config.n_threads or (os.cpu_count() // 2)
        ctx_params.n_threads_batch = self.config.n_threads_batch or os.cpu_count()
        self._ctx_n_ctx = n_ctx
        self._ctx_n_batch = n_batch
        return llama_lib.llama_init_from_model(self._model, ctx_params)

    def _build_prompt(self, hotwords: Optional[List[str]] = None):
        """构建 prompt embeddings"""
        prefix_prompt = "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n<|im_start|>user\n"

        if hotwords:
            hotwords_str = ", ".join(hotwords[:20])  # 最多 20 个热词
            prefix_prompt += f"热词列表：[{hotwords_str}]\n"

        prefix_prompt += "语音转写："
        suffix_prompt = "<|im_end|>\n<|im_start|>assistant\n"

        prefix_tokens = llama_lib.text_to_tokens(self._vocab, prefix_prompt)
        suffix_tokens = llama_lib.text_to_tokens(self._vocab, suffix_prompt)

        prefix_embd = self._embedding_table[prefix_tokens].astype(np.float32)
        suffix_embd = self._embedding_table[suffix_tokens].astype(np.float32)

        return prefix_embd, suffix_embd, len(prefix_tokens), len(suffix_tokens)

    def _decode_llm(self, full_embd: np.ndarray, n_input_tokens: int) -> tuple:
        """执行 LLM 解码"""
        # 清空 KV cache
        mem = llama_lib.llama_get_memory(self._ctx)
        llama_lib.llama_memory_clear(mem, True)

        # 注入 embeddings
        t_inject_start = time.perf_counter()
        batch_embd = llama_lib.llama_batch_init(n_input_tokens, full_embd.shape[1], 1)
        batch_embd.n_tokens = n_input_tokens
        batch_embd.token = ctypes.cast(None, ctypes.POINTER(llama_token))

        if not full_embd.flags['C_CONTIGUOUS']:
            full_embd = np.ascontiguousarray(full_embd)
        ctypes.memmove(batch_embd.embd, full_embd.ctypes.data, full_embd.nbytes)

        for k in range(n_input_tokens):
            batch_embd.pos[k] = k
            batch_embd.n_seq_id[k] = 1
            batch_embd.seq_id[k][0] = 0
            batch_embd.logits[k] = 1 if k == n_input_tokens - 1 else 0

        ret = llama_lib.llama_decode(self._ctx, batch_embd)
        llama_lib.llama_batch_free(batch_embd)
        if ret != 0:
            raise RuntimeError(f"LLM decode failed (ret={ret})")

        t_inject = time.perf_counter() - t_inject_start

        # 生成循环
        t_gen_start = time.perf_counter()
        vocab_size = llama_lib.llama_vocab_n_tokens(self._vocab)
        batch_text = llama_lib.llama_batch_init(1, 0, 1)
        batch_text.n_tokens = 1

        generated_text = ""
        current_pos = n_input_tokens
        decoder_utf8 = ByteDecoder()
        tokens_generated = 0
        repetition_break = False

        for step in range(self.config.n_predict):
            logits_ptr = llama_lib.llama_get_logits(self._ctx)
            logits_arr = np.ctypeslib.as_array(logits_ptr, shape=(vocab_size,))
            token_id = int(np.argmax(logits_arr))

            if token_id == self._eos_token or token_id in self._stop_tokens:
                break

            raw_bytes = llama_lib.token_to_bytes(self._vocab, token_id)
            text_piece = decoder_utf8.decode(raw_bytes)
            generated_text += text_piece
            tokens_generated += 1

            # 熔断检测
            if step == 0:
                last_token_id = token_id
                consecutive_cnt = 1
            elif token_id == last_token_id:
                consecutive_cnt += 1
                if consecutive_cnt > 20:
                    logger.warning("Detected abnormal repetition, breaking")
                    repetition_break = True
                    break
            else:
                last_token_id = token_id
                consecutive_cnt = 1

            batch_text.token[0] = token_id
            batch_text.pos[0] = current_pos
            batch_text.n_seq_id[0] = 1
            batch_text.seq_id[0][0] = 0
            batch_text.logits[0] = 1

            if llama_lib.llama_decode(self._ctx, batch_text) != 0:
                break
            current_pos += 1

        remaining = decoder_utf8.flush()
        generated_text += remaining

        llama_lib.llama_batch_free(batch_text)
        t_gen = time.perf_counter() - t_gen_start

        return generated_text.strip(), tokens_generated, t_inject, t_gen, repetition_break

    @staticmethod
    def _count_cjk_chars(text: str) -> int:
        # Basic CJK Unified Ideographs range; good enough for a lightweight heuristic.
        return sum(1 for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff")

    def _should_fallback_to_ctc(self, *, llm_text: str, ctc_text: str, repetition_break: bool) -> bool:
        """Decide whether to use CTC text instead of LLM-decoded text.

        In practice, some GGUF decoder models can occasionally get stuck in
        short filler loops ("嗯嗯嗯...") or ASCII repetition. When that happens,
        the CTC output is usually far more useful than returning nonsense.
        """
        if repetition_break:
            return True

        ctc = str(ctc_text or "").strip()
        llm = str(llm_text or "").strip()
        if not ctc:
            return False
        if not llm:
            return True

        # If CTC has substantial CJK content but LLM output doesn't, treat LLM
        # output as unreliable.
        ctc_cjk = self._count_cjk_chars(ctc)
        llm_cjk = self._count_cjk_chars(llm)
        if ctc_cjk >= 10 and llm_cjk < max(2, int(ctc_cjk * 0.2)):
            return True

        # If LLM output is far shorter than CTC for longer utterances, it's
        # likely a partial or collapsed output.
        if len(ctc) >= 60 and len(llm) < int(len(ctc) * 0.3):
            return True

        # Guard against trivial single-character loops.
        if len(llm) >= 20 and len(set(llm)) <= 3:
            return True

        return False

    def transcribe(
        self,
        audio_input: Union[bytes, str, Path, np.ndarray],
        hotwords: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """执行转写

        Args:
            audio_input: 音频输入
            hotwords: 热词字符串（换行分隔）
            **kwargs: 其他参数

        Returns:
            转写结果字典
        """
        if not self._loaded:
            self.load()

        timings = Timings()
        t_start = time.perf_counter()

        # 1. 加载音频
        audio = self._load_audio(audio_input)

        # Some GGUF ONNX exports are brittle on very short chunks (e.g. GatherND
        # index out of range). Pad with silence to a minimum length so encoder
        # + CTC have enough frames.
        try:
            min_samples = int(getattr(settings, "gguf_min_samples", 0) or 0)
        except Exception:
            min_samples = 0
        if min_samples > 0 and int(audio.shape[0]) < min_samples:
            pad = min_samples - int(audio.shape[0])
            if pad > 0:
                audio = np.pad(audio, (0, pad), mode="constant")

        # 2. 音频编码
        t_enc = time.perf_counter()
        audio_embd, enc_output = encode_audio(audio, self._encoder_sess)
        timings.encode = time.perf_counter() - t_enc

        # 3. CTC 解码
        t_ctc = time.perf_counter()
        # Different exported CTC heads may expect float32 or float16 inputs.
        # Match the model input dtype to avoid onnxruntime INVALID_ARGUMENT errors.
        ctc_in = enc_output
        try:
            ctc_input_type = str(self._ctc_sess.get_inputs()[0].type or "")
        except Exception:
            ctc_input_type = ""
        if "float16" in ctc_input_type:
            ctc_in = enc_output.astype(np.float16, copy=False)
        elif "float" in ctc_input_type:
            ctc_in = enc_output.astype(np.float32, copy=False)

        ctc_out = self._ctc_sess.run(None, {"enc_output": ctc_in})[0]
        ctc_text, ctc_results = decode_ctc(ctc_out, self._ctc_id2token)
        timings.ctc = time.perf_counter() - t_ctc

        # 4. 准备 Prompt
        t_prep = time.perf_counter()
        hotwords_list = None
        if hotwords:
            hotwords_list = [hw.strip() for hw in hotwords.split('\n') if hw.strip()]

        prefix_embd, suffix_embd, n_prefix, n_suffix = self._build_prompt(hotwords_list)
        full_embd = np.concatenate([prefix_embd, audio_embd.astype(np.float32), suffix_embd], axis=0)
        timings.prepare = time.perf_counter() - t_prep

        # 5. LLM 解码
        # Prevent hard llama.cpp asserts by rejecting oversized batches early.
        n_input_tokens = int(full_embd.shape[0])
        max_batch = int(self._ctx_n_batch or 0)
        if max_batch > 0 and n_input_tokens > max_batch:
            raise ValueError(
                f"GGUF input too long for llama.cpp batch: n_input_tokens={n_input_tokens} > n_batch={max_batch}. "
                f"Reduce chunk duration (VAD_MAX_SEGMENT_MS / SPEAKER_EXTERNAL_DIARIZER_MAX_TURN_DURATION_S) "
                f"or increase GGUF_N_BATCH/GGUF_N_CTX."
            )
        text, n_gen, t_inject, t_gen, repetition_break = self._decode_llm(full_embd, full_embd.shape[0])
        timings.inject = t_inject
        timings.llm_generate = t_gen

        # Fallback: return CTC output when the LLM-decoder collapses into
        # repetition / filler text. This keeps the backend usable in real
        # deployments even when the decoder model is unstable on certain inputs.
        if self._should_fallback_to_ctc(llm_text=text, ctc_text=ctc_text, repetition_break=repetition_break):
            logger.info("[gguf] LLM output looks unreliable; falling back to CTC text")
            text = ctc_text

        # 6. 时间戳对齐
        t_align = time.perf_counter()
        aligned = []
        if ctc_results:
            aligned = align_timestamps(ctc_results, text)
        timings.align = time.perf_counter() - t_align

        timings.total = time.perf_counter() - t_start

        # 构建句子信息
        sentence_info = []
        if aligned:
            # align_timestamps returns seconds; Xiyu API schema expects ms integers.
            try:
                start_s = float(aligned[0].get("start", 0.0) or 0.0)
            except Exception:
                start_s = 0.0
            try:
                end_s = float(aligned[-1].get("start", start_s) or start_s) + 0.1
            except Exception:
                end_s = start_s

            sentence_info.append(
                {
                    "text": text,
                    "start": int(round(max(start_s, 0.0) * 1000.0)),
                    "end": int(round(max(end_s, start_s) * 1000.0)),
                }
            )

        return {
            "text": text,
            "sentence_info": sentence_info,
            "ctc_text": ctc_text,
            "timings": {
                "encode": timings.encode,
                "ctc": timings.ctc,
                "prepare": timings.prepare,
                "inject": timings.inject,
                "llm_generate": timings.llm_generate,
                "align": timings.align,
                "total": timings.total,
            }
        }

    def _load_audio(self, audio_input) -> np.ndarray:
        """加载音频为 numpy 数组"""
        if isinstance(audio_input, np.ndarray):
            return audio_input.astype(np.float32)

        if isinstance(audio_input, (bytes, bytearray)):
            data = bytes(audio_input)

            # Xiyu's HTTP layer uses raw PCM16LE (16kHz, mono) bytes.
            # Keep compatibility with WAV container bytes too.
            is_wav = len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"
            if is_wav:
                import io
                import soundfile as sf

                audio, sr = sf.read(io.BytesIO(data), dtype="float32")
                if sr != 16000:
                    import librosa

                    audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
                return audio

            # Raw PCM16LE 16k mono.
            if len(data) % 2 != 0:
                data = data[: len(data) - 1]
            audio_i16 = np.frombuffer(data, dtype=np.int16)
            return audio_i16.astype(np.float32) / 32768.0

        if isinstance(audio_input, (str, Path)):
            import soundfile as sf
            audio, sr = sf.read(str(audio_input), dtype='float32')
            if sr != 16000:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
            return audio

        raise ValueError(f"Unsupported audio input type: {type(audio_input)}")

    @property
    def supports_streaming(self) -> bool:
        return False

    @property
    def supports_hotwords(self) -> bool:
        return True

    @property
    def supports_speaker(self) -> bool:
        return False

    def unload(self) -> None:
        """卸载模型"""
        if self._ctx:
            llama_lib.llama_free(self._ctx)
            self._ctx = None
        if self._model:
            llama_lib.llama_model_free(self._model)
            llama_lib.llama_backend_free()
            self._model = None
        self._loaded = False
        logger.info("GGUF backend unloaded")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "GGUFBackend",
            "type": "gguf",
            "supports_streaming": self.supports_streaming,
            "supports_hotwords": self.supports_hotwords,
            "supports_speaker": self.supports_speaker,
            "loaded": self._loaded,
        }
