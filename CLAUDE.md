# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Claude Code plugin (`lecture-notes`) that converts PDF/TeX lecture slides into narrated lecture videos. The workflow is:

1. `/lecture-notes <slides.pdf>` — generates an outline (grouping Beamer-style overlay pages into logical slides), then batch-produces SRT narration scripts via parallel agents
2. `/video-from-slides <slides-directory>` — auto-runs `f5-tts-mlx` (Python, via uv) to synthesize per-page narration (using `voice/ref.wav` + `voice/ref.txt` for voice cloning) when `audio/` is missing or incomplete, then generates per-page video segments (Ken Burns effect + audio) and merges them. Overlay build-ups play as a continuous reveal; source aspect ratio is auto-detected. Users may still pre-populate `audio/slide_XX.mp3` to skip TTS.

## Architecture

The project is structured as a **Claude Code plugin** (manifest at `lecture-notes/.claude-plugin/plugin.json`):

- **Skills** (`lecture-notes/skills/`): Two user-invocable skills orchestrate the pipeline
  - `lecture-notes` — 3-phase workflow: outline → batch SRT generation → review
  - `video-from-slides` — 4-phase workflow: setup/PDF→PNG → TTS synthesis (if audio missing) → batch video composition → merge
- **Agents** (`lecture-notes/agents/`): Specialized sub-agents spawned by skills
  - `script-generator` (Sonnet, cyan) — writes SRT narration for a batch of 1–5 logical slides; for overlay build-ups, writes delta narration (one SRT per narrate page)
  - `script-reviewer` (Sonnet, yellow) — validates SRT format, timing, and content coverage
  - `tts-synthesizer` (Sonnet, magenta) — runs `f5-tts-mlx` (Python via uv) per narrate page, matching SRT target duration
  - `video-composer` (Sonnet, green) — ffmpeg Ken Burns video + audio mux per narrate page (aspect-ratio-aware, per-logical-slide continuous zoom)

## Build Commands

```bash
# install.sh handles everything: ffmpeg, uv + f5-tts-mlx, and plugin registration.
# Manual prerequisites if running pieces by hand:
brew install ffmpeg uv               # ffmpeg for video, uv for Python TTS env
# uv project with f5-tts-mlx lives at ~/.local/share/lecture-notes/tts-py/
```

## Key Conventions

- **Logical slides vs PDF pages (overlay support)**: a Beamer-style overlay is one frame revealed across several consecutive PDF pages. `/lecture-notes` groups them into **logical slides** in `outline.md` via a machine-parsed `OVERLAY-GROUPS` table (between `<!-- OVERLAY-GROUPS:START/END -->`), giving `logical | pages | narrate_pages | title`. `outline.md` is the **single source of truth** for the grouping; both skills parse it. A non-overlay deck = one logical slide per page (every group size 1), and the pipeline behaves exactly as before.
- **Narrate pages vs merged pages**: only `narrate_pages` get a `slide_XX.srt` / `slide_XX.mp3` / `slide_XX.mp4` (XX = PDF page). Pages merged into a prior step (pure highlight/no new content) get **no** files, so `slide_NN` numbering is intentionally non-contiguous — iterate over files that exist, never `seq 1 N`.
- **Overlay narration is delta-style**: the first narrate page of a group introduces the slide; later pages speak only newly-revealed content and flow on, no re-introduction.
- **Video continuity**: within a logical slide the Ken Burns zoom runs continuously across reveal steps (each segment's `zstart/zend` is its slice of a 1.0→1.05 trajectory) and resets to 1.0 at the next logical slide; transitions are hard cuts (concat).
- **Aspect ratio is auto-detected** (pdfinfo/ffprobe) → `inner_w/inner_h/pad_x/pad_y`; never assume 4:3. 16:9 → no bars, 4:3 → 240px pillarbox.
- SRT files are per narrate page (`slide_XX.srt`), zero-padded PDF page number, each starting from `00:00:00,000`
- Subtitle blocks: max 2 lines, ~20 CJK chars or ~42 Latin chars per line, 3–5 seconds each
- Speaking rate: Chinese ~250 chars/min, English ~150 words/min
- All skills require user confirmation before proceeding to their generation phase
- Agents run in parallel batches; no dependencies between batches — **except** `tts-synthesizer`, which runs as a single agent processing slides sequentially because MLX uses unified memory
- Audio files (`audio/slide_XX.mp3`) are produced by `tts-synthesizer` when missing, using `voice/ref.wav` (24kHz mono, 5–10s) + `voice/ref.txt` as the voice-cloning reference. Pre-existing MP3s are left untouched.
- The f5-tts MLX checkpoint (~1.5 GB) downloads to `~/.cache/huggingface/` on first use; expect a one-time stall before slide 1.
