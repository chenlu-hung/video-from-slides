"""Synthesize an SRT-aligned WAV by running VoxCPM2 (mlx-audio) per subtitle block.

This is the VoxCPM2 counterpart to ``per_block_synth.py`` (f5-tts). It keeps the
same SRT-timeline contract — the output WAV ends exactly at the SRT's last end
timecode, so downstream Ken Burns video stays in sync — but differs from the
f5-tts helper in two important ways:

1. **Model is loaded once.** VoxCPM2 is a ~2B-param model; reloading it per block
   via a subprocess (as the f5-tts helper does) would be prohibitively slow. We
   import ``mlx_audio`` in-process, load the model a single time, and reuse it
   across every block of the slide.

2. **No duration control.** Unlike f5-tts (``--duration``), VoxCPM2 generates at
   its own natural pace; we cannot force a block to a target length. Blocks are
   laid down sequentially: each starts no earlier than its SRT start (silence
   fills gaps) and never overlaps the previous one. A block longer than its SRT
   slot is time-compressed with ffmpeg ``atempo`` (pitch preserved, no words
   lost) — but only up to ``--max-tempo`` (default 1.2×). Past that the speech
   would sound rushed, so instead the residual spills over and pushes later
   blocks back, letting the whole slide run a little longer than the SRT rather
   than mangling intelligibility. (``--overrun trim`` restores the old hard-cut.)

   With ``--timing natural`` we skip compression entirely — blocks play at their
   natural pace (SRT start still honoured as a minimum) — and ``--write-srt``
   emits a *corrected SRT* whose cue timecodes match the synthesized audio, so
   subtitles stay perfectly in sync and the overflow problem disappears at the
   source instead of being papered over by time-compression.

3. **Loudness.** The assembled timeline is peak-normalised to ``--peak-dbfs``
   (default −1 dBFS) so every slide lands at a consistent, audible level.

We deliberately do **not** run the f5-tts ``preprocess_text`` lone-letter hack
here: that rewrote isolated English letters to phonetic spellings ("n"→"en") to
stop f5-tts reading them as Pinyin inside Chinese, but VoxCPM2 is multilingual
and reads them correctly — applying it would mangle English ("a"→"ay",
"Let's"→"Let'es"). VoxCPM2 gets the raw SRT text.

Usage:
    python per_block_synth_voxcpm.py \
        --srt <slide.srt> --ref-audio <ref.wav> --ref-text "..." \
        --output <slide.wav> [--seed 42] [--model mlx-community/VoxCPM2-8bit] \
        [--inference-timesteps 10] [--cfg 2.0] \
        [--timing pin|natural] [--write-srt <corrected.srt>] \
        [--max-tempo 1.2] [--peak-dbfs -1.0] [--overrun fit|trim]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import mlx.core as mx
from mlx_audio.tts.utils import load as load_tts_model


def parse_srt(path: Path) -> list[tuple[float, float, str, str]]:
    """Return [(start_s, end_s, tts_text, disp_text)] in file order.

    `tts_text` is the block collapsed to a single line (what we feed VoxCPM2);
    `disp_text` keeps the original line breaks (what a corrected SRT should show).
    """
    raw = path.read_text(encoding="utf-8")
    block_re = re.compile(
        r"\d+\s*\n"
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n"
        r"(.*?)(?:\n\s*\n|\Z)",
        re.DOTALL,
    )
    blocks: list[tuple[float, float, str, str]] = []
    for m in block_re.finditer(raw):
        sh, sm, ss, sms, eh, em, es, ems, text = m.groups()
        start = int(sh) * 3600 + int(sm) * 60 + int(ss) + int(sms) / 1000
        end = int(eh) * 3600 + int(em) * 60 + int(es) + int(ems) / 1000
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        tts = " ".join(lines)
        if tts:
            blocks.append((start, end, tts, "\n".join(lines)))
    return blocks


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    """Write an SRT whose cue timecodes match the synthesized audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = "".join(
        f"{i}\n{_fmt_ts(s)} --> {_fmt_ts(e)}\n{t}\n\n"
        for i, (s, e, t) in enumerate(cues, 1)
    )
    path.write_text(out, encoding="utf-8")


