---
name: tts-synthesis
description: >-
  This skill should be used when the user asks to "synthesize audio from scripts",
  "generate speech from SRT", "convert narration to audio", "create lecture audio",
  "text to speech", "TTS", "語音合成", "把講稿轉成語音", "生成音檔",
  or has SRT narration scripts and wants audio files generated.
argument-hint: <path-to-srt-directory>
allowed-tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"]
---

# TTS Synthesis

Convert SRT narration scripts into speech audio using a local TTS model with optional voice cloning. Supports Chinese and English. Uses CosyVoice 3 via Swift CLI (CoreML priority) with a Python/mlx-audio fallback.

## Workflow Overview

Three phases:
1. **Setup & Validation** — Locate SRT files, check CLI build, detect voice reference, confirm settings
2. **Batch TTS Synthesis** — Spawn `tts-worker` agents in parallel batches to call the TTS CLI
3. **Audio Verification** — Cross-reference MP3 outputs against SRT inputs, report and offer retry

---

## Phase 1: Setup & Validation

### Locate SRT Files

- If an argument path is provided, use it as the slides directory
- Otherwise, look for a `srt/` subdirectory in the current working directory or the nearest parent with SRT files
- List all `srt/slide_*.srt` files; abort if none found
- Report the count to the user

### Check TTS CLI Build

Check whether the Swift CLI is already built:
```bash
ls lecture-notes/scripts/tts/.build/release/TTSInfer 2>/dev/null
```

If the binary does not exist, print the following instructions and wait for the user to confirm it is built before proceeding:

```
The TTS CLI has not been built yet. Please run:

  cd lecture-notes/scripts/tts
  swift build -c release

Then re-run this skill once the build completes.
```

**Do NOT proceed to Phase 2 until the binary exists.**

### Detect Voice Reference / Speaker Embedding

First, look for a **pre-computed speaker embedding** (preferred — avoids re-extracting each run):
1. `speaker_embedding.json` in the slides directory
2. `speaker_embedding.json` in the parent of the `srt/` directory
3. Explicit path passed as skill argument (if it ends with `.json`)

If a speaker embedding is found, use `--embedding <path>` (skip voice-ref detection).

Otherwise, look for a **voice reference audio file**:
1. Explicit path passed as skill argument (if it ends with `.wav` or `.mp3`)
2. `voice_ref.wav` in the slides directory
3. `voice_ref.wav` in the parent of the `srt/` directory

If neither is found, prompt the user:
> No speaker embedding or voice reference file found. You can:
> - Place `speaker_embedding.json` (pre-computed 192-dim CAM++ embedding) in the slides directory
> - Place `voice_ref.wav` (3–10 second mono audio) for voice cloning
> - Provide a path to either file now
> If you skip this, the default model voice will be used.

Accept their response (a path or "skip"/"no"). If skipped, proceed without `--voice-ref` or `--embedding`.

### Confirm Settings

Before spawning agents, display a summary and ask for confirmation:

```
TTS Synthesis Settings:
  SRT files:    <N> files in <path>/srt/
  Output:       <path>/audio/
  CLI:          lecture-notes/scripts/tts/.build/release/TTSInfer
  Backend:      coreml (default)
  Voice:        <embedding path> or <voice-ref path> or "default model voice"

Proceed? (yes/no)
```

**Do NOT proceed to Phase 2 without user confirmation.**

---

## Phase 2: Batch TTS Synthesis

### Create Output Directory

```bash
mkdir -p <slides-directory>/audio
```

### Batch Strategy

- Process one SRT file at a time
- For each SRT file, spawn a single `tts-worker` agent using the Agent tool
- Pass each agent:
  - The single SRT file path
  - The output directory path
  - The path to the TTS CLI binary
  - The speaker embedding path (if available), OR the voice reference path, OR "none"
- Wait for the agent to complete before spawning the next one

### Agent Invocation

For each SRT file, invoke `tts-worker` with a prompt like:

```
Process the following SRT file for TTS synthesis:

CLI: lecture-notes/scripts/tts/.build/release/TTSInfer
Output directory: <slides-directory>/audio/
Speaker embedding: <embedding_path or "none">
Voice reference: <voice_ref_path or "none">

SRT file:
- <slides-directory>/srt/slide_03.srt

Run:
  TTSInfer --srt <srt-path> --output <audio-dir>/slide_03.mp3 [--embedding <path.json>] [--voice-ref <path>] --backend coreml

Use --embedding if a speaker embedding path is provided.
Otherwise, use --voice-ref if a voice reference path is provided.
Omit both flags if neither is available.

Verify the output MP3 exists and is non-empty. Report success or failure.
```

### Sequential Execution

Launch one `tts-worker` agent at a time. Wait for each agent to finish before starting the next.

---

## Phase 3: Audio Verification

After all agents complete:

1. **Cross-reference**: List `audio/slide_*.mp3` and compare against `srt/slide_*.srt`
   - Any SRT without a corresponding MP3 is a failure
2. **Size check**: Each MP3 should be at least 10 KB (near-zero size indicates a synthesis failure)
3. **Duration estimate**: Parse each SRT's last timecode as the expected duration; warn if MP3 file size seems inconsistent (rough estimate: 128kbps MP3 ≈ 16 KB/sec)

Report results:
```
Audio Synthesis Results:
  ✓ slide_01.mp3  (2.3 MB, ~72s expected)
  ✓ slide_02.mp3  (1.8 MB, ~56s expected)
  ✗ slide_03.mp3  MISSING
  ...

Failed: 1 / N slides
```

Offer to retry failed slides:
> Would you like me to retry the failed slides? (yes/no)

If yes, spawn a new `tts-worker` for just the failed slides.

---

## Voice Cloning Convention

- **Pre-computed embedding (recommended)**: Place `speaker_embedding.json` (flat JSON array of 192 floats, from CAM++) in the slides directory. This is faster — no embedding extraction needed at synthesis time. Generate one with:
  ```
  TTSInfer --srt any.srt --output /dev/null --voice-ref voice_ref.wav --save-embedding speaker_embedding.json
  ```
- **Voice reference audio**: Place `voice_ref.wav` (3–10 seconds, mono, 16kHz or higher) in the slides directory. The embedding is extracted via the CAM++ encoder each time.
- Or pass an explicit path as the skill argument
- If neither is provided, the model's default voice is used (still high quality)

## Backend Selection

- Default: `--backend coreml` (fastest on Apple Silicon, uses Neural Engine)
- Fallback: `--backend mlx` (if CoreML model files are missing)
- Python fallback: if the Swift CLI fails to build, use `lecture-notes/scripts/tts/fallback/tts_infer.py` with `mlx-audio` (Qwen3-TTS 0.6B)
