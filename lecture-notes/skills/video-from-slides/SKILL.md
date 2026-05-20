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

Generate lecture videos from PDF slides with narration audio. Each slide becomes a video segment with a Ken Burns effect (slow zoom-in), synchronized to its audio. Segments are merged into a final video, and all subtitles are merged into a single external SRT file (not burned into the video).

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
3. **Per-slide Video Generation** — Run ffmpeg in parallel via Python ThreadPoolExecutor
4. **Merge** — Ask user for merge strategy, concatenate videos with ffmpeg, generate merged external SRT

---

## Phase 1: Setup & Validation

### Check Prerequisites

1. **ffmpeg**: Run `which ffmpeg`. If not found, tell the user to install it (`brew install ffmpeg`) and abort.

   > **macOS gotcha — broken x265 symlink**: If ffmpeg exits with `Library not loaded: .../libx265.NNN.dylib`, find the installed x265 version with `find /opt/homebrew -name "libx265*.dylib"` and create a symlink at the missing path pointing to the installed one:
   > ```bash
   > ln -sf /opt/homebrew/Cellar/x265/<old-version>/lib/libx265.<old-N>.dylib \
   >        /opt/homebrew/opt/x265/lib/libx265.<old-N>.dylib
   > ```

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

Find the PDF file in the slides directory. Use `pdftocairo` (from poppler, installed via `brew install poppler`) as the primary method:

```bash
mkdir -p <slides-directory>/images
pdftocairo -png -r 150 -scale-to-x 1920 -scale-to-y -1 "<slides.pdf>" "<slides-directory>/images/slide"
```

This produces files named `slide-01.png`, `slide-02.png`, … (with a **dash** separator). Use this naming when constructing image paths in Phase 3.

If `pdftocairo` is not available, fall back to the macOS Quartz approach:

```bash
python3 -c "
import subprocess, sys
from pathlib import Path
from Quartz import PDFDocument
from Foundation import NSURL

pdf_path = sys.argv[1]
out_dir = sys.argv[2]
url = NSURL.fileURLWithPath_(pdf_path)
doc = PDFDocument.alloc().initWithURL_(url)
count = doc.pageCount()
print(f'Extracting {count} pages...')
for i in range(count):
    page = doc.pageAtIndex_(i)
    tmp_pdf = f'{out_dir}/tmp_page_{i+1:02d}.pdf'
    page.dataRepresentation().writeToFile_atomically_(tmp_pdf, True)
    out_png = f'{out_dir}/slide_{i+1:02d}.png'
    subprocess.run(['sips', '-s', 'format', 'png', '--resampleWidth', '1920', tmp_pdf, '--out', out_png], capture_output=True)
    Path(tmp_pdf).unlink()
    print(f'  slide_{i+1:02d}.png')
" "<pdf-path>" "<slides-directory>/images"
```

Note: the Quartz fallback produces `slide_01.png` (underscore). Adjust image path construction in Phase 3 accordingly.

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
  Resolution:   1920x1080 (4:3 slides pillarboxed in 16:9)
  FPS:          30
  Ken Burns:    slow zoom-in (~5%)
  Subtitle:     merged external SRT (not burned into video)
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

### Run with Python ThreadPoolExecutor

Do **not** use `ProcessPoolExecutor` — on macOS (spawn start method) it requires a `if __name__ == '__main__'` guard that breaks when the script is run directly. Use `ThreadPoolExecutor` instead; subprocess calls release the GIL so parallelism is real.

Write the following script to a temp file and run it. Adjust `SLIDES_DIR`, `N_SLIDES`, and `IMG_PATTERN` based on what pdftocairo produced (`slide-NN.png` with dash, or `slide_NN.png` with underscore).

```python
#!/usr/bin/env python3
import subprocess
import concurrent.futures
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

SLIDES_DIR = Path("<slides-directory>")
VIDEO_DIR  = SLIDES_DIR / "video"
VIDEO_DIR.mkdir(exist_ok=True)

# pdftocairo produces "slide-NN.png" (dash); Quartz fallback produces "slide_NN.png" (underscore)
IMG_PATTERN = "slide-{i:02d}.png"   # adjust if needed

VF = (
    "scale=1440:1080:flags=lanczos,"
    "zoompan=z='min(zoom+0.0002,1.05)':d=9999"
    ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1440x1080,"
    "pad=1920:1080:240:0:black,"
    "format=yuv420p"
)

def process_slide(i):
    img    = SLIDES_DIR / "images" / IMG_PATTERN.format(i=i)
    audio  = SLIDES_DIR / "audio"  / f"slide_{i:02d}.mp3"
    output = VIDEO_DIR             / f"slide_{i:02d}.mp4"
    log    = VIDEO_DIR             / f"slide_{i:02d}.log"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "30", "-i", str(img),
        "-i", str(audio),
        "-vf", VF,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-r", "30",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",   # end when audio ends; image loops until then
        str(output)
    ]
    with open(log, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)

    if result.returncode == 0 and output.exists():
        mb = output.stat().st_size / 1024 / 1024
        return f"OK  slide_{i:02d}  {mb:.1f} MB"
    else:
        tail = log.read_text().splitlines()[-5:]
        return f"FAIL slide_{i:02d}\n" + "\n".join(tail)

N_SLIDES = <total_slide_count>

print(f"Generating {N_SLIDES} slide videos with 4 parallel workers...", flush=True)
results = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(process_slide, i): i for i in range(1, N_SLIDES + 1)}
    for fut in concurrent.futures.as_completed(futs):
        msg = fut.result()
        print(msg, flush=True)
        results.append(msg)

fails = [r for r in results if r.startswith("FAIL")]
print(f"\nDone. {len(results)-len(fails)}/{N_SLIDES} succeeded, {len(fails)} failed.")
if fails:
    sys.exit(1)
```