def ensure_mono(ref_path: Path) -> Path:
    """Return a mono version of ref_path. VoxCPM2 resamples internally, so we
    only fold stereo down to mono (sample rate is left untouched)."""
    if sf.info(str(ref_path)).channels == 1:
        return ref_path
    out = Path(tempfile.mkstemp(suffix="_refmono.wav")[1])
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ref_path),
         "-ac", "1", str(out)],
        check=True,
    )
    return out


def fit_to_slot(audio: np.ndarray, sr: int, target_samples: int) -> np.ndarray:
    """Time-compress `audio` to `target_samples` with ffmpeg atempo (pitch
    preserved). atempo handles 0.5–2.0×; for the rare >2× overrun we hard-trim."""
    factor = len(audio) / target_samples
    if factor > 2.0:
        return audio[:target_samples]
    tin = Path(tempfile.mktemp(suffix="_fitin.wav"))
    tout = Path(tempfile.mktemp(suffix="_fitout.wav"))
    try:
        sf.write(str(tin), audio, sr)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tin),
             "-filter:a", f"atempo={factor:.6f}", str(tout)],
            check=True,
        )
        out, _ = sf.read(str(tout), dtype="float32")
    finally:
        for f in (tin, tout):
            if f.exists():
                os.remove(f)
    return out[:target_samples]


