# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Claude Code plugin (`lecture-notes`) that converts PDF/TeX lecture slides into narrated lecture videos. The workflow is:

1. `/lecture-notes <slides.pdf>` — generates an outline, then batch-produces SRT narration scripts via parallel agents
2. `/tts-synthesis <slides-directory>` — synthesizes speech audio from the SRT files using a local TTS model
3. `/video-from-slides <slides-directory>` — generates per-slide videos (Ken Burns effect + audio) and merges them

## Architecture

The project is structured as a **Claude Code plugin** (manifest at `lecture-notes/.claude-plugin/plugin.json`):

- **Skills** (`lecture-notes/skills/`): Three user-invocable skills orchestrate the pipeline
  - `lecture-notes` — 3-phase workflow: outline → batch SRT generation → review
  - `tts-synthesis` — 3-phase workflow: setup/validation → batch TTS → audio verification
  - `video-from-slides` — 3-phase workflow: setup/PDF→PNG → batch video composition → merge
- **Agents** (`lecture-notes/agents/`): Specialized sub-agents spawned by skills
  - `script-generator` (Sonnet, cyan) — writes SRT narration for a batch of 1–5 slides
  - `script-reviewer` (Sonnet, yellow) — validates SRT format, timing, and content coverage
  - `tts-worker` (Sonnet, purple) — invokes TTSInfer CLI per SRT file, reports success/failure
  - `video-composer` (Sonnet, green) — ffmpeg Ken Burns video + audio mux per slide batch
- **TTS CLI** (`lecture-notes/scripts/tts/`): Swift package (macOS 14+, Apple Silicon)
  - Uses `SpeechSwift` (CosyVoice 3) with CoreML (default) or MLX backend
  - Parses SRT → synthesizes per-segment → assembles with silence gaps → writes MP3
  - Python fallback at `fallback/tts_infer.py` uses `mlx-audio` (Qwen3-TTS 0.6B), requires `ffmpeg`

## Build Commands

```bash
# Build TTS CLI (one-time, required before /tts-synthesis)
cd lecture-notes/scripts/tts && swift build -c release

# Python fallback dependencies
pip install mlx-audio
brew install ffmpeg
```

## Key Conventions

- SRT files are per-slide (`slide_XX.srt`), zero-padded, each starting from `00:00:00,000`
- Subtitle blocks: max 2 lines, ~20 CJK chars or ~42 Latin chars per line, 3–5 seconds each
- Speaking rate: Chinese ~250 chars/min, English ~150 words/min
- All skills require user confirmation before proceeding to their generation phase
- Agents run in parallel batches; no dependencies between batches
- TTS output sample rate is 24000 Hz (CosyVoice 3 / Qwen3-TTS native)
