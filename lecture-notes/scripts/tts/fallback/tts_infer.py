#!/usr/bin/env python3
"""
TTS Inference fallback using mlx-audio (Qwen3-TTS 0.6B).

Usage:
    python tts_infer.py --srt <path> --output <path> [--voice-ref <path>]

Requirements:
    pip install mlx-audio

The model is auto-downloaded on first run (~1.2 GB).
ffmpeg must be available in PATH for MP3 encoding (brew install ffmpeg).
"""

import argparse
import re
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="TTS synthesis from SRT using mlx-audio")
    p.add_argument("--srt", required=True, help="Path to input SRT file")
    p.add_argument("--output", required=True, help="Path to output WAV file")
    p.add_argument("--voice-ref", default=None, help="Path to voice reference WAV (for cloning)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------

_TIMECODE_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")

def timecode_to_ms(tc: str) -> int:
    m = _TIMECODE_RE.match(tc.strip())
    if not m:
        raise ValueError(f"Invalid timecode: {tc!r}")
    h, mi, s, ms = (int(g) for g in m.groups())
    return ((h * 3600 + mi * 60 + s) * 1000) + ms

def parse_srt(path: str) -> list[dict]:
    """Return list of {index, start_ms, end_ms, text} dicts."""
    text = Path(path).read_text(encoding="utf-8")
    segments = []
    for block in re.split(r"\n\n+", text.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            continue
        arrow_parts = lines[1].split("-->")
        if len(arrow_parts) != 2:
            continue
        try:
            start_ms = timecode_to_ms(arrow_parts[0])
            end_ms = timecode_to_ms(arrow_parts[1])
        except ValueError:
            continue
        body = "\n".join(lines[2:])
        segments.append({"index": index, "start_ms": start_ms, "end_ms": end_ms, "text": body})
    return sorted(segments, key=lambda s: s["start_ms"])


# ---------------------------------------------------------------------------
# WAV utilities
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000  # Qwen3-TTS native output


def float32_to_int16(samples) -> bytes:
    """Convert a list/array of float32 samples [-1, 1] to packed int16 bytes."""
    out = bytearray()
    for s in samples:
        clamped = max(-1.0, min(1.0, float(s)))
        i16 = int(clamped * 32767)
        out += struct.pack("<h", i16)
    return bytes(out)


def silence_samples(duration_ms: int, sample_rate: int = SAMPLE_RATE) -> list:
    count = max(0, duration_ms * sample_rate // 1000)
    return [0.0] * count


def write_wav(samples: list, path: str, sample_rate: int = SAMPLE_RATE):
    pcm = float32_to_int16(samples)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def write_mp3(samples: list, path: str, sample_rate: int = SAMPLE_RATE):
    """Write samples as MP3 via ffmpeg (must be in PATH)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        write_wav(samples, tmp_path, sample_rate)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path,
             "-codec:a", "libmp3lame", "-q:a", "2", path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# mlx-audio / Qwen3-TTS synthesis
# ---------------------------------------------------------------------------

def load_model():
    try:
        from mlx_audio.tts.models.qwen3 import Qwen3TTS
        from mlx_audio.tts.utils import load_model as _load
    except ImportError:
        print(
            "Error: mlx-audio is not installed. Run:\n"
            "  pip install mlx-audio",
            file=sys.stderr,
        )
        sys.exit(3)

    model, tokenizer = _load("Qwen/Qwen3-TTS-0.6B")
    return model, tokenizer


def extract_speaker_embedding(model, voice_ref_path: str):
    """
    Attempt to extract speaker embedding for voice cloning.
    mlx-audio's Qwen3-TTS may support this via encode_prompt_audio.
    Falls back to None (default voice) if unsupported.
    """
    try:
        import mlx.core as mx
        # Qwen3-TTS voice cloning: provide reference audio as prompt
        # The API varies by mlx-audio version; attempt a best-effort approach.
        import soundfile as sf
        audio, sr = sf.read(voice_ref_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return {"prompt_audio": audio, "prompt_audio_sr": sr}
    except Exception as e:
        print(f"Warning: could not load voice reference '{voice_ref_path}': {e}. Using default voice.", file=sys.stderr)
        return None


def synthesize_segment(model, tokenizer, text: str, speaker_kwargs: dict | None) -> list:
    """Synthesize a single text segment. Returns float32 samples."""
    import mlx.core as mx

    kwargs = dict(speaker_kwargs) if speaker_kwargs else {}
    # generate() returns (samples, sample_rate) or just samples depending on version
    result = model.generate(text, tokenizer=tokenizer, **kwargs)
    if isinstance(result, tuple):
        samples, _ = result
    else:
        samples = result

    # Convert MLX array to Python list of floats
    if hasattr(samples, "tolist"):
        return samples.tolist()
    return list(samples)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Parse SRT
    try:
        segments = parse_srt(args.srt)
    except Exception as e:
        print(f"Error reading SRT '{args.srt}': {e}", file=sys.stderr)
        sys.exit(2)

    if not segments:
        print(f"No valid SRT segments found in '{args.srt}'", file=sys.stderr)
        sys.exit(2)

    # Load model
    print(f"Loading Qwen3-TTS model (auto-downloads on first run)...", file=sys.stderr)
    model, tokenizer = load_model()

    # Speaker embedding (voice cloning)
    speaker_kwargs = None
    if args.voice_ref:
        speaker_kwargs = extract_speaker_embedding(model, args.voice_ref)

    # Synthesize all segments with silence gaps
    all_samples: list = []
    cursor_ms = 0

    for seg in segments:
        gap_ms = seg["start_ms"] - cursor_ms
        if gap_ms > 0:
            all_samples.extend(silence_samples(gap_ms))

        print(f"  Synthesizing segment {seg['index']}: {seg['text'][:60]!r}...", file=sys.stderr)
        try:
            audio = synthesize_segment(model, tokenizer, seg["text"], speaker_kwargs)
        except Exception as e:
            print(f"Error synthesizing segment {seg['index']}: {e}", file=sys.stderr)
            sys.exit(4)

        all_samples.extend(audio)
        synthesized_ms = len(audio) * 1000 // SAMPLE_RATE
        cursor_ms = seg["start_ms"] + max(synthesized_ms, seg["end_ms"] - seg["start_ms"])

    # Write MP3
    try:
        write_mp3(all_samples, args.output)
    except Exception as e:
        print(f"Failed to write MP3 to '{args.output}': {e}", file=sys.stderr)
        sys.exit(5)

    print(f"Synthesized {len(segments)} segment(s) → {args.output}")


if __name__ == "__main__":
    main()
