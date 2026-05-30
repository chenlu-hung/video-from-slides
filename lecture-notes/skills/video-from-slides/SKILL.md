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

Generate lecture videos from PDF slides with narration audio. Each narrate page becomes a video segment with a Ken Burns effect (slow zoom-in), synchronized to its audio. Segments are merged into a final video, and all subtitles are merged into a single external SRT file (not burned into the video).

**Overlay-aware.** Beamer-style overlays (one frame revealed step by step across several PDF pages) are read from the `OVERLAY-GROUPS` table in `outline.md`: the pages of a build-up are stitched into one **logical slide** whose Ken Burns zoom runs continuously across the reveal steps and resets at the next slide, so the final video shows the content appearing progressively rather than re-narrating near-identical pages. Source aspect ratio (4:3, 16:9, …) is auto-detected — no hardcoded pillarbox.

## Prerequisites

- `ffmpeg` in PATH (`brew install ffmpeg`)
- Completed `/lecture-notes` pipeline (SRT files in `srt/`)
- Either of:
  - **Audio supplied**: per-slide `audio/slide_XX.mp3` already present, OR
  - **Reference voice for TTS**: `voice/ref.wav` (mono, 5–10s; any sample rate —
    VoxCPM2 resamples internally) + `voice/ref.txt` (the transcript of that clip).
    The skill will synthesize all missing MP3s with VoxCPM2 via the `mlx-audio`
    helper, run inside the uv-managed project at
    `~/.local/share/lecture-notes/tts-py/` (set up by `install.sh`).

## Workflow Overview

Four phases:
1. **Setup & Validation** — Check prerequisites, convert PDF to PNGs, parse overlay groups + sections from `outline.md`, detect aspect ratio, confirm settings
2. **TTS Synthesis** *(only if any `audio/slide_XX.mp3` is missing)* — Spawn one `tts-synthesizer` agent to fill in the missing audio
3. **Per-segment Video Generation** — Render one segment per narrate page (continuous per-logical-slide zoom) in parallel via Python ThreadPoolExecutor
4. **Merge** — Ask user for merge strategy, concatenate segments with ffmpeg, generate merged external SRT

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
     - `~/.local/share/lecture-notes/tts-py/pyproject.toml` exists and `mlx-audio` is importable in that env (`uv run --project ~/.local/share/lecture-notes/tts-py --quiet python -c 'import mlx_audio'`). If not, abort and tell the user to re-run `install.sh`.
     - `voice/ref.wav` exists and is mono (`ffprobe -v error -select_streams a:0 -show_entries stream=channels -of csv=p=0 voice/ref.wav` should output `1`). Sample rate is unconstrained — VoxCPM2 resamples internally. If missing or not mono, abort with the exact expected filenames and format.
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

One PNG is rendered **per PDF page** (including overlay sub-frames). The number of PNGs therefore matches the PDF page count `P`, which may be **larger** than the number of SRTs — see "Parse Overlay Groups" next.

### Parse Overlay Groups

`outline.md` is the single source of truth for how PDF pages collapse into **logical slides** (overlay build-ups). Parse the machine-readable table delimited by `<!-- OVERLAY-GROUPS:START -->` / `<!-- OVERLAY-GROUPS:END -->`. Each row gives `logical | pages | narrate_pages | title`:

- `pages` — full PDF page span of the group (e.g. `2-8` or a single `5`).
- `narrate_pages` — the pages that actually have an SRT/audio/video segment (e.g. `2,3,5,6,8`); the rest are **merged** and have no files.

Build a structure like:

```python
logical_slides = [
  {"logical": 1, "pages": [1],            "narrate": [1],            "title": "Title"},
  {"logical": 2, "pages": [2,3,4,5,6,7,8],"narrate": [2,3,5,6,8],    "title": "Integration-by-parts: A glance"},
  ...
]
narrate_pages = [1, 2, 3, 5, 6, 8, ...]   # flat, ordered — the only pages with srt/audio/video
```

**Backward compatibility:** if `outline.md` has no `OVERLAY-GROUPS` block (older runs, or a non-overlay deck where the table was never written), fall back to treating **every page that has a `srt/slide_XX.srt` as its own single-step logical slide**. The rest of the pipeline is identical in that case.

Cross-check: every `narrate_page` must have a `srt/slide_XX.srt`; every `srt/slide_XX.srt` must be a `narrate_page`. Warn on any mismatch but continue.

### Detect Aspect Ratio

The source slides may be 4:3 or 16:9 (or other). Detect the page aspect ratio once and reuse it for every ffmpeg filter — do **not** assume 4:3.