def synth_block(model, text: str, ref_audio: str, ref_text: str,
                inference_timesteps: int, cfg_value: float) -> tuple[np.ndarray, int]:
    """Generate one block; return (mono float32 samples, sample_rate)."""
    chunks: list[np.ndarray] = []
    sr = 0
    for result in model.generate(
        text=text,
        ref_audio=ref_audio,
        ref_text=ref_text,
        inference_timesteps=inference_timesteps,
        cfg_value=cfg_value,
    ):
        chunks.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
        sr = int(result.sample_rate)
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    return audio, sr


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--srt", required=True, type=Path)
    p.add_argument("--ref-audio", required=True, type=Path)
    p.add_argument("--ref-text", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--model", default="mlx-community/VoxCPM2-8bit")
    p.add_argument("--inference-timesteps", type=int, default=10)
    p.add_argument("--cfg", type=float, default=2.0)
    p.add_argument("--max-tempo", type=float, default=1.2,
                   help="max atempo speed-up for an over-long block before letting "
                        "it overflow and extend the slide (default 1.2x)")
    p.add_argument("--peak-dbfs", type=float, default=-1.0,
                   help="peak-normalise the assembled timeline to this dBFS (default -1.0)")
    p.add_argument("--overrun", choices=["fit", "trim"], default="fit",
                   help="how to handle a block longer than its SRT slot (pin timing "
                        "only): 'fit' compresses up to --max-tempo then overflows "
                        "(default), 'trim' hard-cuts it to the slot")
    p.add_argument("--timing", choices=["pin", "natural"], default="pin",
                   help="'pin' keeps the SRT timeline (compress/overflow to honour it); "
                        "'natural' never compresses — blocks play at their natural pace "
                        "(SRT start honoured as a minimum) and a corrected SRT follows")
    p.add_argument("--write-srt", type=Path, default=None,
                   help="also write a corrected SRT here, with cue timecodes matching "
                        "the synthesized audio (relative to slide start)")
    args = p.parse_args()

    blocks = parse_srt(args.srt)
    if not blocks:
        print(f"[voxcpm] no SRT blocks parsed from {args.srt}", file=sys.stderr)
        return 1

    # VoxCPM2 resamples the reference internally; we only ensure it is mono.
    ref_audio = str(ensure_mono(args.ref_audio))
    total_dur = blocks[-1][1]

    print(f"[voxcpm] loading model {args.model} ...", file=sys.stderr)
    model = load_tts_model(args.model)

    rendered: list[tuple[float, float, np.ndarray]] = []
    sample_rate = 0
    for idx, (start, end, text, _disp) in enumerate(blocks):
        if args.seed is not None:
            mx.random.seed(args.seed + idx)
        block_dur = end - start
        print(f"[voxcpm] block {idx + 1}/{len(blocks)}  "
              f"t={start:6.2f}-{end:6.2f}  ({block_dur:.2f}s)  text={text[:40]!r}")
        audio, sr = synth_block(model, text, ref_audio, args.ref_text,
                                args.inference_timesteps, args.cfg)
        if sr:
            sample_rate = sr
        rendered.append((start, end, audio))

    if not sample_rate:
        print("[voxcpm] model produced no audio", file=sys.stderr)
        return 1

    # Sequential placement: each block starts no earlier than its SRT start
    # (silence fills gaps, never overlaps the previous block).
    #   pin     — compress an over-long block up to --max-tempo, then let the
    #             residual overflow push later blocks back (slide runs a bit long).
    #   natural — never compress; blocks play at their natural pace and a corrected
    #             SRT (--write-srt) follows the audio so subtitles stay in sync.
    placed: list[tuple[int, np.ndarray]] = []
    cursor = 0.0
    n_compressed = n_overflow = 0
    for start, end, audio in rendered:
        place_start = max(start, cursor)
        slot = end - start
        dur = len(audio) / sample_rate
        if args.timing == "pin" and dur > slot > 0:
            if args.overrun == "trim":
                audio = audio[: int(slot * sample_rate)]
            else:
                factor = dur / slot
                if factor <= args.max_tempo:
                    audio = fit_to_slot(audio, sample_rate, int(slot * sample_rate))
                    n_compressed += 1
                    print(f"[voxcpm]   block t={start:.2f}s +{dur - slot:.2f}s over"
                          f" slot — compressed {factor:.2f}x to fit", file=sys.stderr)
                else:
                    audio = fit_to_slot(audio, sample_rate,
                                        int(dur / args.max_tempo * sample_rate))
                    spill = len(audio) / sample_rate - slot
                    n_overflow += 1
                    print(f"[voxcpm]   block t={start:.2f}s +{dur - slot:.2f}s over"
                          f" slot — capped at {args.max_tempo:.2f}x, +{spill:.2f}s"
                          f" spills (slide extends)", file=sys.stderr)
        placed.append((int(place_start * sample_rate), audio))
        cursor = place_start + len(audio) / sample_rate

    total_out = max(cursor, total_dur)
    timeline = np.zeros(int(total_out * sample_rate) + 1, dtype=np.float32)
    for pos, audio in placed:
        timeline[pos:pos + len(audio)] = audio
    timeline = timeline[: int(total_out * sample_rate)]

    # peak-normalize so every slide lands at a consistent, audible level
    peak = float(np.max(np.abs(timeline))) if timeline.size else 0.0
    if peak > 1e-6:
        timeline = (timeline * (10 ** (args.peak_dbfs / 20.0) / peak)).astype(np.float32)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.output), timeline, sample_rate)

    # corrected SRT: cue i spans [its placed start, next block's placed start],
    # last cue ends at the audio end — so the text stays in sync with the audio.
    if args.write_srt is not None:
        starts = [pos / sample_rate for pos, _ in placed]
        cues = []
        for i, (_, _, _tts, disp) in enumerate(blocks):
            s = starts[i]
            e = starts[i + 1] if i + 1 < len(starts) else total_out
            cues.append((s, max(e, s), disp))
        write_srt(args.write_srt, cues)
        print(f"[voxcpm] wrote corrected SRT {args.write_srt}", file=sys.stderr)

    extra = total_out - total_dur
    tail = (f"  [+{extra:.2f}s over SRT]" if extra > 0.01 else "")
    print(f"[voxcpm] wrote {args.output} ({total_out:.2f}s @ {sample_rate}Hz; "
          f"timing={args.timing}, {n_compressed} fitted, {n_overflow} overflowed){tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
