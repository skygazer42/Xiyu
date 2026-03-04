from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from scripts.eval.metrics import cer, ngram_repeat_ratio, wer

__all__ = ["get_reference_for_audio", "load_reference_map"]


@dataclass(frozen=True)
class Endpoint:
    label: str
    url: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_reference_map(path: Path) -> Dict[str, str]:
    """Load reference transcripts mapping from TSV or JSON.

    TSV format (recommended for quick edits):
        filename<TAB>reference text

    JSON format:
        {"filename.wav": "reference text", ...}
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    if p.suffix.lower() == ".json":
        obj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(obj, dict):
            raise ValueError("ref.json must be a JSON object mapping filename -> text")
        out: Dict[str, str] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                continue
            if v is None:
                continue
            out[k.strip()] = str(v)
        return out

    # Default: TSV-ish text file
    out: Dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Prefer tab separator; fall back to first whitespace split.
        if "\t" in line:
            key, value = line.split("\t", 1)
        else:
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            key, value = parts[0], parts[1]

        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        out[key] = value

    return out


def get_reference_for_audio(ref_map: Dict[str, str], audio_path: Path) -> Optional[str]:
    """Lookup reference transcript by basename first, then full/relative keys."""
    p = Path(audio_path)
    key_candidates = [
        p.name,
        str(p),
        str(p.as_posix()),
    ]

    try:
        rel = p.resolve().relative_to(_repo_root().resolve())
        key_candidates.append(str(rel))
        key_candidates.append(rel.as_posix())
    except Exception:
        pass

    for k in key_candidates:
        if k in ref_map:
            return ref_map[k]
    return None


def _load_asr_options(arg: Optional[str]) -> Optional[str]:
    if arg is None:
        return None
    s = str(arg).strip()
    if not s:
        return None
    if s.startswith("@"):
        p = Path(s[1:])
        return p.read_text(encoding="utf-8", errors="ignore").strip()

    # Validate JSON early so we don't send junk to the server.
    json.loads(s)
    return s


def _build_endpoints(host: str, ports: Optional[List[int]], urls: Optional[List[str]]) -> List[Endpoint]:
    if ports and urls:
        raise ValueError("Use only one of --ports or --urls")
    endpoints: List[Endpoint] = []
    if ports:
        for p in ports:
            endpoints.append(Endpoint(label=str(p), url=f"http://{host}:{p}/api/v1/transcribe"))
    elif urls:
        for u in urls:
            endpoints.append(Endpoint(label=u.replace("http://", "").replace("https://", ""), url=u))
    else:
        raise ValueError("Provide --ports or --urls")
    return endpoints


def _iter_audio_files(path: Path) -> List[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    if not p.is_dir():
        raise FileNotFoundError(str(p))

    exts = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}
    files = [f for f in sorted(p.iterdir()) if f.is_file() and f.suffix.lower() in exts]
    return files


def _choose_text_for_scoring(result: Dict[str, Any], scoring_field: str) -> Tuple[str, str]:
    if scoring_field == "text_accu":
        return "text_accu", str(result.get("text_accu") or "")
    if scoring_field == "text":
        return "text", str(result.get("text") or "")

    # auto
    text_accu = result.get("text_accu")
    if isinstance(text_accu, str) and text_accu.strip():
        return "text_accu", text_accu
    return "text", str(result.get("text") or "")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate accuracy across Xiyu endpoints for one file or a directory (CER/WER/duplication).",
    )
    parser.add_argument("--audio", required=True, help="Audio file or directory path.")
    parser.add_argument("--host", type=str, default="localhost", help="Host for --ports (default: localhost).")
    parser.add_argument("--ports", type=int, nargs="*", default=None, help="Ports to compare (e.g. 8101 8102).")
    parser.add_argument("--urls", type=str, nargs="*", default=None, help="Full URLs to compare.")
    parser.add_argument("--ref", type=str, default=None, help="Reference map file (TSV or JSON).")
    parser.add_argument("--with-speaker", action="store_true", help="Send with_speaker=true.")
    parser.add_argument("--apply-hotword", action="store_true", help="Send apply_hotword=true (default).")
    parser.add_argument("--no-apply-hotword", action="store_true", help="Send apply_hotword=false.")
    parser.add_argument("--apply-llm", action="store_true", help="Send apply_llm=true.")
    parser.add_argument("--llm-role", type=str, default="default", help="LLM role (default/meeting/corrector/...).")
    parser.add_argument("--asr-options", type=str, default=None, help="ASR options JSON or @file.")
    parser.add_argument("--timeout", type=float, default=600.0, help="Request timeout seconds (default: 600).")
    parser.add_argument(
        "--out",
        type=str,
        default=str(_repo_root() / "data" / "outputs" / "eval_accuracy"),
        help="Output directory (default: data/outputs/eval_accuracy).",
    )
    parser.add_argument(
        "--scoring-field",
        type=str,
        default="auto",
        choices=["auto", "text", "text_accu"],
        help="Which field to score against reference (default: auto).",
    )

    # Scoring options
    parser.add_argument("--lowercase", action="store_true", help="Lowercase ref/hyp before scoring.")
    parser.add_argument("--remove-punc", action="store_true", help="Remove punctuation before scoring.")
    parser.add_argument("--cer-remove-whitespace", action="store_true", help="Remove whitespace for CER.")

    args = parser.parse_args(list(argv))

    try:
        endpoints = _build_endpoints(args.host, args.ports, args.urls)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    audio_files = _iter_audio_files(Path(args.audio))
    if not audio_files:
        print("No audio files found.", file=sys.stderr)
        return 2

    ref_map: Dict[str, str] = {}
    if args.ref:
        ref_map = load_reference_map(Path(args.ref))

    try:
        asr_options_str = _load_asr_options(args.asr_options)
    except Exception as e:
        print(f"--asr-options error: {e}", file=sys.stderr)
        return 2

    try:
        import requests  # type: ignore[import-not-found]
    except Exception as e:
        print("Missing dependency: requests. Install via `pip install requests`.", file=sys.stderr)
        print(f"Import error: {e}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    apply_hotword = True
    if args.no_apply_hotword:
        apply_hotword = False
    if args.apply_hotword:
        apply_hotword = True

    rows: List[Dict[str, Any]] = []

    for audio_path in audio_files:
        audio_bytes = audio_path.read_bytes()
        audio_out_dir = out_dir / audio_path.stem
        audio_out_dir.mkdir(parents=True, exist_ok=True)

        ref_text = get_reference_for_audio(ref_map, audio_path) if ref_map else None

        for ep in endpoints:
            files = {"file": (audio_path.name, audio_bytes)}
            data: Dict[str, Any] = {
                "with_speaker": "true" if args.with_speaker else "false",
                "apply_hotword": "true" if apply_hotword else "false",
                "apply_llm": "true" if args.apply_llm else "false",
                "llm_role": str(args.llm_role),
            }
            if asr_options_str is not None:
                data["asr_options"] = asr_options_str

            started = time.time()
            try:
                resp = requests.post(ep.url, files=files, data=data, timeout=float(args.timeout))
                latency_s = time.time() - started
            except Exception as e:
                rows.append(
                    {
                        "audio": audio_path.name,
                        "endpoint": ep.label,
                        "url": ep.url,
                        "ok": False,
                        "error": str(e),
                        "latency_s": latency_s,
                    }
                )
                continue

            raw_body = resp.text
            try:
                payload = resp.json()
            except Exception:
                payload = {"_raw": raw_body}

            out_path = audio_out_dir / f"{ep.label}.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            row: Dict[str, Any] = {
                "audio": audio_path.name,
                "endpoint": ep.label,
                "url": ep.url,
                "ok": bool(resp.ok),
                "status_code": resp.status_code,
                "latency_s": latency_s,
                "saved_to": str(out_path),
            }

            if resp.ok and isinstance(payload, dict):
                field, hyp_text = _choose_text_for_scoring(payload, args.scoring_field)
                row["scoring_field"] = field
                row["hyp_len"] = len(hyp_text)
                row["dup_ratio_4gram"] = ngram_repeat_ratio(hyp_text, n=4)

                if ref_text is not None:
                    row["cer"] = cer(
                        ref_text,
                        hyp_text,
                        lowercase=bool(args.lowercase),
                        remove_punc=bool(args.remove_punc),
                        remove_whitespace=bool(args.cer_remove_whitespace),
                    )
                    row["wer"] = wer(
                        ref_text,
                        hyp_text,
                        lowercase=bool(args.lowercase),
                        remove_punc=bool(args.remove_punc),
                    )

            rows.append(row)

    # Persist one merged summary.
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print a minimal summary.
    print("\nEval summary")
    print("=" * 80)
    print(f"Outputs: {out_dir}")
    if args.ref:
        print(f"Ref: {args.ref}")
    print("-" * 80)
    for r in rows:
        if r.get("ok"):
            cer_s = f" CER={r['cer']:.4f}" if r.get("cer") is not None else ""
            wer_s = f" WER={r['wer']:.4f}" if r.get("wer") is not None else ""
            dup_s = f" dup4={r['dup_ratio_4gram']:.3f}" if r.get("dup_ratio_4gram") is not None else ""
            print(
                f"{r['audio']:<24} {r['endpoint']:<12} {r['status_code']:>3} {r['latency_s']:>7.2f}s"
                f"{cer_s}{wer_s}{dup_s}"
            )
        else:
            print(f"{r['audio']:<24} {r['endpoint']:<12} ERR {r.get('error')}")
    print("-" * 80)
    print(f"Summary written: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
