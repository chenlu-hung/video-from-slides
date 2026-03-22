# lecture-notes

Generate lecture narration scripts from PDF or TeX slides, and synthesize speech audio using local TTS models on Apple Silicon.

## Features

- Reads PDF or TeX slides and estimates speaking duration per slide
- Generates an editable `outline.md` for review before script generation
- Batch-generates SRT narration scripts using parallel agents (1–5 slides per batch)
- Validates output for content coverage, SRT format, and timing accuracy
- Supports both Chinese and English slides
- Synthesizes speech audio from SRT scripts using CosyVoice 3 (Swift/CoreML) or Qwen3-TTS (Python/MLX)
- Optional voice cloning from a short reference audio file

## Installation

```bash
git clone https://github.com/chenlu-hung/video-from-slides.git
cd video-from-slides
./install.sh
```

The install script checks and installs all prerequisites (Xcode CLI tools, Homebrew, ffmpeg), builds the Swift TTS CLI, and registers the plugin with Claude Code.

### Prerequisites

- macOS 14+ on Apple Silicon
- Claude Code CLI
- Xcode command-line tools
- ffmpeg (`brew install ffmpeg`)

## Usage

### Step 1: Generate SRT scripts from slides

```
/lecture-notes path/to/slides.pdf
```

### Step 2: Synthesize audio from SRT scripts

First, build the Swift TTS CLI (one-time setup):

```bash
cd scripts/tts
swift build -c release
```

Then run the skill:

```
/tts-synthesis path/to/slides-directory
```

For voice cloning, place a 3–10 second mono WAV file named `voice_ref.wav` in your slides directory before running.

### Step 3: Generate lecture video

```
/video-from-slides path/to/slides-directory
```

Choose to merge all slides into one video or split by sections.

## Workflow

### Script Generation (`/lecture-notes`)

1. **Outline** — Reads slides, estimates duration, produces `outline.md` for your review
2. **Generate** — After you confirm the outline, spawns agents to generate `srt/slide_XX.srt` files
3. **Review** — Validates all SRT files and reports any issues

### TTS Synthesis (`/tts-synthesis`)

1. **Setup** — Locates SRT files, checks CLI build, detects voice reference, confirms settings
2. **Synthesize** — Spawns parallel agents to convert each SRT to a MP3 file
3. **Verify** — Cross-references outputs, checks file sizes, offers to retry failures

### Video Generation (`/video-from-slides`)

1. **Setup** — Checks SRT & audio files exist, converts PDF to PNGs, parses section structure
2. **Generate** — Spawns parallel agents to create Ken Burns videos with audio per slide
3. **Merge** — User chooses merge strategy (all / by section / both)

## Output Structure

```
your-slides-directory/
├── slides.pdf
├── outline.md
├── voice_ref.wav       (optional, for voice cloning)
├── srt/
│   ├── slide_01.srt
│   ├── slide_02.srt
│   └── ...
├── audio/
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

## TTS Backends

| Backend | CLI | Model | Notes |
|---------|-----|-------|-------|
| CoreML (default) | Swift `TTSInfer` | CosyVoice 3 | Fastest on Apple Silicon, uses Neural Engine |
| MLX | Swift `TTSInfer` | CosyVoice 3 | `--backend mlx` flag |
| Python fallback | `fallback/tts_infer.py` | Qwen3-TTS 0.6B | `pip install mlx-audio`, auto-downloads model |
