from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scripts.eval.metrics import cer, ngram_repeat_ratio, wer


@dataclass(frozen=True)
class Endpoint:
    label: str
    url: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_ref_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


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
    try:
        json.loads(s)
    except Exception as e:
        raise ValueError(f"--asr-options is not valid JSON: {e}") from e
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


def _choose_text_for_scoring(result: Dict[str, Any]) -> Tuple[str, str]:
    # Prefer text_accu when present (it is intended for meeting/recall final output).
    text_accu = result.get("text_accu")
    if isinstance(text_accu, str) and text_accu.strip():
        return "text_accu", text_accu
    text = result.get("text")
    return "text", str(text or "")


def _safe_filename(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)[:120] or "endpoint"


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="A/B compare Xiyu ports/URLs for one audio file (optionally compute CER/WER).",
    )
    parser.add_argument("audio", type=str, help="Audio file path (wav/mp3/m4a/...).")
    parser.add_argument("--host", type=str, default="localhost", help="Host for --ports (default: localhost).")
    parser.add_argument("--ports", type=int, nargs="*", default=None, help="Ports to compare (e.g. 8101 8102).")
    parser.add_argument("--urls", type=str, nargs="*", default=None, help="Full URLs to compare.")
    parser.add_argument("--ref", type=str, default=None, help="Reference transcript text file (optional).")
    parser.add_argument("--with-speaker", action="store_true", help="Send with_speaker=true.")
    parser.add_argument(
        "--asr-options",
        type=str,
        default=None,
        help='ASR options JSON string, or @/path/to/options.json (optional).',
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="Request timeout seconds (default: 600).")
    parser.add_argument(
        "--out",
        type=str,
        default=str(_repo_root() / "data" / "outputs" / "eval"),
        help="Output directory for saving responses (default: data/outputs/eval).",
    )

    # Scoring options
    parser.add_argument("--lowercase", action="store_true", help="Lowercase ref/hyp before scoring.")
    parser.add_argument("--remove-punc", action="store_true", help="Remove punctuation before scoring.")
    parser.add_argument("--cer-remove-whitespace", action="store_true", help="Remove whitespace for CER.")

    args = parser.parse_args(list(argv))

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 2

    try:
        endpoints = _build_endpoints(args.host, args.ports, args.urls)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        asr_options_str = _load_asr_options(args.asr_options)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    ref_text = None
    if args.ref:
        ref_text = _load_ref_text(Path(args.ref))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import requests  # type: ignore[import-not-found]
    except Exception as e:
        print("Missing dependency: requests. Install via `pip install requests` (or run inside the docker env).")
        print(f"Import error: {e}", file=sys.stderr)
        return 2

    rows: List[Dict[str, Any]] = []
    for ep in endpoints:
        files = {"file": (audio_path.name, audio_path.read_bytes())}
        data: Dict[str, Any] = {}
        if args.with_speaker:
            data["with_speaker"] = "true"
        if asr_options_str is not None:
            data["asr_options"] = asr_options_str

        start = time.time()
        try:
            resp = requests.post(ep.url, files=files, data=data, timeout=float(args.timeout))
            latency_s = time.time() - start
        except Exception as e:
            latency_s = time.time() - start
            rows.append(
                {
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

        # Save response
        out_path = out_dir / f"{_safe_filename(ep.label)}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        row: Dict[str, Any] = {
            "endpoint": ep.label,
            "url": ep.url,
            "status_code": resp.status_code,
            "ok": bool(resp.ok),
            "latency_s": latency_s,
            "saved_to": str(out_path),
        }

        if resp.ok and isinstance(payload, dict):
            field, hyp_text = _choose_text_for_scoring(payload)
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

    # Print summary
    print("\nA/B compare results")
    print("=" * 80)
    for r in rows:
        ep = r.get("endpoint")
        status = r.get("status_code", "ERR")
        latency = r.get("latency_s", 0.0)
        ok = r.get("ok", False)
        line = f"{ep:>12}  {status!s:>4}  {latency:>7.2f}s  ok={ok}"
        if r.get("cer") is not None:
            line += f"  CER={r['cer']:.4f}  WER={r['wer']:.4f}"
        if r.get("dup_ratio_4gram") is not None:
            line += f"  dup4={r['dup_ratio_4gram']:.3f}"
        if r.get("saved_to"):
            line += f"  saved={r['saved_to']}"
        if r.get("error"):
            line += f"  error={r['error']}"
        print(line)

    # Also save a merged summary.
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 80)
    print(f"Summary written: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
