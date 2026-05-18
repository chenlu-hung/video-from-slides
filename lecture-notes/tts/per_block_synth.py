"""Synthesize an SRT-aligned WAV by running f5-tts-mlx per subtitle block.

F5-TTS has a per-call generated-audio cap (~30–40s on M-series). To produce
narration matching an SRT that may run for minutes, we synthesize each
subtitle block independently and assemble them on a silence-padded timeline
keyed off the block's start timecode. The output WAV ends at the SRT's last
end timecode, so downstream Ken Burns video composition stays in sync.

Usage:
    python per_block_synth.py \
        --srt <slide.srt> --ref-audio <ref.wav> --ref-text "..." \
        --output <slide.wav> [--seed 42] [--model lucasnewman/f5-tts-mlx]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from preprocess_text import normalize as preprocess_text

SAMPLE_RATE = 24_000


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    """Return [(start_s, end_s, text)] in file order."""
    raw = path.read_text(encoding="utf-8")
    block_re = re.compile(
        r"\d+\s*\n"
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n"
        r"(.*?)(?:\n\s*\n|\Z)",
        re.DOTALL,
    )
    blocks: list[tuple[float, float, str]] = []
    for m in block_re.finditer(raw):
        sh, sm, ss, sms, eh, em, es, ems, text = m.groups()
        start = int(sh) * 3600 + int(sm) * 60 + int(ss) + int(sms) / 1000
        end = int(eh) * 3600 + int(em) * 60 + int(es) + int(ems) / 1000
        clean = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
        if clean:
            blocks.append((start, end, preprocess_text(clean)))
    return blocks


def ensure_24k_mono(ref_path: Path) -> Path:
    """Return a 24kHz mono version of ref_path. Re-encode via ffmpeg if needed."""
    info = sf.info(str(ref_path))
    if info.samplerate == SAMPLE_RATE and info.channels == 1:
        return ref_path
    out = Path(tempfile.mkstemp(suffix="_ref24k.wav")[1])
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ref_path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), str(out)],
        check=True,
    )
    return out


def synth_block(text: str, target_dur: float, ref_path: Path, ref_text: str,
                ref_dur: float, model: str, seed: int | None, out_path: Path) -> None:
    cmd = [
        sys.executable, "-m", "f5_tts_mlx.generate",
        "--text", text,
        "--ref-audio", str(ref_path),
        "--ref-text", ref_text,
        "--duration", f"{ref_dur + target_dur:.3f}",
        "--model", model,
        "--output", str(out_path),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    subprocess.run(cmd, check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--srt", required=True, type=Path)
    p.add_argument("--ref-audio", required=True, type=Path)
    p.add_argument("--ref-text", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--model", default="lucasnewman/f5-tts-mlx")
    args = p.parse_args()

    blocks = parse_srt(args.srt)
    if not blocks:
        print(f"[per_block_synth] no SRT blocks parsed from {args.srt}", file=sys.stderr)
        return 1

    ref_path = ensure_24k_mono(args.ref_audio)
    ref_dur = sf.info(str(ref_path)).duration
    total_dur = blocks[-1][1]

    timeline = np.zeros(int(total_dur * SAMPLE_RATE) + SAMPLE_RATE, dtype=np.float32)

    tmp_dir = Path(tempfile.mkdtemp(prefix="f5_blocks_"))
    try:
        for idx, (start, end, text) in enumerate(blocks):
            block_dur = end - start
            block_wav = tmp_dir / f"block_{idx:03d}.wav"
            print(f"[per_block_synth] block {idx + 1}/{len(blocks)}  "
                  f"t={start:6.2f}-{end:6.2f}  ({block_dur:.2f}s)  text={text[:40]!r}")
            seed = (args.seed or 0) + idx
            synth_block(text, block_dur, ref_path, args.ref_text, ref_dur,
                        args.model, seed, block_wav)

            audio, sr = sf.read(str(block_wav), dtype="float32")
            if sr != SAMPLE_RATE:
                print(f"[per_block_synth]   warning: block sr={sr}, expected {SAMPLE_RATE}",
                      file=sys.stderr)
            expected = int(block_dur * SAMPLE_RATE)
            if len(audio) > expected:
                audio = audio[:expected]   # trim overrun so blocks don't overlap
            pos = int(start * SAMPLE_RATE)
            timeline[pos:pos + len(audio)] = audio
    finally:
        for f in tmp_dir.glob("block_*.wav"):
            f.unlink()
        tmp_dir.rmdir()

    timeline = timeline[: int(total_dur * SAMPLE_RATE)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.output), timeline, SAMPLE_RATE)
    print(f"[per_block_synth] wrote {args.output} ({total_dur:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