Run it in the background (it can take several minutes for long lectures):

```bash
python3 /tmp/generate_slides_video.py
```

Monitor progress by counting completed MP4s:

```bash
ls <slides-directory>/video/*.mp4 | wc -l
```

If any slides fail, check the corresponding `.log` file in `video/` for the ffmpeg error, fix the issue, and re-run for just those slides before proceeding.

Clean up log files after all slides succeed:

```bash
rm -f <slides-directory>/video/*.log
```

### ffmpeg Filter Explained

The `-vf` chain:
1. `scale=1440:1080:flags=lanczos` — scale 4:3 slide to 1440×1080 (preserves full content)
2. `zoompan=z='min(zoom+0.0002,1.05)':d=9999:...` — Ken Burns slow zoom from 1.0→1.05×, centered, over the full clip duration (`d=9999` frames ≫ any single slide duration, so zoom motion never resets)
3. `pad=1920:1080:240:0:black` — add 240 px black bars left and right, producing standard 16:9 1920×1080 output
4. `format=yuv420p` — ensure broad player compatibility

`-shortest` ends the output stream when the audio ends (the image loops indefinitely, so without this the video would run forever).

---

## Phase 4: Merge

### Verify All Slide Videos

After all slides complete:
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

Use the ffmpeg concat demuxer (no re-encoding):

```bash
# Build concat list
VIDEO_DIR="<slides-directory>/video"
> "$VIDEO_DIR/concat_list.txt"
for i in $(seq -f "%02g" 1 <N>); do
    echo "file 'slide_${i}.mp4'" >> "$VIDEO_DIR/concat_list.txt"
done

# Merge
ffmpeg -y -f concat -safe 0 -i "$VIDEO_DIR/concat_list.txt" -c copy "$VIDEO_DIR/final_all.mp4"
rm "$VIDEO_DIR/concat_list.txt"
```

For section merges, build one concat list per section and output `section_NN_<name>.mp4`.

### Generate Merged External SRT

After concatenation (or per-section), generate a single `final.srt` (or per-section SRT) that covers the full video timeline. Use **actual MP4 durations** (not SRT end timestamps) as the offset ground truth, so subtitle timing matches the video exactly even if audio was trimmed.

Write and run the following Python script:

```python
#!/usr/bin/env python3
"""
Merge per-slide SRT files into a single final.srt with adjusted timestamps.
Timestamps are shifted by cumulative actual MP4 durations.
"""
import subprocess, re, sys
from pathlib import Path

SLIDES_DIR = Path("<slides-directory>")
VIDEO_DIR  = SLIDES_DIR / "video"
SRT_DIR    = SLIDES_DIR / "srt"

def get_mp4_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())

def srt_time_to_ms(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)

def ms_to_srt_time(ms):
    h = ms // 3600000; ms %= 3600000
    m = ms // 60000;   ms %= 60000
    s = ms // 1000;    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(path):
    text = path.read_text(encoding="utf-8")
    entries = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        tc_line = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if tc_line is None:
            continue
        start_str, end_str = [x.strip() for x in lines[tc_line].split("-->")]
        content = "\n".join(lines[tc_line+1:]).strip()
        entries.append((srt_time_to_ms(start_str), srt_time_to_ms(end_str), content))
    return entries

slide_nums = sorted(
    int(p.stem.replace("slide_", ""))
    for p in SRT_DIR.glob("slide_*.srt")
)

offset_ms  = 0
all_entries = []
entry_idx   = 1

for n in slide_nums:
    srt_path = SRT_DIR / f"slide_{n:02d}.srt"
    mp4_path = VIDEO_DIR / f"slide_{n:02d}.mp4"

    if mp4_path.exists():
        slide_duration_ms = int(get_mp4_duration(mp4_path) * 1000)
    else:
        # fallback: use last SRT end time
        entries = parse_srt(srt_path)
        slide_duration_ms = entries[-1][1] if entries else 0
        print(f"  WARNING: {mp4_path.name} missing — using SRT end time as duration")

    for start, end, content in parse_srt(srt_path):
        all_entries.append((entry_idx, start + offset_ms, end + offset_ms, content))
        entry_idx += 1

    offset_ms += slide_duration_ms

out_path = VIDEO_DIR / "final.srt"
with open(out_path, "w", encoding="utf-8") as f:
    for idx, start, end, content in all_entries:
        f.write(f"{idx}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{content}\n\n")

total_s = offset_ms // 1000
print(f"Wrote {len(all_entries)} cues to {out_path}")
print(f"Total duration: {total_s//3600:02d}:{(total_s%3600)//60:02d}:{total_s%60:02d}")
```

For section merges, run this script with a filtered `slide_nums` list per section and output `section_NN.srt`.

The resulting `.srt` file is **external** — load it in VLC, IINA, or any player that supports external subtitle tracks. It is never burned into the video.

### Report Results

```
Video Generation Complete:
  ✓ final_all.mp4   (60 MB, 35:20)
  ✓ final.srt       (395 cues — load as external subtitle in VLC / IINA)
  or
  ✓ section_01_introduction.mp4   (8 MB,  3:45)  + section_01.srt
  ✓ section_02_main_content.mp4   (52 MB, 31:35) + section_02.srt
```
