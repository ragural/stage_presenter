"""Download offline models for Stage Presenter.

Usage:
  python download_models.py              # everything (LLM ~5 GB, Whisper, Kokoro, Piper)
  python download_models.py --only kokoro piper whisper
  python download_models.py --llm OpenVINO/Phi-3.5-mini-instruct-int4-cw-ov
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
import urllib.request
from pathlib import Path

MODELS = Path(__file__).parent / "models"

DEFAULT_LLM = "OpenVINO/Qwen3-8B-int4-cw-ov"
DEFAULT_WHISPER = "OpenVINO/whisper-small.en-int8-ov"

KOKORO_FILES = {  # name -> mirrors (tried in order)
    "kokoro-v1.0.onnx": [
        "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx",
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    ],
    "voices-v1.0.bin": [
        "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin",
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    ],
}

# (locale dir, voice, quality)
PIPER_VOICES = [
    ("en/en_US", "lessac", "medium"),
    ("en/en_US", "ryan", "high"),
    ("en/en_US", "amy", "medium"),
    ("en/en_GB", "alba", "medium"),
    ("en/en_GB", "alan", "medium"),
    ("en/en_GB", "northern_english_male", "medium"),
]
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  ↓ {dest.name}  <- {url.split('/')[2]}")

    def hook(n, bs, total):
        if total > 0:
            sys.stdout.write(f"\r    {min(100, n * bs * 100 // total):3d}%")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, tmp, hook)
    tmp.replace(dest)
    print()


def fetch(urls, dest: Path, retries: int = 3) -> None:
    if isinstance(urls, str):
        urls = [urls]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ {dest.name} (cached)")
        return
    last = None
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                return _fetch(url, dest)
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"\n    ! {e} (attempt {attempt}/{retries})")
                time.sleep(2 * attempt)
    raise RuntimeError(f"failed to download {dest.name}: {last}")


def hf(repo: str, sub: str) -> None:
    from huggingface_hub import snapshot_download

    dest = MODELS / sub / repo.split("/")[-1]
    print(f"• {repo} -> {dest}")
    snapshot_download(repo, local_dir=dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=["llm", "whisper", "kokoro", "piper"])
    ap.add_argument("--llm", default=DEFAULT_LLM)
    ap.add_argument("--whisper", default=DEFAULT_WHISPER)
    a = ap.parse_args()
    want = set(a.only or ["llm", "whisper", "kokoro", "piper"])
    failed: list[str] = []

    def kokoro():
        print("• Kokoro TTS")
        for name, urls in KOKORO_FILES.items():
            fetch(urls, MODELS / "kokoro" / name)

    def piper():
        print("• Piper voices")
        for loc, name, q in PIPER_VOICES:
            stem = f"{loc.split('/')[1]}-{name}-{q}"
            for ext in (".onnx.json", ".onnx"):
                fetch(f"{PIPER_BASE}/{loc}/{name}/{q}/{stem}{ext}", MODELS / "piper" / f"{stem}{ext}")

    steps = {
        "kokoro": kokoro,
        "piper": piper,
        "whisper": lambda: hf(a.whisper, "whisper"),
        "llm": lambda: hf(a.llm, "llm"),
    }
    for name, fn in steps.items():
        if name not in want:
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failed.append(name)

    print("\nModels are in", MODELS)
    if failed:
        print("FAILED:", ", ".join(failed), "- re-run: python download_models.py --only", " ".join(failed))
        sys.exit(1)
    print("All downloads complete.")


if __name__ == "__main__":
    main()
