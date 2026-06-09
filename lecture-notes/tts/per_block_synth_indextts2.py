"""Synthesize an SRT-matched WAV for one slide using the IndexTTS-2 MLX-Swift binary.

Approach (mirrors the reference ``index-tts2`` batch notebook and the
``indextts2-mlx`` project's own usage): synthesize the **whole slide** as a
single natural utterance, then correct its length to the SRT target with a
pitch-preserving tempo stretch (ffmpeg ``atempo``). We do **not** split the
slide into per-block clips and we do **not** trim — trimming was cutting the
last word or two off any block that ran past its SRT slot.

Why whole-slide + atempo instead of per-block placement:
  * IndexTTS-2 already splits long text into sentence segments internally
    (``tokenizer.splitSegments``), synthesizes each, and joins them with
    interval silence + crossfade — so one ``--text`` call produces clean,
    naturally-paced narration for the entire slide with no boundary clipping.
  * Matching the SRT's total duration via ``atempo`` (by default snapping to the
    exact target; widen with ``--duration-tolerance`` to instead keep natural
    takes that are already close) keeps the slide audio aligned to its scripted
    length for downstream sync, without ever truncating speech.

Output contract: a mono WAV at the engine's rate (BigVGAN v2 → 22.05 kHz). Its
duration is tempo-corrected to the SRT target (exactly, by default); the caller
transcodes it to MP3. Downstream video uses ``-shortest`` and rebuilds the merged
SRT from actual durations.

Usage:
    python3 per_block_synth_indextts2.py \
        --srt <slide.srt> --ref-audio <ref.wav> \
        --output <slide.wav> [--seed 42] [--steps 20] [--speed 1.0] \
        [--duration-tolerance 0] [--max-mel-tokens N] \
        [--indextts2-home <dir>]   # or set $INDEXTTS2_HOME
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_HOME = os.environ.get(
    "INDEXTTS2_HOME", "/Users/chenlu-hung/Documents/Projects/indextts2-mlx"
)

# Default: snap to the exact SRT duration (tolerance 0 ⇒ atempo on any non-trivial
# mismatch). Widen with --duration-tolerance to instead keep the untouched, most
# natural-sounding take whenever the natural length is already within ±tol.
DEFAULT_DURATION_TOLERANCE = 0.0
# ffmpeg atempo accepts a single factor in [0.5, 2.0].
ATEMPO_LO, ATEMPO_HI = 0.5, 2.0

_TC_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def parse_srt(path: Path) -> tuple[str, float]:
    """Return (whole-slide text, target_seconds) — all blocks concatenated.

    Text lines from every subtitle block are joined with spaces (preserving
    their punctuation so IndexTTS-2's sentence splitter inserts natural pauses);
    the target is the last block's end timecode.
    """
    text_blocks: list[str] = []
    last_end = 0.0
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        m = _TC_RE.match(s)
        if m:
            eh, em, es, ems = map(int, m.group(5, 6, 7, 8))
            last_end = eh * 3600 + em * 60 + es + ems / 1000
        elif s and not s.isdigit():
            text_blocks.append(s)
    return " ".join(text_blocks), last_end


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Resolve the binary / model / preproc dirs, honoring explicit overrides."""
    home = Path(args.indextts2_home)
    binary = Path(args.indextts2_bin) if args.indextts2_bin else \
        home / ".build/xcode/Build/Products/Debug/indextts2"
    model = Path(args.model) if args.model else home / "models/mlx-indextts2-standard-8bit"
    preproc = Path(args.preproc_dir) if args.preproc_dir else home / "models/preprocessing"
    for label, p in (("binary", binary), ("model dir", model), ("preproc dir", preproc)):
        if not p.exists():
            print(f"[indextts2_synth] missing {label}: {p}", file=sys.stderr)
            print("  Build the indextts2 binary (./build.sh Debug) and convert the "
                  "preprocessing weights, or pass --indextts2-home / --indextts2-bin.",
                  file=sys.stderr)
            sys.exit(2)
    return binary, model, preproc


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]
    )
    return float(out.strip())


