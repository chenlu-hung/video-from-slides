# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Claude Code plugin (`lecture-notes`) that converts PDF/TeX lecture slides into narrated lecture videos. The workflow is:

1. `/lecture-notes <slides.pdf>` — generates an outline, then batch-produces SRT narration scripts via parallel agents
2. `/video-from-slides <slides-directory>` — auto-runs `f5-tts-mlx` (Python, via uv) to synthesize per-slide narration (using `voice/ref.wav` + `voice/ref.txt` for voice cloning) when `audio/` is missing or incomplete, then generates per-slide videos (Ken Burns effect + audio) and merges them. Users may still pre-populate `audio/slide_XX.mp3` to skip TTS.

## Architecture

The project is structured as a **Claude Code plugin** (manifest at `lecture-notes/.claude-plugin/plugin.json`):

- **Skills** (`lecture-notes/skills/`): Two user-invocable skills orchestrate the pipeline
  - `lecture-notes` — 3-phase workflow: outline → batch SRT generation → review
  - `video-from-slides` — 4-phase workflow: setup/PDF→PNG → TTS synthesis (if audio missing) → batch video composition → merge
- **Agents** (`lecture-notes/agents/`): Specialized sub-agents spawned by skills
  - `script-generator` (Sonnet, cyan) — writes SRT narration for a batch of 1–5 slides
  - `script-reviewer` (Sonnet, yellow) — validates SRT format, timing, and content coverage
  - `tts-synthesizer` (Sonnet, magenta) — runs `f5-tts-mlx` (Python via uv) per slide, matching SRT target duration
  - `video-composer` (Sonnet, green) — ffmpeg Ken Burns video + audio mux per slide batch

## Build Commands

```bash
# install.sh handles everything: ffmpeg, uv + f5-tts-mlx, and plugin registration.
# Manual prerequisites if running pieces by hand:
brew install ffmpeg uv               # ffmpeg for video, uv for Python TTS env
# uv project with f5-tts-mlx lives at ~/.local/share/lecture-notes/tts-py/
```

## Key Conventions

- SRT files are per-slide (`slide_XX.srt`), zero-padded, each starting from `00:00:00,000`
- Subtitle blocks: max 2 lines, ~20 CJK chars or ~42 Latin chars per line, 3–5 seconds each
- Speaking rate: Chinese ~250 chars/min, English ~150 words/min
- All skills require user confirmation before proceeding to their generation phase
- Agents run in parallel batches; no dependencies between batches — **except** `tts-synthesizer`, which runs as a single agent processing slides sequentially because MLX uses unified memory
- Audio files (`audio/slide_XX.mp3`) are produced by `tts-synthesizer` when missing, using `voice/ref.wav` (24kHz mono, 5–10s) + `voice/ref.txt` as the voice-cloning reference. Pre-existing MP3s are left untouched.
- The f5-tts MLX checkpoint (~1.5 GB) downloads to `~/.cache/huggingface/` on first use; expect a one-time stall before slide 1.
