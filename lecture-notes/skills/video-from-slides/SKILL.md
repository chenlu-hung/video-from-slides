---
name: video-from-slides
description: >-
  This skill should be used when the user asks to "generate lecture video",
  "create video from slides", "make teaching video", "combine slides and audio into video",
  "製作教學影片", "生成投影片影片", "把投影片轉成影片", "合併影片",
  or has slides with SRT scripts and audio files and wants a final video produced.
argument-hint: <path-to-slides-directory>
allowed-tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"]
---

# Video from Slides

Generate lecture videos from PDF slides with narration audio. Each slide becomes a video segment with a Ken Burns effect (slow zoom-in), synchronized to its SRT narration timing. Segments are then merged into a final video.

## Prerequisites

- `ffmpeg` in PATH (`brew install ffmpeg`)
- Completed `/lecture-notes` pipeline (SRT files in `srt/`)
- Either of:
  - **Audio supplied**: per-slide `audio/slide_XX.mp3` already present, OR
  - **Reference voice for TTS**: `voice/ref.wav` (24kHz mono, 5–10s) + `voice/ref.txt`
    (the transcript of that clip). The skill will synthesize all missing MP3s via
    `python -m f5_tts_mlx.generate`, run inside the uv-managed project at
    `~/.local/share/lecture-notes/tts-py/` (set up by `install.sh`).

## Workflow Overview

Four phases:
1. **Setup & Validation** — Check prerequisites, convert PDF to PNGs, parse sections, confirm settings
2. **TTS Synthesis** *(only if any `audio/slide_XX.mp3` is missing)* — Spawn one `tts-synthesizer` agent to fill in the missing audio
3. **Per-slide Video Generation** — Spawn `video-composer` agents in parallel batches
4. **Merge** — Ask user for merge strategy, concatenate with ffmpeg

---

## Phase 1: Setup & Validation

### Check Prerequisites

1. **ffmpeg**: Run `which ffmpeg`. If not found, tell the user to install it (`brew install ffmpeg`) and abort.

2. **SRT files**: Check for `srt/slide_*.srt` in the slides directory. If none found, abort with:
   > No SRT files found. Please run `/lecture-notes` first to generate narration scripts.

3. **Audio files**: Check for `audio/slide_*.mp3` in the slides directory. Build the list of slide numbers that have an SRT but no matching MP3 — call this `missing_audio`.

   - If `missing_audio` is empty → user supplied all audio, skip Phase 2 (TTS) later.
   - If `missing_audio` is non-empty → TTS will run in Phase 2. Validate TTS prerequisites now (do **not** abort yet):
     - `uv` is on PATH (`command -v uv`). If not, abort and tell the user to re-run `install.sh`.
     - `~/.local/share/lecture-notes/tts-py/pyproject.toml` exists and `f5-tts-mlx` is importable in that env (`uv run --project ~/.local/share/lecture-notes/tts-py --quiet python -c 'import f5_tts_mlx'`). If not, abort and tell the user to re-run `install.sh`.
     - `voice/ref.wav` exists and is 24kHz mono (`ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels -of csv=p=0 voice/ref.wav` should output `24000,1`). If missing or wrong format, abort with the exact expected filenames and format.
     - `voice/ref.txt` exists and is non-empty.

4. **Cross-reference**: At this point every SRT either has a matching MP3 or is in `missing_audio` (which will be filled in Phase 2).

### Convert PDF to PNG

Find the PDF file in the slides directory. Convert each page to a PNG image:

```bash
mkdir -p <slides-directory>/images
sips -s format png --resampleWidth 1920 <slides.pdf> --out <slides-directory>/images/
```

If `sips` cannot handle multi-page PDF extraction, use this approach instead:

```bash
# Use the built-in macOS Python to split PDF pages, then convert each
python3 -c "
import subprocess, sys
from pathlib import Path

pdf_path = sys.argv[1]
out_dir = sys.argv[2]

# Use Quartz (PyObjC) to count pages
from Quartz import PDFDocument
from Foundation import NSURL

url = NSURL.fileURLWithPath_(pdf_path)
doc = PDFDocument.alloc().initWithURL_(url)
if not doc:
    print(f'Error: cannot open {pdf_path}', file=sys.stderr)
    sys.exit(1)

count = doc.pageCount()
print(f'Extracting {count} pages...')

for i in range(count):
    page = doc.pageAtIndex_(i)
    # Export each page as a single-page PDF, then convert to PNG via sips
    tmp_pdf = f'{out_dir}/tmp_page_{i+1:02d}.pdf'
    data = page.dataRepresentation()
    data.writeToFile_atomically_(tmp_pdf, True)
    out_png = f'{out_dir}/slide_{i+1:02d}.png'
    subprocess.run(['sips', '-s', 'format', 'png', '--resampleWidth', '1920', tmp_pdf, '--out', out_png],
                   capture_output=True)
    Path(tmp_pdf).unlink()
    print(f'  slide_{i+1:02d}.png')
" "<pdf-path>" "<slides-directory>/images"
```

Verify that the number of PNGs matches the number of SRT files. If there is a mismatch, warn the user but continue (some slides may be intentionally skipped).

### Parse Section Structure

Read `outline.md` and identify section boundaries. Sections are indicated by slide entries whose title contains keywords like "Section", "Part", "章", "節", or whose outline notes indicate a new topic. Build a mapping:

```
sections = [
  { "name": "Introduction", "slides": [1, 2, 3] },
  { "name": "Main Content", "slides": [4, 5, 6, 7, 8] },
  ...
]
```

If no clear section structure is found, treat all slides as one section.

### Confirm Settings

Display a summary and ask for confirmation:

```
Video Generation Settings:
  Slides:       <N> PNG images in <path>/images/
  SRT files:    <N> files in <path>/srt/
  Audio files:  <K> existing, <M> to synthesize (or "all existing" / "all to synthesize")
  TTS engine:   f5-tts-mlx via uv (only if M > 0)
  Reference:    voice/ref.wav  (only if M > 0)
  Output:       <path>/video/
  Resolution:   1920x1080
  FPS:          30
  Ken Burns:    slow zoom-in (~5%)
  Sections:     <N> sections detected

Proceed? (yes/no)
```

**Do NOT proceed to Phase 2 without user confirmation.**

---

## Phase 2: TTS Synthesis *(skip if `missing_audio` is empty)*

### Create Output Directory

```bash
mkdir -p <slides-directory>/audio
```

### Agent Invocation

Spawn **one** `tts-synthesizer` agent (do not batch — MLX uses unified memory and concurrent runs would OOM). Pass the agent:

- `<slides-directory>` — absolute path
- `<tts-project-dir>` — `~/.local/share/lecture-notes/tts-py` (expanded to an absolute path; the uv project where `f5-tts-mlx` is installed)
- `<ref-wav>` — `<slides-directory>/voice/ref.wav`
- `<ref-text>` — contents of `<slides-directory>/voice/ref.txt`, read by the skill and passed inline
- The list of slide numbers in `missing_audio`

Tell the agent: process slides sequentially, write outputs to `<slides-directory>/audio/slide_NN.mp3`, verify each MP3 duration is within ±10% of the SRT target (retry once with bumped `--speed` if too long).

### Heads-up About First Run

The first `python -m f5_tts_mlx.generate` invocation downloads the MLX checkpoint (~1.5 GB) from Hugging Face into `~/.cache/huggingface/`. Surface a one-line notice to the user before spawning the agent so the apparent stall on slide 1 is expected.

### Optional: Alternate Checkpoints for Better Mandarin

The default `lucasnewman/f5-tts-mlx` checkpoint covers English well and Mandarin reasonably. For better Traditional Chinese results, advanced users can edit the agent prompt to pass `--model <alternate-repo-id>` to `python -m f5_tts_mlx.generate`. Surface this hint in the post-run summary only if the user reports Mandarin quality issues.

### After Synthesis

Re-check that every SRT now has a matching MP3. If any are still missing (TTS failed for them), ask the user whether to retry only the failed slides or abort.

---

## Phase 3: Per-slide Video Generation

### Create Output Directory

```bash
mkdir -p <slides-directory>/video
```

### Batch Strategy

- Group slides into batches of 3–5
- For each batch, spawn a `video-composer` agent using the Agent tool
- Pass each agent:
  - The list of slide numbers in its batch
  - The slides directory path (containing `images/`, `srt/`, `audio/` subdirectories)
  - The output directory path (`video/`)

### Agent Invocation

For each batch, invoke `video-composer` with a prompt like:

```
Generate lecture videos for the following slides:

Slides directory: <slides-directory>
Output directory: <slides-directory>/video/
FPS: 30
Resolution: 1920x1080

Slides to process: 3, 4, 5

For each slide XX:
  - Image: <slides-directory>/images/slide_XX.png
  - SRT: <slides-directory>/srt/slide_XX.srt
  - Audio: <slides-directory>/audio/slide_XX.mp3

Steps per slide:
1. Parse the SRT file to get the last timecode end time as the target duration
2. Generate a Ken Burns (slow zoom-in) video from the slide image matching that duration
3. Mux the audio MP3 into the video
4. Output as <slides-directory>/video/slide_XX.mp4

Verify each output MP4 exists and is non-empty. Report success or failure per slide.
```

### Parallel Execution

Launch multiple `video-composer` agents in parallel. Each batch is independent.

---

## Phase 4: Merge

### Verify All Slide Videos

After all agents complete:
1. List `video/slide_*.mp4` and compare against `srt/slide_*.srt`
2. Report any missing videos
3. If there are failures, offer to retry before merging

### Ask Merge Strategy

Prompt the user:

```
All slide videos are ready. How would you like to merge them?

1. Merge all slides into one video (final_all.mp4)
2. Merge by section:
   - Section 1: "Introduction" (slides 1-3) → section_01_introduction.mp4
   - Section 2: "Main Content" (slides 4-8) → section_02_main_content.mp4
   ...
3. Both (section videos + one combined video)

Enter 1, 2, or 3:
```

### Concatenate Videos

Use the ffmpeg concat demuxer:

```bash
# Create a concat list file
cat > <video-dir>/concat_list.txt << 'EOF'
file 'slide_01.mp4'
file 'slide_02.mp4'
file 'slide_03.mp4'
EOF

# Merge without re-encoding
ffmpeg -f concat -safe 0 -i <video-dir>/concat_list.txt -c copy <output-path>
```

For section merges, create one concat list per section.

Clean up the temporary concat list files after merging.

### Report Results

```
Video Generation Complete:
  ✓ final_all.mp4  (128.5 MB, 15:32)
  or
  ✓ section_01_introduction.mp4  (32.1 MB, 3:45)
  ✓ section_02_main_content.mp4  (96.4 MB, 11:47)
```
