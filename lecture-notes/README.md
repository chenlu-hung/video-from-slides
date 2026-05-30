# lecture-notes

Generate lecture narration scripts from PDF or TeX slides, synthesize the narration with [VoxCPM2](https://huggingface.co/mlx-community/VoxCPM2-8bit) (via [mlx-audio](https://github.com/Blaizzy/mlx-audio), Python run under [uv](https://github.com/astral-sh/uv)), and produce a narrated lecture video with a Ken Burns effect — all on-device.

## Features

- Reads PDF or TeX slides and estimates speaking duration per logical slide
- **Overlay-aware** — groups Beamer-style overlay pages (`\pause`, `\onslide`, …) into one logical slide, so a deck that exports as many incremental pages is narrated once and plays as a continuous step-by-step reveal instead of repeating near-identical pages
- Generates an editable `outline.md` (including the overlay grouping) for review before script generation
- Batch-generates SRT narration scripts using parallel agents (1–5 logical slides per batch)
- Validates output for content coverage, SRT format, and timing accuracy
- Supports both Chinese and English slides (and mixed zh/en in the same slide)
- Synthesizes narration with VoxCPM2 (mlx-audio, Apple Silicon) — clones a project-supplied reference voice; since VoxCPM2 has no duration control, narration plays at a natural pace and a corrected per-slide SRT (`srt-synced/`) is emitted so subtitles stay in sync
- Generates Ken Burns effect lecture videos with audio (zoom runs continuously across each overlay build-up); **auto-detects source aspect ratio** (4:3, 16:9, …) so slides are never stretched or wrongly pillarboxed

## Installation

```bash
git clone https://github.com/chenlu-hung/video-from-slides.git
cd video-from-slides
./install.sh
```

The install script checks/installs prerequisites (Homebrew, ffmpeg, uv), creates a uv project at `~/.local/share/lecture-notes/tts-py/` with `mlx-audio` (for VoxCPM2) installed, and registers the plugin with Claude Code.

### Prerequisites

- macOS 14+ on Apple Silicon (MLX requirement)
- Claude Code CLI
- ffmpeg (`brew install ffmpeg`)
- uv (`brew install uv`)

## Usage

### Step 1: Generate SRT scripts from slides

```
/lecture-notes path/to/slides.pdf
```

### Step 2: Provide a reference voice

Inside the slides directory create:

- `voice/ref.wav` — mono WAV, 5–10 seconds of the target speaker (any sample rate; VoxCPM2 resamples internally)
- `voice/ref.txt` — the transcript of `ref.wav` (exact text being spoken)

Convert any source clip to mono with ffmpeg if needed:

```bash
ffmpeg -i source.m4a -ac 1 voice/ref.wav
```

To skip TTS for any subset of slides, drop pre-made `audio/slide_XX.mp3` files in place — the skill leaves existing MP3s alone and only synthesizes the missing ones.

### Step 3: Generate lecture video

```
/video-from-slides path/to/slides-directory
```

The first run downloads the VoxCPM2-8bit MLX checkpoint (~3.2 GB); subsequent runs are cached. Choose to merge all slides into one video or split by sections.

## Workflow

### Script Generation (`/lecture-notes`)

1. **Outline** — Reads slides, groups overlay pages into logical slides, estimates duration, produces `outline.md` (with an `OVERLAY-GROUPS` table you can edit) for your review
2. **Generate** — After you confirm the outline, spawns agents to generate `srt/slide_XX.srt` files (one per narrated page; for overlay build-ups each step is a short delta that flows on from the last)
3. **Review** — Validates all SRT files and reports any issues

### Video Generation (`/video-from-slides`)

1. **Setup** — Checks SRT, reference voice, and TTS binary; converts PDF to PNGs; parses the overlay groups + sections from `outline.md`; auto-detects aspect ratio
2. **TTS** — *(skipped if `audio/` is already complete)* Spawns a `tts-synthesizer` agent that runs VoxCPM2 (mlx-audio, via uv) sequentially, one page at a time, at a natural pace, writing a corrected per-page SRT to `srt-synced/`
3. **Compose** — Creates Ken Burns videos with audio per narrated page; the zoom runs continuously across each overlay build-up and resets at the next logical slide
4. **Merge** — User chooses merge strategy (all / by section / both); produces the final video plus an external `final.srt`

## Output Structure

```
your-slides-directory/
├── slides.pdf
├── outline.md
├── voice/
│   ├── ref.wav            (your reference voice, mono)
│   └── ref.txt            (transcript of ref.wav)
├── srt/                   (authored scripts, one per narrated page; overlay-merged pages skipped)
│   ├── slide_01.srt
│   ├── slide_02.srt
│   └── ...
├── srt-synced/            (corrected SRTs whose cues match the synthesized audio)
│   ├── slide_01.srt
│   └── ...
├── audio/                 (synthesized by VoxCPM2, or supplied by you)
│   ├── slide_01.mp3
│   ├── slide_02.mp3
│   └── ...
├── images/                (one PNG per PDF page, including overlay sub-frames)
│   ├── slide-01.png
│   ├── slide-02.png
│   └── ...
└── video/
    ├── slide_01.mp4       (one segment per narrated page; numbering may have gaps)
    ├── slide_02.mp4
    ├── ...
    ├── final_all.mp4      (or section_XX_name.mp4)
    └── final.srt          (external subtitle for the merged video — load in VLC / IINA)
```

> Narration files (`srt/`, `audio/`, `video/`) are keyed by **narrated** PDF page, so when overlay sub-frames are merged the `slide_NN` numbering is intentionally non-contiguous. `images/` always has one PNG per PDF page.
