# lecture-notes

Generate lecture narration scripts from PDF or TeX slides, synthesize the narration with [f5-tts-mlx](https://github.com/lucasnewman/f5-tts-mlx) (Python, run via [uv](https://github.com/astral-sh/uv)), and produce a narrated lecture video with a Ken Burns effect — all on-device.

## Features

- Reads PDF or TeX slides and estimates speaking duration per slide
- Generates an editable `outline.md` for review before script generation
- Batch-generates SRT narration scripts using parallel agents (1–5 slides per batch)
- Validates output for content coverage, SRT format, and timing accuracy
- Supports both Chinese and English slides (and mixed zh/en in the same slide)
- Synthesizes narration with f5-tts-mlx (Python + MLX, Apple Silicon) — clones a project-supplied reference voice and matches each slide's SRT total duration
- Generates Ken Burns effect lecture videos with audio, auto-merges slides and narration

## Installation

```bash
git clone https://github.com/chenlu-hung/video-from-slides.git
cd video-from-slides
./install.sh
```

The install script checks/installs prerequisites (Homebrew, ffmpeg, uv), creates a uv project at `~/.local/share/lecture-notes/tts-py/` with `f5-tts-mlx` installed, and registers the plugin with Claude Code.

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

- `voice/ref.wav` — 24kHz mono WAV, 5–10 seconds of the target speaker
- `voice/ref.txt` — the transcript of `ref.wav` (exact text being spoken)

Convert any source clip with ffmpeg if needed:

```bash
ffmpeg -i source.m4a -ac 1 -ar 24000 voice/ref.wav
```

To skip TTS for any subset of slides, drop pre-made `audio/slide_XX.mp3` files in place — the skill leaves existing MP3s alone and only synthesizes the missing ones.

### Step 3: Generate lecture video

```
/video-from-slides path/to/slides-directory
```

The first run downloads the f5-tts MLX checkpoint (~1.5 GB); subsequent runs are cached. Choose to merge all slides into one video or split by sections.

## Workflow

### Script Generation (`/lecture-notes`)

1. **Outline** — Reads slides, estimates duration, produces `outline.md` for your review
2. **Generate** — After you confirm the outline, spawns agents to generate `srt/slide_XX.srt` files
3. **Review** — Validates all SRT files and reports any issues

### Video Generation (`/video-from-slides`)

1. **Setup** — Checks SRT, reference voice, and TTS binary; converts PDF to PNGs; parses sections
2. **TTS** — *(skipped if `audio/` is already complete)* Spawns a `tts-synthesizer` agent that runs `python -m f5_tts_mlx.generate` (via uv) sequentially, one slide at a time, matching each SRT's target duration
3. **Compose** — Spawns parallel agents to create Ken Burns videos with audio per slide
4. **Merge** — User chooses merge strategy (all / by section / both)

## Output Structure

```
your-slides-directory/
├── slides.pdf
├── outline.md
├── voice/
│   ├── ref.wav            (your reference voice, 24kHz mono)
│   └── ref.txt            (transcript of ref.wav)
├── srt/
│   ├── slide_01.srt
│   ├── slide_02.srt
│   └── ...
├── audio/                 (synthesized by f5-tts-mlx, or supplied by you)
│   ├── slide_01.mp3
│   ├── slide_02.mp3
│   └── ...
├── images/
│   ├── slide_01.png
│   ├── slide_02.png
│   └── ...
└── video/
    ├── slide_01.mp4
    ├── slide_02.mp4
    ├── ...
    └── final_all.mp4
```
