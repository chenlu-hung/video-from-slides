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

### Detect Overlay Groups

Beamer (and similar) decks export **overlays** — one logical slide revealed step by step — as **several consecutive PDF pages** that share the same frame and build content up incrementally (`\pause`, `\onslide`, `\uncover`, `\only`, `\alt`). A 69-page PDF may really be ~18 logical slides.

As you read the pages, group consecutive pages that are clearly **the same frame building up** into one **logical slide**. Judge by eye, not by a fixed rule — this handles incremental adds, content swaps, and image reveals alike. Signals:

- Identical frame title and footer; body grows or changes while the rest stays fixed.
- Page _N_'s visible content is (mostly) contained in page _N+1_'s, or only a localized region changes.
- A genuinely new frame title / wholly different layout starts a **new** logical slide.

For each logical slide, also decide **which pages get narrated**:

- A page is a **narrate page** if it reveals content worth speaking to.
- A page is **merged** (pure highlight, recolor, a tiny tweak, removing a "?") if it adds nothing narratable. Merged pages get **no** SRT / audio / video segment of their own; in the final video the build-up jumps straight from the previous narrate page to the next one.
- The **first** page of every group is always a narrate page (it introduces the slide).

A normal, non-overlay deck simply yields one logical slide per page (every group has a single narrate page) — the rest of the pipeline then behaves exactly as before.

### Estimate Duration

Estimate speaking duration **per logical slide** based on content density:
- Title/section slides: 15–30 seconds
- Content slides with bullet points: 60–120 seconds
- Complex diagrams or equations: 90–180 seconds
- Adjust for language: Chinese text reads at ~250 characters/min, English at ~150 words/min

For a multi-step logical slide, the duration is the sum over its narrate pages; estimate roughly per step so the build-up paces naturally.

### Generate outline.md

Write the outline file in the same directory as the source slides. `outline.md` is the **single source of truth** for the page→logical-slide grouping — the `video-from-slides` skill parses the same file. Use this format:

```markdown
# Lecture Notes Outline

- **Source**: [filename]
- **Total PDF pages**: [P]
- **Logical slides**: [L]
- **Estimated total duration**: [MM:SS]

<!-- OVERLAY-GROUPS:START -->
| logical | pages | narrate_pages | title |
|---------|-------|---------------|-------|
| 1 | 1 | 1 | Title |
| 2 | 2-8 | 2,3,5,6,8 | Integration-by-parts: A glance |
| 3 | 9-11 | 9,10,11 | The IBP formula |
<!-- OVERLAY-GROUPS:END -->

## Logical Slide 1 (page 1): Title
- **Duration**: 0:20
- **Key points**: [main ideas to cover]
- **Notes**: [additional context or speaking notes]

## Logical Slide 2 (pages 2–8): Integration-by-parts: A glance
- **Overlay build-up**: 7 PDF pages; narrate on 2, 3, 5, 6, 8 (pages 4 and 7 are pure highlights, merged into the prior step)
- **Duration**: 1:40
- **Step narration**:
  - **page 2**: introduce the three indefinite integrals; the third has no obvious antiderivative
  - **page 3**: rewrite the third integral, splitting off the x factor
  - **page 5**: bring in the product-rule expansion
  - **page 6**: turn it into an equation
  - **page 8**: isolate the target integral — this is the IBP idea in miniature
- **Notes**: [additional context]
...
```

**The `OVERLAY-GROUPS` table is mandatory and machine-parsed** — keep it exactly between the two HTML-comment markers:

- `logical` — 1-based logical-slide index, contiguous.
- `pages` — the PDF page span in this group, as a single page (`1`) or an inclusive range (`2-8`).
- `narrate_pages` — comma-separated PDF page numbers within `pages` that each get their own SRT/audio/video segment. Pages in `pages` but not in `narrate_pages` are **merged** (no files). The first page of the group must appear here.
- `title` — short frame title (no `|` characters).

