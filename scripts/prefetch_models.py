#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


SUPPORTED_BACKENDS = ("pytorch", "onnx", "sensevoice", "whisper")
DEFAULT_BACKENDS = ("pytorch", "sensevoice", "whisper")


def _repo_root() -> Path:
    # scripts/prefetch_models.py lives under <repo>/scripts/.
    return Path(__file__).resolve().parents[1]


def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _format_seconds(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


def _load_speaker_artifacts_if_possible(backend) -> bool:
    # Best-effort: PyTorchBackend exposes a property `asr_model_with_spk` that
    # triggers the download/load path without doing a full inference pass.
    try:
        if hasattr(backend, "asr_model_with_spk"):
            _ = backend.asr_model_with_spk  # noqa: F841
            return True
    except Exception:
        return False
    return False


def _prefetch_one(
    backend_type: str,
    *,
    duration_s: float,
    device: Optional[str],
    with_speaker: bool,
) -> Tuple[bool, str]:
    from src.config import settings
    from src.models.model_manager import model_manager
    from src.core.engine import transcription_engine

    if device:
        settings.device = device

    model_manager.switch_backend(backend_type)

    # Warmup triggers download + model load for local backends.
    transcription_engine.warmup(duration=float(duration_s))

    if with_speaker and backend_type == "pytorch":
        backend = model_manager.backend
        if _load_speaker_artifacts_if_possible(backend):
            return True, "ok (speaker preloaded)"
        # Fallback: speaker artifacts may still load on first with_speaker request.
        return True, "ok (speaker preload skipped)"

    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prefetch (download + warmup) Xiyu local ASR backends.\n"
            "This helps avoid slow first requests when running locally."
        )
    )
    parser.add_argument(
        "--backends",
        "-b",
        nargs="+",
        default=list(DEFAULT_BACKENDS),
        help=(
            "Backends to prefetch. Supported: "
            + ", ".join(SUPPORTED_BACKENDS)
            + ". Use 'all' to prefetch all supported local backends."
        ),
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=float,
        default=1.0,
        help="Warmup duration in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default=None,
        help="Override DEVICE for warmup (default: use .env / environment).",
    )
    parser.add_argument(
        "--with-speaker",
        action="store_true",
        help="Also try to preload PyTorch speaker artifacts (if supported).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on the first error (default: best-effort).",
    )

    args = parser.parse_args()

    repo_root = _repo_root()
    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    raw_backends = [str(b).strip().lower() for b in (args.backends or []) if str(b).strip()]
    if not raw_backends:
        print("No backends selected.", file=sys.stderr)
        return 2

    if "all" in raw_backends:
        backends = list(SUPPORTED_BACKENDS)
    else:
        backends = _dedupe_keep_order(raw_backends)

    unknown = [b for b in backends if b not in SUPPORTED_BACKENDS]
    if unknown:
        print(
            "Unsupported backends: "
            + ", ".join(unknown)
            + f". Supported: {', '.join(SUPPORTED_BACKENDS)} (or 'all').",
            file=sys.stderr,
        )
        return 2

    duration_s = float(args.duration)
    if duration_s <= 0:
        print("--duration must be > 0.", file=sys.stderr)
        return 2

    print("======================================")
    print("Xiyu Local Model Prefetch")
    print("======================================")
    print(f"Repo: {repo_root}")
    print(f"Backends: {', '.join(backends)}")
    if args.device:
        print(f"Device override: {args.device}")
    if args.with_speaker:
        print("Speaker preload: enabled (PyTorch only, best-effort)")
    print("")
    print("Notes:")
    print("- This only prefetches *local* backends (pytorch/onnx/sensevoice/whisper).")
    print("- Remote backends (qwen3/vibevoice/router) download weights in their own vLLM servers.")
    print("- Caches are typically under ~/.cache/modelscope and ~/.cache/huggingface (varies by backend).")
    print("")

    rows: List[Tuple[str, bool, float, str]] = []
    all_ok = True

    for backend_type in backends:
        t0 = time.time()
        print(f"==> Prefetching {backend_type} ...")
        try:
            ok, msg = _prefetch_one(
                backend_type,
                duration_s=duration_s,
                device=args.device,
                with_speaker=bool(args.with_speaker),
            )
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130
        except Exception as e:
            ok = False
            msg = f"error: {e}"
            all_ok = False
            if args.strict:
                elapsed = time.time() - t0
                rows.append((backend_type, False, elapsed, msg))
                break

        elapsed = time.time() - t0
        rows.append((backend_type, ok, elapsed, msg))
        print(f"    {msg} ({_format_seconds(elapsed)})")

    print("")
    print("Summary:")
    for backend_type, ok, elapsed, msg in rows:
        status = "OK" if ok else "FAIL"
        print(f"- {backend_type:<10} {status:<4} {_format_seconds(elapsed):>7}  {msg}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