```bash
# Page size in points, e.g. "453.543 x 255.118"
pdfinfo "<slides.pdf>" | awk -F'[: ]+' '/Page size/ {print $4, $6}'
```

Or, equivalently, probe a rendered PNG:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "<images>/slide-01.png"
```

Compute `AR = width / height`. Fit the slide inside 1920×1080 preserving AR, then pad to fill:

- `inner_h = 1080`, `inner_w = round(1080 * AR / 2) * 2` (even). If `inner_w > 1920`, instead set `inner_w = 1920`, `inner_h = round(1920 / AR / 2) * 2` (letterbox top/bottom).
- Pad offsets: `pad_x = (1920 - inner_w) // 2`, `pad_y = (1080 - inner_h) // 2`.

Examples: 16:9 → `inner_w=1920, pad_x=0` (no bars); 4:3 → `inner_w=1440, pad_x=240` (pillarbox, as before). Pass `inner_w, inner_h, pad_x, pad_y` into Phase 3.

### Parse Section Structure

Read `outline.md` and identify section boundaries. Sections are indicated by logical-slide entries whose title contains keywords like "Section", "Part", "章", "節", or whose outline notes indicate a new topic. A section is a contiguous run of **logical slides**; expand each to its narrate pages for concatenation. A section boundary never falls inside an overlay group (the group is one logical slide). Build a mapping:

```
sections = [
  { "name": "Introduction", "logical": [1, 2],    "narrate": [1, 2, 3, 5, 6, 8] },
  { "name": "Main Content", "logical": [3, 4, 5], "narrate": [9, 10, 11, ...] },
  ...
]
```

If no clear section structure is found, treat all logical slides as one section.

### Confirm Settings

Display a summary and ask for confirmation:

```
Video Generation Settings:
  PDF pages:    <P> PNG images in <path>/images/
  Logical slides: <L>  (<G> overlay build-ups collapsing <P-L+G> sub-frames)
  Narrate pages: <N> (= SRT files in <path>/srt/; merged sub-frames omitted)
  Audio files:  <K> existing, <M> to synthesize (or "all existing" / "all to synthesize")
  TTS engine:   VoxCPM2 (mlx-audio) via uv (only if M > 0)
  Reference:    voice/ref.wav  (only if M > 0)
  Output:       <path>/video/
  Aspect ratio: <W>x<H> source → inner <inner_w>x<inner_h>, pad <pad_x>/<pad_y> (e.g. "16:9 → no bars" / "4:3 → 240px pillarbox")
  Resolution:   1920x1080
  FPS:          30
  Ken Burns:    slow zoom-in (~5%); continuous across each overlay build-up, resets per logical slide
  Subtitle:     merged external SRT (not burned into video)
  Sections:     <S> sections detected

Proceed? (yes/no)
```

If `G` is 0 (no overlay groups), present it as a plain deck — logical slides == pages and the line about build-ups can be dropped.

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
- `<tts-project-dir>` — `~/.local/share/lecture-notes/tts-py` (expanded to an absolute path; the uv project where `mlx-audio` is installed)
- `<ref-wav>` — `<slides-directory>/voice/ref.wav`
- `<ref-text>` — contents of `<slides-directory>/voice/ref.txt`, read by the skill and passed inline
- The list of slide numbers in `missing_audio`

Tell the agent: process slides sequentially, write outputs to `<slides-directory>/audio/slide_NN.mp3` and a corrected per-slide SRT to `<slides-directory>/srt-synced/slide_NN.srt`. The helper runs in `--timing natural` (VoxCPM2 has no speed control, so it plays at natural pace and the corrected SRT follows the audio — see the agent prompt). The agent reports each slide's natural-pace drift (`+Xs over SRT`); a large drift means that script was timed tight and the user may want to loosen it, but the audio/subtitles stay in sync regardless.

### Heads-up About First Run

The first synthesis call downloads the VoxCPM2-8bit MLX checkpoint (~3.2 GB) from Hugging Face into `~/.cache/huggingface/`. Surface a one-line notice to the user before spawning the agent so the apparent stall on slide 1 is expected.

### Optional: Alternate Checkpoints and Tuning

The default `mlx-community/VoxCPM2-8bit` is multilingual (Chinese included) and a good speed/quality balance. Advanced users can edit the agent prompt to pass `--model mlx-community/VoxCPM2-4bit` (lighter/faster) or `mlx-community/VoxCPM2-bf16` (highest quality), and tune `--inference-timesteps` / `--cfg`. Surface this hint in the post-run summary only if the user reports voice quality issues.

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