def synth_whole(binary: Path, model: Path, preproc: Path, text: str, ref: Path,
                out_wav: Path, seed: int | None, steps: int | None,
                speed: float | None, max_mel_tokens: int | None) -> None:
    """One IndexTTS-2 call for the whole slide (single model load, single utterance)."""
    cmd = [
        str(binary),
        "--model", str(model),
        "--preproc-dir", str(preproc),
        "--ref", str(ref),
        "--text", text,
        "--out", str(out_wav),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if steps is not None:
        cmd += ["--steps", str(steps)]
    if speed is not None:
        cmd += ["--speed", str(speed)]
    if max_mel_tokens is not None:
        cmd += ["--max-mel-tokens", str(max_mel_tokens)]
    subprocess.run(cmd, check=True)  # stream the binary's stderr progress through


def atempo(src: Path, dst: Path, factor: float) -> None:
    """Tempo-stretch src → dst by `factor` (pitch preserved)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-filter:a", f"atempo={factor:.4f}", str(dst)],
        check=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--srt", required=True, type=Path)
    p.add_argument("--ref-audio", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--steps", type=int, default=None, help="diffusion steps (engine default 25)")
    p.add_argument("--speed", type=float, default=None, help="speaking rate multiplier")
    p.add_argument("--max-mel-tokens", type=int, default=None,
                   help="AR mel-token cap (engine default 1500 ≈ 30s). When omitted, "
                        "auto-scaled from the SRT target (~50 tok/s + headroom) so long "
                        "slides are not truncated mid-narration.")
    p.add_argument("--duration-tolerance", type=float, default=DEFAULT_DURATION_TOLERANCE,
                   help="how far (fraction) the natural length may sit from the SRT "
                        "target before a pitch-preserving atempo stretch snaps it back. "
                        f"Default {DEFAULT_DURATION_TOLERANCE:g} matches the SRT duration "
                        "exactly on every slide; e.g. 0.10 keeps natural takes already "
                        "within ±10%%.")
    p.add_argument("--indextts2-home", default=DEFAULT_HOME,
                   help="root of the indextts2-mlx project (or set $INDEXTTS2_HOME)")
    p.add_argument("--indextts2-bin", default=None, help="override path to the indextts2 binary")
    p.add_argument("--model", default=None, help="override path to the model dir")
    p.add_argument("--preproc-dir", default=None, help="override path to the preprocessing dir")
    args = p.parse_args()

    binary, model, preproc = resolve_paths(args)

    text, target = parse_srt(args.srt)
    if not text:
        print(f"[indextts2_synth] no text parsed from {args.srt}", file=sys.stderr)
        return 1

    # The engine generates ~50 mel tokens/s and caps the AR loop at --max-mel-tokens
    # (default 1500 ≈ 30s); a slide longer than that would be cut off mid-sentence.
    # Auto-scale the cap from the SRT target with headroom unless explicitly overridden.
    max_mel_tokens = args.max_mel_tokens
    if max_mel_tokens is None and target > 0:
        max_mel_tokens = max(1500, int(target * 65) + 200)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="indextts2_"))
    raw_wav = tmp_dir / "raw.wav"
    try:
        synth_whole(binary, model, preproc, text, args.ref_audio, raw_wav,
                    args.seed, args.steps, args.speed, max_mel_tokens)
        if not raw_wav.exists():
            print(f"[indextts2_synth] engine produced no WAV at {raw_wav}", file=sys.stderr)
            return 1

        actual = ffprobe_duration(raw_wav)
        ratio = actual / target if target > 0 else 1.0
        tol = max(0.0, args.duration_tolerance)
        band_lo, band_hi = 1.0 - tol, 1.0 + tol
        note = "natural (within band)"
        if target > 0 and not (band_lo <= ratio <= band_hi):
            if ATEMPO_LO <= ratio <= ATEMPO_HI:
                atempo(raw_wav, args.output, ratio)          # new dur = actual/ratio = target
                corrected = ffprobe_duration(args.output)
                note = f"atempo {ratio:.3f} → {corrected/target:.3f}"
                actual = corrected
            else:
                shutil.copyfile(raw_wav, args.output)
                note = f"ratio {ratio:.3f} outside atempo range — left natural"
        else:
            shutil.copyfile(raw_wav, args.output)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"[indextts2_synth] wrote {args.output}  "
          f"(target {target:.2f}s, actual {actual:.2f}s; {note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