For a non-overlay deck, every row is `| n | n | n | … |` (one page = one logical slide = one narrate page).

Provide one `## Logical Slide L (pages A–B): Title` section per logical slide. Single-page slides use the simple `Key points` form; multi-step slides use the `Overlay build-up` + `Step narration` form (one bullet per **narrate** page) so the script-generator knows what each reveal step should add.

### Confirm with User

After generating the outline, inform the user:
1. The outline has been saved to `outline.md`
2. They can edit it directly to adjust key points, duration, or notes — **including the `OVERLAY-GROUPS` table** if any overlay frames were grouped wrongly (e.g. split a group, merge two, or change which pages are narrated)
3. If overlay groups were detected, state it plainly: "Detected L logical slides across P PDF pages (N overlay build-ups)" so the user can sanity-check the collapse before any audio is generated
4. Ask them to confirm when ready to proceed to script generation

**Do NOT proceed to Phase 2 until the user confirms.**

## Phase 2: Batch Script Generation

### Batch Strategy

Batch by **logical slide**, never by raw PDF page — a multi-step overlay group must stay whole in one batch so the agent can write continuous build-up narration across its steps.

- Group **1–5 consecutive logical slides** into each batch (use a smaller batch, even a single logical slide, if one group has many narrate pages).
- For each batch, spawn a `script-generator` agent using the Agent tool.
- Pass the agent:
  - The slide content (read from PDF/TeX) for **every page in the batch's groups**, including merged pages — the agent needs to see the full build-up to write good deltas
  - The corresponding `## Logical Slide` outline sections + the relevant `OVERLAY-GROUPS` rows
  - The output directory path
  - The logical-slide range and, for each, its `narrate_pages`

### Agent Invocation

For each batch, invoke the agent with a prompt containing:
1. The outline sections for the assigned logical slides, and their `OVERLAY-GROUPS` rows (`pages` + `narrate_pages`)
2. The slide content (images or text) for all pages in those groups
3. Instructions to output **one `.srt` file per narrate page**, named `slide_XX.srt` (zero-padded **PDF page number**), and to write **no file** for merged pages
4. For multi-step groups: write **delta narration** — the first narrate page introduces the slide; each later narrate page speaks only the newly-revealed content and flows on from the previous step (no re-introducing the slide). See the `script-generator` agent for the full contract.
5. The output directory (same directory as the source slides, under a `srt/` subdirectory)

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
- **Per-block timecodes reflect natural speech pace** (CJK at ~250 chars/min, English at ~150 wpm). Do **not** evenly space blocks across the outline's target duration — that produced the broken 15-seconds-per-block scripts the new TTS pipeline can't realign. If the natural total drifts from the outline's target, adjust narration text volume, not timecodes.

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

1. **Content coverage** — for a single-page slide, its SRT covers the outline key points. For a multi-step overlay group, coverage is judged **across the group's narrate pages together** (the build-up as a whole), not page by page — later narrate pages are *expected* to be short deltas, so do **not** flag them for "missing introduction" or "doesn't cover the whole slide."
2. **Group completeness** — every `narrate_page` in the `OVERLAY-GROUPS` table has exactly one `slide_XX.srt`, and merged pages have none. No stray SRTs for merged or non-existent pages.
3. **SRT format** — valid SRT structure (sequence numbers, timecodes, text blocks)
4. **Per-block timing** — each block's `end - start` is within ±25% of the speech-rate estimate from its text length (catches the "evenly spaced" bug)
5. **Slide total** — the logical-slide total (summed over its narrate pages) within ±15% of the outline target indicates well-sized content
6. **Subtitle length** — each text block ≤ 2 lines, within character limits
7. **Language consistency** — matches the language of the slides

Present the review results to the user with any issues found, and offer to regenerate problematic slides (regenerate a whole logical slide at once, not a single mid-group page).