One segment is produced **per narrate page**, named `slide_NN.mp4` (NN = PDF page). Each segment's Ken Burns zoom is a slice of a **per-logical-slide** zoom trajectory: the zoom runs continuously across an overlay build-up and **resets to 1.0 at each new logical slide**. Because the transition within a group and between groups are both just a hard cut (concat), this falls out by computing each page's zoom start/end from its position in the logical-slide audio timeline — no multi-image ffmpeg call needed.

Fill in the three blocks marked `# >>>`: the image pattern, the aspect-ratio fit from Phase 1, and `LOGICAL` (each logical slide as a list of its narrate PDF pages, from the `OVERLAY-GROUPS` table). Write the script to a temp file and run it.

```python
#!/usr/bin/env python3
import subprocess
import concurrent.futures
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

SLIDES_DIR = Path("<slides-directory>")
VIDEO_DIR  = SLIDES_DIR / "video"
AUDIO_DIR  = SLIDES_DIR / "audio"
IMAGE_DIR  = SLIDES_DIR / "images"
VIDEO_DIR.mkdir(exist_ok=True)

FPS = 30
ZOOM_MAX = 0.05   # total Ken Burns zoom (1.00 -> 1.05) spread across each logical slide

# >>> pdftocairo produces "slide-NN.png" (dash); Quartz fallback "slide_NN.png" (underscore)
IMG_PATTERN = "slide-{i:02d}.png"

# >>> Aspect-ratio fit from Phase 1 "Detect Aspect Ratio" (16:9 -> 1920,1080,0,0; 4:3 -> 1440,1080,240,0)
INNER_W, INNER_H, PAD_X, PAD_Y = 1920, 1080, 0, 0

# >>> Logical slides as ordered lists of NARRATE PDF pages (from the OVERLAY-GROUPS table).
#     Single-page slides are length-1 lists; merged pages are simply absent.
LOGICAL = [
    [1],
    [2, 3, 5, 6, 8],
    [9, 10, 11],
    # ...
]

def mp3_duration(page):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(AUDIO_DIR / f"slide_{page:02d}.mp3")],
        capture_output=True, text=True)
    return float(r.stdout.strip())

PAGES = [p for group in LOGICAL for p in group]   # flat narrate pages, in order
DUR   = {p: mp3_duration(p) for p in PAGES}

# Per-page zoom window: continuous within a logical slide, reset to 1.0 per slide.
ZWIN = {}
for group in LOGICAL:
    total = sum(DUR[p] for p in group) or 1.0
    elapsed = 0.0
    for p in group:
        zstart = 1.0 + ZOOM_MAX * (elapsed / total)
        elapsed += DUR[p]
        zend   = 1.0 + ZOOM_MAX * (elapsed / total)
        ZWIN[p] = (zstart, zend)

def vf_for(page):
    zstart, zend = ZWIN[page]
    nframes = max(int(round(DUR[page] * FPS)), 1)
    step = (zend - zstart) / nframes
    # Accumulator-style linear zoom (same mechanism as the proven d=9999 form):
    # floor at zstart, add a fixed step per output frame, cap at zend.
    return (
        f"scale={INNER_W}:{INNER_H}:flags=lanczos,"
        f"zoompan=z='min(max(zoom,{zstart:.6f})+{step:.8f},{zend:.6f})':d=9999"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={INNER_W}x{INNER_H},"
        f"pad=1920:1080:{PAD_X}:{PAD_Y}:black,"
        f"format=yuv420p"
    )

def process_page(page):
    img    = IMAGE_DIR / IMG_PATTERN.format(i=page)
    audio  = AUDIO_DIR / f"slide_{page:02d}.mp3"
    output = VIDEO_DIR / f"slide_{page:02d}.mp4"
    log    = VIDEO_DIR / f"slide_{page:02d}.log"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(img),
        "-i", str(audio),
        "-vf", vf_for(page),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",   # end when audio ends; image loops until then
        str(output)
    ]
    with open(log, "w") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)

    if result.returncode == 0 and output.exists():
        mb = output.stat().st_size / 1024 / 1024
        return f"OK  slide_{page:02d}  {mb:.1f} MB"
    else:
        tail = log.read_text().splitlines()[-5:]
        return f"FAIL slide_{page:02d}\n" + "\n".join(tail)

print(f"Generating {len(PAGES)} segments ({len(LOGICAL)} logical slides) with 4 workers...", flush=True)
results = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(process_page, p): p for p in PAGES}
    for fut in concurrent.futures.as_completed(futs):
        msg = fut.result()
        print(msg, flush=True)
        results.append(msg)

fails = [r for r in results if r.startswith("FAIL")]
print(f"\nDone. {len(results)-len(fails)}/{len(PAGES)} succeeded, {len(fails)} failed.")
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

The `-vf` chain (all four numbers come from Phase 1, the zoom from the logical-slide timeline):
1. `scale=INNER_W:INNER_H:flags=lanczos` — scale the slide to its aspect-ratio-correct inner size (1920×1080 for 16:9 → no bars; 1440×1080 for 4:3 → pillarbox)
2. `zoompan=z='min(max(zoom,ZSTART)+STEP,ZEND)':d=9999:...:s=INNER_WxINNER_H` — Ken Burns zoom over the clip, but starting at `ZSTART` and ending at `ZEND` (not always 1.0→1.05). For a single-page slide that's 1.0→1.05; for step _i_ of an overlay build-up it's the slice of the 1.0→1.05 trajectory covering that step's share of the logical slide's audio, so concatenating the steps gives one continuous zoom that resets only at the next logical slide. `d=9999` ≫ any clip's frame count, so the accumulator stays on the single looped image.
3. `pad=1920:1080:PAD_X:PAD_Y:black` — center the inner image in 1920×1080 (PAD_X=240 pillarbox for 4:3; PAD_X=0 for 16:9; PAD_Y>0 letterboxes ultra-wide sources)
4. `format=yuv420p` — ensure broad player compatibility

`-shortest` ends the output stream when the audio ends (the image loops indefinitely, so without this the video would run forever).

---

## Phase 4: Merge

### Verify All Slide Videos

After all segments complete:
1. List `video/slide_*.mp4` and compare against `srt/slide_*.srt` — there is one of each **per narrate page** (merged sub-frames have neither). Numbering is non-contiguous when pages were merged; that is expected.
2. Report any narrate page missing its MP4
3. If there are failures, offer to retry before merging

### Ask Merge Strategy

Prompt the user:

```
All slide videos are ready. How would you like to merge them?

