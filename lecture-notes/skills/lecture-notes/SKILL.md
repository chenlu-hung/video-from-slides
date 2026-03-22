---
name: lecture-notes
description: This skill should be used when the user asks to "generate lecture scripts", "create slide narration", "make lecture notes from slides", "generate SRT from slides", "create teaching script", "convert slides to speech", "write narration for presentation", "製作講稿", "生成投影片講稿", "把投影片轉成講稿", or provides PDF/TeX slides and wants narration scripts generated. Provides a structured workflow for outline creation, batch script generation, and quality review.
argument-hint: <path-to-slides.pdf-or-.tex>
allowed-tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"]
---

# Lecture Notes Generator

Generate teaching lecture scripts from PDF or TeX slides, outputting each slide's narration as an individual SRT file.

## Workflow Overview

The process has three phases:
1. **Outline** — Read slides, estimate duration per slide, produce an editable `outline.md`
2. **Generate** — Spawn agents in batches of 1–5 slides to write SRT files
3. **Review** — Validate each SRT for content coverage, format, and timing

## Phase 1: Outline Creation

### Read the Slides

- For PDF files: use the Read tool (supports PDF with `pages` parameter; for large PDFs read in chunks of 20 pages)
- For TeX files: read as plain text with the Read tool

### Estimate Duration

Estimate speaking duration per slide based on content density:
- Title/section slides: 15–30 seconds
- Content slides with bullet points: 60–120 seconds
- Complex diagrams or equations: 90–180 seconds
- Adjust for language: Chinese text reads at ~250 characters/min, English at ~150 words/min

### Generate outline.md

Write the outline file in the same directory as the source slides. Use this format:

```markdown
# Lecture Notes Outline

- **Source**: [filename]
- **Total slides**: [N]
- **Estimated total duration**: [MM:SS]

## Slide 1: [Title]
- **Duration**: [MM:SS]
- **Key points**: [main ideas to cover]
- **Notes**: [additional context or speaking notes]

## Slide 2: [Title]
...
```

### Confirm with User

After generating the outline, inform the user:
1. The outline has been saved to `outline.md`
2. They can edit it directly to adjust key points, duration, or notes
3. Ask them to confirm when ready to proceed to script generation

**Do NOT proceed to Phase 2 until the user confirms.**

## Phase 2: Batch Script Generation

### Batch Strategy

- Group slides into batches of 1–5 consecutive slides
- For each batch, spawn a `script-generator` agent using the Agent tool
- Pass the agent:
  - The slide content (read from PDF/TeX)
  - The corresponding outline entries from `outline.md`
  - The output directory path
  - The slide range (e.g., slides 3–7)

### Agent Invocation

For each batch, invoke the agent with a prompt containing:
1. The outline entries for the assigned slides
2. The slide content (images or text)
3. Instructions to output one `.srt` file per slide, named `slide_XX.srt`
4. The output directory (same directory as the source slides, under a `srt/` subdirectory)

Create the `srt/` output directory before spawning agents:
```
mkdir -p <slides-directory>/srt
```

**Content guidelines to include in each agent prompt:**
- Detect language from slide content; narrate in the same language
- Use a conversational, pedagogical tone suitable for teaching
- When slides contain math or code, describe them verbally in the narration
- If a slide has very little content (e.g., a section divider), keep the narration brief

### SRT Format Specification

Each `slide_XX.srt` file:
- Starts timing from `00:00:00,000` (each slide is independent)
- Each subtitle block: sequence number, timecode line, text (max 2 lines per block, ~20 CJK chars or ~42 Latin chars per line), blank line separator
- Total duration of all blocks should match the duration specified in the outline

Example:
```
1
00:00:00,000 --> 00:00:04,500
歡迎來到今天的課程
我們將介紹機器學習的基礎概念

2
00:00:04,500 --> 00:00:09,000
首先讓我們看一下今天的大綱
```

### Parallel Execution

Launch multiple agents in parallel when possible. Each agent works independently on its batch, so there are no dependencies between batches.

## Phase 3: Quality Review

After all agents complete, spawn a `script-reviewer` agent to check:

1. **Content coverage** — each slide's SRT covers the key points from the outline
2. **SRT format** — valid SRT structure (sequence numbers, timecodes, text blocks)
3. **Timing accuracy** — total duration per slide within ±15% of the outline target
4. **Subtitle length** — each text block ≤ 2 lines, within character limits
5. **Language consistency** — matches the language of the slides

Present the review results to the user with any issues found, and offer to regenerate problematic slides.