1. Merge everything into one video (final_all.mp4)
2. Merge by section (section boundaries fall between logical slides):
   - Section 1: "Introduction" (logical 1-2) → section_01_introduction.mp4
   - Section 2: "Main Content" (logical 3-5) → section_02_main_content.mp4
   ...
3. Both (section videos + one combined video)

Enter 1, 2, or 3:
```

### Concatenate Videos

Use the ffmpeg concat demuxer (no re-encoding). **Do not** generate the list with `seq` — narrate-page numbers are non-contiguous when pages were merged. List the **actual** segments in numeric order:

```bash
# Build concat list from the segments that actually exist, in page order
VIDEO_DIR="<slides-directory>/video"
ls "$VIDEO_DIR"/slide_*.mp4 | sort -V | sed "s|.*/|file '|; s|$|'|" > "$VIDEO_DIR/concat_list.txt"

# Merge
ffmpeg -y -f concat -safe 0 -i "$VIDEO_DIR/concat_list.txt" -c copy "$VIDEO_DIR/final_all.mp4"
rm "$VIDEO_DIR/concat_list.txt"
```

For section merges, build one concat list per section from that section's narrate pages (the `narrate` list from Parse Section Structure), e.g. `printf "file 'slide_%02d.mp4'\n" 1 2 3 5 6 8`, and output `section_NN_<name>.mp4`. Because zoom resets per logical slide, every section starts cleanly at zoom 1.0.

### Generate Merged External SRT

After concatenation (or per-section), generate a single `final.srt` (or per-section SRT) that covers the full video timeline. Use **actual MP4 durations** (not SRT end timestamps) as the offset ground truth, so subtitle timing matches the video exactly even if audio was trimmed.

For each narrate page the script prefers the **corrected** per-slide SRT in `srt-synced/` (written by the TTS helper, with cue timings matching the synthesized audio) and falls back to the original `srt/` only when no corrected one exists (e.g. user-supplied audio). This keeps within-slide subtitle timing exact under VoxCPM2's natural pacing.

This script already handles overlay grouping for free: it iterates the SRTs that **exist** (one per narrate page — merged pages contribute none) and shifts each by the cumulative segment duration, so the per-step cues of a build-up land back-to-back and flow seamlessly into the next logical slide.

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
SYNCED_DIR = SLIDES_DIR / "srt-synced"   # corrected SRTs (cues match the audio)

def pick_srt(n):
    """Prefer the corrected SRT in srt-synced/; fall back to the original srt/."""
    synced = SYNCED_DIR / f"slide_{n:02d}.srt"
    return synced if synced.exists() else SRT_DIR / f"slide_{n:02d}.srt"

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
    srt_path = pick_srt(n)
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

For section merges, run this script with `slide_nums` filtered to that section's narrate pages and output `section_NN.srt`.

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
