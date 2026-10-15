# CorrCUT Platform - Master Build Blueprint

## 1) Project Goal

Build a Windows-first, open-source desktop application that lets you:

- Load movie files with multi-track and multi-channel audio.
- Identify and edit profanity in dialogue (primarily center channel when available).
- Preview edits in sync with video before final export.
- Export a new media file with an additional "clean" audio track while preserving original video and tracks.
- Expand later into broader movie editing (subtitle, video trims/masks, and edit-driven packaging only where required — e.g. chapter sync after trims).

Primary outcome: a reliable, non-destructive, repeatable workflow for family-safe viewing.

---

## 2) Guiding Principles

- Non-destructive editing: keep source files immutable.
- Deterministic outputs at the **edit-decision level**: same project file + same inputs + same pinned tool versions = the same edit decisions and stream map. (Note: encoders are not guaranteed bit-for-bit identical across versions/thread counts, so determinism is defined at the decision/structure level, not the byte level.)
- Open-source-only stack for core functionality.
- Preview and export parity: what the user hears in preview should match the rendered result. This is achieved **structurally** by having preview and export consume the *same* generated filtergraph (see Section 5 and 5b), not by reconciling two separate engines after the fact.
- Incremental delivery: ship useful versions early, then add AI/advanced features.

---

## 3) End-State Product Vision

Desktop app that combines:

- Video player + waveform timeline + transcript lane.
- Edit events (mute/duck/beep/replace) as timeline objects.
- AI-assisted profanity detection and suggested censor windows.
- Batch processing for multiple episodes/movies.
- Multi-track export with original track preserved and clean track appended.

---

## 4) Recommended Technology Stack

## Core recommendation (balanced for speed + maintainability)

- Language: Python 3.12+
- UI Framework: PySide6 (Qt)
- **UI toolkit choice (LOCKED): QWidget + QGraphicsView, *not* QML/Qt Quick** (see 4a)
- **AI runs as a separate process/venv** (orchestrated by the JobRunner, 5d) so heavy ML deps never bloat or destabilize the core editor build
- Playback: libmpv via python bindings (best control/performance for desktop playback)
- Media processing / rendering: FFmpeg + FFprobe
- Muxing/track management: **mkvmerge (MKVToolNix) for final MKV muxing** (superior track-name/`default`/`forced`/language flag control); FFmpeg used for rendering the clean track only. MP4 is out of scope for v1 (poor multi-track/flag support).
- AI speech-to-text: faster-whisper, plus a **forced-alignment** step (WhisperX / wav2vec2) for accurate word boundaries
- Data model and persistence: JSON project files initially (with `project_version` + migration policy), SQLite in later phases
- Packaging: PyInstaller or Nuitka for Windows builds
- Test stack: pytest + snapshot tests + integration tests using sample media fixtures

### Licensing note (because "open-source-only" is a stated principle)

- Bundling FFmpeg (and certain encoders) triggers GPL / `nonfree` obligations. Choose an FFmpeg build whose enabled components are license-compatible with distribution, and ship the corresponding **written offer for source**. Avoid `--enable-nonfree` encoders in distributed builds.

## 4a) UI Toolkit Decision: QWidget + QGraphicsView (LOCKED)

The app is a desktop pro-tool: mostly traditional chrome (docked panels, menus, forms, lists, dialogs — see the Section 9 screen list) with **one** performance-sensitive custom surface (the timeline + waveform). That ratio drives the choice.

### Decision

- **App shell + all panels/dialogs:** QWidget, using `QDockWidget` for the editor layout (player / timeline / inspector / panels).
- **Video surface:** `QOpenGLWidget` driving the **libmpv render API**.
- **Timeline/waveform:** `QGraphicsView` scene for interactive items (playhead, mute/duck regions) + a custom-painted waveform layer fed by the **multi-resolution peak cache** (Phase 2). Add an OpenGL viewport only if profiling demands it.

### Why not QML/Qt Quick

- QML's main win (GPU scene graph) does **not** solve the one hard task for free: a dense waveform still requires a custom `QQuickItem`/scene-graph node, which is **less documented and more painful from PySide6** than a QWidget custom `paintEvent`.
- QML has **no native docking**, weaker native desktop chrome/keyboard behavior, and trickier libmpv embedding.
- QML adds a **second language (QML/JS) and the Python↔QML boundary** for a small team.
- QGraphicsView performance is adequate when drawing from the precomputed peak cache (not raw samples), with an OpenGL viewport as headroom.

### Escape hatch

- If QGraphicsView ever proves insufficient for the timeline alone, embed QML for **just that one panel** via `QQuickWidget` — without converting the whole app.

### Reconsider only if

- The UI becomes touch-first, heavily animated, or fully bespoke (every control custom-drawn). That is not this project.

## Alternative stack (if team prefers web tech)

- Language: TypeScript
- UI shell: Tauri (Rust core) or Electron
- Playback: mpv/libVLC wrapper
- Processing: FFmpeg subprocess orchestration
- AI: Whisper bindings/service subprocess

Python + PySide6 is the fastest path for this specific media workflow.

---

## 5) System Architecture (Target)

- `ui/` (QWidget + QGraphicsView; see 4a)
  - player panel (`QOpenGLWidget` + libmpv), timeline/waveform (`QGraphicsView` + peak cache), transcript, track inspector, export wizard — docked via `QDockWidget`
- `domain/`
  - project model, **Operation** model (see 5b), validation, **time model**
- `engine/`
  - media probe, channel extraction, **FiltergraphCompiler** (core), render pipeline, mux pipeline
- `engine/jobs/`
  - **JobRunner** (async process orchestration, progress, cancellation, temp lifecycle, crash recovery) — used by render, mux, transcription, waveform generation
- `ai/`
  - transcription, forced alignment, profanity candidate generation, confidence scoring
- `preview/`
  - real-time event scheduler, playback filter control (mpv `lavfi`), seek sync
- `io/`
  - project save/load, fingerprinting, cache, temp artifacts, logs
- `tests/`
  - unit, integration, golden output comparison

Key abstractions:

- **Operation** (see Section 14) is the canonical edit object — a generalization of the original "EditEvent" that can also express future video/subtitle/trim operations.
- **FiltergraphCompiler** is the single source of truth that turns Operations into an FFmpeg filtergraph string. Both preview and export consume its output, which makes preview/export parity *structural*.
- **JobRunner** is the single abstraction for every long-running external process (FFmpeg, mkvmerge, Whisper), providing progress, cancellation, and recovery from Phase 1 onward.

---

## 5b) Core Decision: The FiltergraphCompiler (LOCKED — commit-to-principle)

Preview (libmpv) and export (FFmpeg) are two different engines. **LOCKED principle:** never maintain separate preview vs export filter logic. Route both through one compiler:

```
Operations  ->  FiltergraphCompiler  ->  ffmpeg filter string
                                          |-> mpv `af` (live preview, via lavfi)
                                          |-> ffmpeg -filter_complex (export render)
```

- libmpv can run audio through `lavfi`/`af` using the same FFmpeg filter syntax used for export, so the *identical* generated graph drives both.
- Live editing changes the graph as the user edits; preview reloads the `af` chain (accept a brief, controlled glitch on reload rather than diverging logic).
- Export/render code must call the compiler; do not hand-author one-off FFmpeg filter strings in the render path.

### Build-now vs later

- **Now (Phase 2 onward):** introduce the `FiltergraphCompiler` module and API early. Even if preview is not wired yet, export-oriented compilation (mute/duck + `afade` micro-fades) should go through it from the first render path.
- **Phase 3:** wire the same compiled graph into mpv preview (`af` / lavfi); compiler-level parity tests only (same `FiltergraphCompiler` output, mpv label mapping).
- **Phase 4:** export render + full parity tests (preview and export consume identical compiler output for the same Operation set).
- **Later:** new Operation types add processors to the compiler; the preview/export split does not change.

---

## 5c) Core Decision: The Time Model (LOCKED — exact rational seconds)

Millisecond/float boundaries are not precise enough (48 kHz = 48 samples/ms) and float accumulates drift over a multi-hour film, causing click/fade inconsistencies and preview/export divergence.

### Decision

**Canonical internal time = exact rational seconds (`fractions.Fraction`).** A thin helper layer converts to *samples* (audio) and *frames* (video) only at the edges. This is chosen over raw integer-sample-at-a-fixed-rate because this project has multiple simultaneous clocks (e.g. 48 kHz main track, 44.1 kHz commentary, a re-encoded clean track, and video at 24000/1001 fps), and a single sample rate would force everything else off-grid and reintroduce drift.

### Why rational is optimal here

- **Superset that stays sample-exact.** A sample boundary at rate `R` is exactly `n/R` s; a video frame `k` at `p/q` fps is exactly `k·q/p` s. `Fraction` represents both grids losslessly; integer-samples-at-one-rate cannot.
- **Rate-agnostic.** Handles mixed sample rates and output re-encodes without rounding.
- **Matches FFmpeg.** FFmpeg models time as a rational `time_base` + integer PTS, so this maps cleanly onto the export compiler (5b).
- **No float accumulation.** `Fraction` arithmetic is exact; floats appear only at display/playback edges.

### Rules

- **Model layer uses `Fraction` only.** All Operation boundaries, fade lengths, and per-stream offsets are stored as exact rational seconds.
- **Quantize at compile time, per target stream.** When the FiltergraphCompiler (5b) emits filters for a specific audio stream, snap `start`/`end` to that stream's integer sample index so edits are sample-exact for the stream actually being edited, while the model stays rate-agnostic.
- **Per-stream offset** (also a `Fraction`) maps container `start_time` / encoder priming delay to project-zero. This is the mechanism that defeats A/V sync drift. Explicitly model and compensate for: **encoder/decoder priming delay** (AC3/AAC), container `start_time` offsets, edit lists / negative timestamps, and **VFR** video timing.
- **Floats are display/playback-only.** Convert to ms for the UI and to float seconds for mpv/Whisper; never feed those values back into the model. Whisper's float timestamps are quantized to the nearest sample on ingest and stored as `Fraction`.

### Canonical type

```python
from dataclasses import dataclass
from fractions import Fraction

@dataclass(frozen=True)
class TimePoint:
    seconds: Fraction  # canonical, exact

    def to_samples(self, rate: int) -> int:
        return round(self.seconds * rate)            # snap at the edge

    def to_frame(self, fps: Fraction) -> int:
        return int(self.seconds * fps)

    @classmethod
    def from_samples(cls, n: int, rate: int) -> "TimePoint":
        return cls(Fraction(n, rate))

    @classmethod
    def from_seconds_float(cls, x: float, rate: int) -> "TimePoint":
        return cls(Fraction(round(x * rate), rate))  # quantize on ingest
```

### Serialization

- Store every time value in the JSON project file exactly, as `"num/den"` (or a `{num, den}` pair), so it round-trips losslessly. **Never** store a float.

### Render-level caveat

- Some FFmpeg filters evaluate `enable='between(t,...)'` once per *audio frame* (~1024 samples ≈ 21 ms), not per sample, so a hard `volume` toggle can be coarse. Use **`afade`** (its `st`/`d`/`nb_samples` are sample-accurate) for the ramps / micro-fades. Keep the *model* sample-exact regardless, so the compiler can always emit sample-accurate arguments when the filter supports it.

This decision must land in Phase 0/1 because it touches every downstream component.

---

## 5d) Core Decision: Concurrency / Threading Model (LOCKED)

The UI thread must never block on FFmpeg, mkvmerge, Whisper, or waveform generation. Pattern is locked as follows.

### Pattern

- **External tools run as subprocesses, not in-process.** FFmpeg, mkvmerge, and faster-whisper are invoked as child processes managed by the **JobRunner** (`engine/jobs/`). This sidesteps the Python GIL and keeps heavy work out of the UI process's main thread.
- **One worker per job, owned by the JobRunner.** Each job is a `QObject` worker moved onto its own `QThread` (or a `QRunnable` on a bounded `QThreadPool`). The worker's only responsibilities are: spawn/supervise the subprocess, parse progress, and emit signals.
- **All UI updates happen on the main thread via Qt signals/slots.** Workers communicate *only* by emitting signals (`progress`, `log`, `finished`, `failed`, `cancelled`); they never touch widgets directly. Cross-thread delivery uses Qt's queued connections.
- **Cancellation is cooperative.** A cancel request terminates the child process (graceful kill → hard kill timeout) and the worker emits `cancelled`; the JobRunner guarantees temp-file cleanup on every terminal outcome (finished/failed/cancelled).
- **Bounded concurrency.** A single JobRunner-owned queue caps how many jobs run at once (default: 1 render/mux at a time; transcription may run alongside). Batch mode (Phase 6) reuses this same queue.

### Why this pattern

- Subprocess isolation avoids GIL contention and means a crashing/hung tool can't take down the UI.
- Qt signals/slots are the idiomatic, thread-safe way to marshal results back to the main thread in PySide6.
- A single owning queue gives one place for progress, cancellation, recovery, and (later) persistence of job state to SQLite.

### Build-now vs later

- **Now (Phase 0):** the minimal runner — spawn one subprocess, parse `-progress`, emit signals, cancel cleanly, clean up temps. Lock the worker/signal contract.
- **Later (Phase 6/9):** persisted job queue, retry/resume, crash recovery hardening — all additive on top of the same contract.

---

## 5e) Core Decision: Dialogue-Targeting per Layout (LOCKED — v1 defaults)

When censoring profanity, the app must know **which audio channels to edit** for each track. That depends on channel layout, not user preference alone.

### v1 defaults (auto-selected at probe time)

| Detected layout | Strategy key | Edit scope | Notes |
| --- | --- | --- | --- |
| 5.1 / 7.1 (discrete center) | `center_channel` | `channels: ["FC"]` only | Rebuild surround track with edited center; FL/FR/LFE/SL/SR unchanged on export. |
| Stereo | `full_mix` | whole stream (`channels: []`) | Dialogue is phantom-center in L+R; muting one side sounds hollow. Affects all center-panned content, not voice alone. |
| Unknown / odd layout | `full_mix` (fallback) | whole stream | Show warning in track inspector; user may override later. |

### UX / project file

- **Phase 1:** after probe, set and persist `dialogue_target_strategy` on each `tracks[]` entry; show it clearly in the track inspector (e.g. *"Editing: center channel (FC)"* vs *"Editing: full stereo mix"*).
- **Always warn** that dialogue can bleed into non-center channels (reverb, effects, shouts); center-only editing does not remove 100% of a swear in all mixes.

### Deferred (not v1)

- Center extraction or **Demucs**-style separation for stereo → optional processor later; UI must label as experimental/imperfect.
- Per-track user override in UI (reasonable Phase 2+ enhancement; defaults above are sufficient for v1).

### Downstream consumers

- **Operations** default `scope.channels` from the track's strategy when the user creates a censor region.
- **FiltergraphCompiler** and export path use the same scope (split/rejoin for `center_channel`; whole-stream filters for `full_mix`).

---

## 6) Phase-by-Phase Roadmap

## Phase 0 - Foundation and Environment

### Objective
Set up reproducible development baseline and verify media toolchain.

### Deliverables

- Repo scaffolding and module layout.
- Local tool detection (`ffmpeg`, `ffprobe`, `mkvmerge`) with **pinned version checks**.
- Sample media fixture set (short clips in stereo + 5.1, plus at least one lossy-surround codec such as AC-3/E-AC3).
- Logging, config management, and error-reporting baseline.
- **Time model module** (sample/rational time, delay/offset handling) — foundational (see 5c).
- **JobRunner skeleton** (implements the LOCKED concurrency pattern in 5d): subprocess-per-job workers on `QThread`/`QThreadPool`, progress parsing, cooperative cancellation, temp-file lifecycle, and structured error capture — communicating to the UI thread only via Qt signals/slots.

### Tooling

- Python, Poetry or uv for dependency management.
- FFmpeg static build for Windows (license-compatible component set; see Section 4 licensing note).
- Pre-commit hooks (black/ruff/isort, optional mypy).

### Exit Criteria

- App launches placeholder window.
- Toolchain self-check passes (tools found + versions pinned/verified).
- Can probe sample files and print stream/channel metadata.
- JobRunner can launch a sample FFmpeg job, report progress, and cancel cleanly.

---

## Phase 1 - Media Intake and Analysis

### Objective
Load media and expose complete metadata and channel layout.

### Features

- Open media file dialog.
- Stream inspector:
  - video streams
  - audio tracks (codec, language, channel layout)
  - subtitle tracks
- Channel map visualization for selected audio track.
- Probe cache (avoid repeated expensive scans).

### Implementation Notes

- Use `ffprobe -show_streams -show_format -print_format json`.
- Normalize channel naming (`FL`, `FR`, `FC`, `LFE`, `SL`, `SR`, etc.).
- Build robust parser to handle edge cases and unknown layouts.
- Apply the **LOCKED dialogue-targeting defaults** (5e): auto-assign `dialogue_target_strategy` per track (`center_channel` for 5.1/7.1, `full_mix` for stereo and unknown layouts).

### Exit Criteria

- User can inspect exact audio track and channel structure.
- The app reports, per track, which dialogue-targeting strategy applies.
- Metadata persists in project file.

---

## Phase 2 - Manual Editing v1 (No AI Yet)

### Objective
Allow manual censor interval editing on targeted dialogue channel.

### Features

- Timeline with playhead and zoom.
- Waveform rendering for selected channel or track.
- Add/edit/delete censor regions.
- Censor action types:
  - mute
  - duck to configurable dB
- Automatic micro-fades to prevent clicks.

### Implementation Notes

- Precompute waveform peaks into a **cached, multi-resolution (mipmap) per-channel peak store** so zoom does not require re-decoding a multi-hour file.
- Use the generalized **Operation** schema (see Section 14), not millisecond floats — time fields are sample/rational (see 5c).
- Introduce the **FiltergraphCompiler** module here (5b): compile Operations to FFmpeg filter strings even before live preview exists; do not embed filter logic in UI or ad-hoc export scripts.
- Keep all edits in project state only (no destructive source writes).

### Exit Criteria

- User can manually mark and audition censored intervals.
- Project saves and reloads all edit events accurately.

---

## Phase 3 - Preview Engine with Modifications

### Objective
Playback video with edits applied in real time.

### Features

- Synchronized video/audio preview.
- Real-time application of current timeline events.
- Scrubbing and frame stepping.
- A/B toggle between original and edited monitoring.

### Implementation Notes

- Keep preview low-latency and deterministic.
- Seek behavior: recompute active events at new playhead position.
- Wire mpv preview to the **same** compiled filtergraph from the FiltergraphCompiler (5b); reload `af` on edit changes.
- **Parity (Phase 3 scope only):** unit tests assert preview compilation uses the same `FiltergraphCompiler` output as export, with mpv label relabeling (`[aid]` / `[ao]`). No separate preview filter logic.
- **Parity (Phase 4):** once export render exists, add integration tests that preview and export consume identical compiler output for the same Operation set (not two reconciled code paths).

### Exit Criteria

- Editor preview reflects timeline edits without full re-render.
- No major sync drift in normal usage.

### Status

Phase 3 preview, Phase 4 export, and Phase 5 AI assist are **implemented**. Preview/export parity uses one `FiltergraphCompiler` graph; AI suggestions require human approval before becoming operations.

---

## Phase 4 - Export and Muxing v1

### Objective
Generate and append clean audio track while preserving source streams.

### Features

- Render edited dialogue/channel output from events.
- Reconstruct full multichannel track where only target channel differs.
- Append as new audio track to output container.
- Track naming and default track selection options.

### Export mode (audio-only — LOCKED for v1 through Phase 4)

When the project has **no video operations** (blur, masks, trims, etc.), export uses **append mode**:

- Source video and all original streams are **stream-copied** (`-c copy` / mkvmerge equivalent).
- Only the **clean audio track** is rendered and **appended** to the output MKV.
- Original file stays immutable; output is typically `*_clean.mkv` alongside the source.

### Implementation Notes

- Remux (`-c copy`) applies to **untouched** streams only (video, original audio, subtitles).
- The clean track is **not** a copy: editing a channel forces decode → edit → **re-encode**. Be explicit about this:
  - Lossless/proprietary sources (TrueHD/Atmos, DTS-HD MA, DTS, E-AC3) have no usable open-source lossless re-encoder. The clean track is therefore a **transcode** to a supported codec (see Section 8b matrix). The original lossless track stays untouched and selectable.
  - Default clean-track codec: **AC-3 640k for 5.1** (compatibility) or **FLAC/PCM** (archival). Make this a preset choice.
- Preserve loudness on the clean track by editing only target regions and avoiding any normalization filter unless explicitly enabled.
- Final muxing uses **mkvmerge** for correct track names / `default` / `forced` / language flags.
- Provide export presets:
  - `MKV + clean track appended (AC-3)`
  - `MKV + clean track appended (FLAC, archival)`

### Exit Criteria

- Output plays with original and clean audio selectable, with correct track name/flags.
- Video and all non-edited audio streams are not re-encoded.
- Export manifest records the clean-track codec and the fact that it is a transcode.

---

## Phase 5 - AI Assist (Detection and Suggestions)

### Objective
Reduce manual effort by proposing likely profanity intervals.

### Features

- Local transcription with word timestamps (faster-whisper).
- Profanity lexicon matching with stemming/variants.
- Suggested censor windows with confidence.
- "Apply all above threshold" and one-click review workflow.

### Implementation Notes

- Raw Whisper word timestamps can be off by 100s of ms. Run a **forced-alignment** pass (WhisperX / wav2vec2) to tighten word boundaries before generating censor windows; fall back to generous padding when alignment confidence is low.
- Keep human-in-the-loop approval required by default.
- Include configurable context padding (pre/post ms).
- Plan for hard cases: profanity in songs, heavy accents, mumbling, and multi-language audio.
- Maintain explainability: show transcript phrase that triggered suggestion.

### Exit Criteria

- User can auto-generate suggestions and review quickly.
- False positives manageable via confidence and review controls.

### Status

Phase 5 is **implemented**: faster-whisper transcription (optional `[ai]` extra or `CORRCUT_AI_PYTHON` subprocess), lexicon matching with padding, **AI Suggestions** dock, accept/reject/apply-all workflow. WhisperX forced alignment is deferred — low-confidence words get extra padding instead.

---

## Phase 6 - Quality and Batch

### Objective
Make workflow production-friendly for many files.

### Features

- Batch queue for multiple episodes/movies.
- Retry/resume failed jobs.
- Validation report (tracks preserved, timings, clipping checks).

### Implementation Notes

- Job queue with persisted state (SQLite).
- Structured run logs and export manifest.
- Optional GPU acceleration where feasible.

### Exit Criteria

- Can process multi-file queue reliably with repeatable results.

---

## Phase 7 - Subtitle Editing Expansion

### Objective
Extend beyond profanity censoring with a full subtitle workflow (ingest, preview, edit, export).

### Audio stance (LOCKED for Phase 7)

- **Use source audio as-is.** No in-app noise cleanup, EQ, compression, dynamic-range modes, or other mastering/DSP — censor edits (mute/duck/beep/gain on regions) only.
- **Deferred (not Phase 7):** optional handoff to an external wave editor (e.g. Audacity) for users who want deeper audio work. No bundled audio-enhancement pipeline in this phase.

### Subtitle workflow (LOCKED)

**Source priority**

1. **Embedded text subtitle** in the MKV (`subrip`, `ass`, `webvtt`, etc.) — extract to project on import/refresh.
2. **No text subtitle** — build cues from **cached Whisper** output (AI detect or explicit **Generate subtitles**; do not re-transcribe every export).
3. **PGS / bitmap subs** (`hdmv_pgs_subtitle`, `dvd_subtitle`, etc.) — **no OCR in v1**; use Whisper path only. Show clear UI copy when no text track exists.

**Project model (single source of truth for preview + export)**

- `subtitle_cues[]` on the project file — each cue is the unit of display, edit, and export:

```
SubtitleCue {
  id: string
  start, end          // rational seconds (5c)
  text: string         // generated/original line (embedded extract or Whisper); read-only in UI
  edited_text: string  // censored line shown in overlay and written to Edited track; user-editable
  source: "embedded" | "whisper" | "user"
  user_edited: bool    // when true, do not overwrite edited_text on auto-regenerate
}
```

- One **active source subtitle stream** per project (auto language match or user pick in Streams), same as today.
- Working interchange format: **temp SRT** from extract or generation — internal pipeline only; canonical state lives in `subtitle_cues[]`.

**Edited line generation (operation-aware)**

- `edited_text` defaults from `text` by masking tokens that overlap **enabled audio operations** (mute/duck/beep/gain regions), using the lexicon to identify profane tokens inside cues when needed.
- Replacement string configurable (default `***`), same family as today.
- **Regenerate** can refresh non-`user_edited` cues when operations change; per-cue **Reset** restores `edited_text` from the current auto-generated value and clears `user_edited`.

**Editing rules**

- User edits **`edited_text` only** — `text` (generated/original) is never edited in the UI.
- Subtitle cue edits participate in the **undo stack** (same as operations).
- **Preview:** Qt **UI overlay** on the video panel (not mpv subtitle rendering for project subs), driven by the same `subtitle_cues[]` renderer used at export — WYSIWYG for the Edited line.
- Optional: hide overlay / show Edited vs Generated (`text`) vs off; double-click overlay to edit `edited_text` when paused; do not use `sid=auto` on large files (known mpv freeze).

**Export (append mode, audio-only projects)**

Export wizard checkboxes (each **default on** when applicable):

| Checkbox | When shown | Behavior |
| --- | --- | --- |
| **Append Generated subtitle** | Whisper-sourced cues only | Append `Generated` text track from `text` |
| **Append Edited subtitle** | Always (if cues exist) | Append `Edited` text track from `edited_text` |

When **embedded text** subs exist: original subtitle stream is **stream-copied**; only **Append Edited** applies (no redundant Generated append). When **Whisper-sourced**: both tracks can append per checkboxes.

- **Edited** subtitle track uses the **same track name as clean audio** (default `Edited`) and the same **replace existing Edited track, if present** option.
- **Generated** track name default `Generated` (replace optional, same pattern as audio).
- Export writes temp SRT from project cues → mkvmerge append; must match overlay text exactly.

**Phasing**

- **7a:** Project `subtitle_cues[]`, extract/Whisper ingest, operation-aware `edited_text`, export append with checkboxes (overlay + cue list editor can follow).
- **7b:** UI overlay + cue list editor + undo + per-cue reset + read-only original line + regenerate-all + optional generated-line preview + inline overlay edit (paused).
- **Later:** timeline transcript lane, burn-in, forced-sub controls, sidecar `.srt` import, PGS OCR.

### Deferred (not Phase 7)

- Language-specific censor policies and per-language detection rule sets. v1 uses a single lexicon/matching flow with optional Whisper language hint and track language metadata only.
- Video edits (blur, masks, trims) and full edited export — **Phase 8**.

### Exit Criteria

- Subtitle ingest, overlay preview, cue editor, operation-aware `edited_text`, and append export (Generated/Edited) work end-to-end on representative fixtures.

### Status

Phase 7 subtitle workflow is **implemented**: `subtitle_cues[]` ingest (embedded + Whisper), operation-aware `edited_text`, Qt overlay preview (edited or generated lines, inline edit when paused), Subtitles panel (original line read-only, regenerate-all, undo), and append export with Generated/Edited checkboxes.

---

## Phase 8 - Video Editing Expansion

### Objective
Add video modification operations and the export path required when the timeline includes video edits.

### Features

- **Blur boxes** and **visual masks** on time-bounded regions (profanity-adjacent nudity/violence censoring, etc.).
- **Scene trims** — remove spans from the output timeline; retime downstream audio, subtitles, and chapters to match.
- **Full edited export** when any video operation is present (see **Export mode (when video is edited — LOCKED)** below).
- **Chapters (trim-coupled only):** when scene trims ship, drop or retime chapter markers that fall inside trimmed regions and shift later chapters by removed duration. No standalone chapter editor.

### Export mode (when video is edited — LOCKED)

When the project includes **any video operation**, export switches from append mode to **full edited export**:

- Write a **new self-contained MKV** (do **not** add a second video track to the source file).
- **Re-encode video** with edits applied (blur/masks/trims as implemented).
- **Mux audio, subtitles, and chapters** for the **same edited timeline** — clean audio and any retimed subs/chapters must match the new video duration.
- Original source file remains untouched; output is a separate file (e.g. `*_edited.mkv`).

Rationale: dual video tracks in one MKV only work when both videos share the exact same duration and timeline. Trims and most video edits change runtime; a separate file keeps sync straightforward and plays reliably in household players.

**Audio-only projects** (censor ops only) continue to use Phase 4 **append mode** — clean track appended, video stream-copied.

### Chapters (trim-coupled only)

- **In scope only when scene trims ship:** if the source has chapter markers, drop or retime chapters that fall inside trimmed-out regions and shift later chapters by the removed duration. No standalone chapter editor.
- **Out of scope for now:** general metadata work (container tags, stream reordering, playback-default editing beyond what Phase 4 export already sets on the clean track). Revisit only if a concrete need appears.

### Architecture Requirement

- Add processor plugin layer so each video feature is an independent module using the shared timeline/project model and `FiltergraphCompiler` (video filtergraph path alongside existing audio graph).

### Deferred (not Phase 8)

- Burn-in subtitles, general container metadata editor, GPU encode policy beyond “use if available.”

### Exit Criteria

- Video operations preview in mpv and export to a self-contained edited MKV with synced audio, subtitles, and trim-adjusted chapters on short fixtures.
- New video feature modules can be added without refactoring the core engine.

---

## Phase 9 - Hardening and Distribution

### Objective
Ship stable builds to end users.

### Features

- Installer and auto-update strategy.
- Crash-safe recovery for interrupted exports — *hardening* of the JobRunner recovery built in Phase 0, not a from-scratch feature.
- Telemetry (opt-in, local-first if preferred).
- End-user docs and troubleshooting assistant.

### Exit Criteria

- Signed Windows release with reproducible build steps.
- Basic support docs + known limitations catalog.

---

## 7) Data Model (Initial)

Use JSON for v1 project files:

- `project_version` (drives a defined **migration policy**; old project files must remain loadable via forward migrations)
- `source_files[]` (path, **fingerprint**, probe metadata)
  - fingerprint = file size + hash of first/last N MB + key probe metadata. Do **not** full-hash multi-GB movies.
- `tracks[]` (stream index, layout, language, codec, `dialogue_target_strategy`: `center_channel` | `full_mix`)
- `operations[]` (canonical edit objects — see Section 14; replaces the older "events" name)
- `ai_suggestions[]`
- `subtitle_cues[]` (Phase 7 — see Subtitle workflow; `text` + `edited_text`, rational times)
- `subtitle_settings` (optional: active stream index, replacement string, export flags)
- `exports[]` (history, output paths, manifests, clean-track codec)

Move to SQLite when batch/history/concurrency needs grow. The `project_version` + migration policy makes that transition safe.

---

## 8) Recommended FFmpeg Strategy

- Probe with FFprobe JSON only.
- Extract target channel for focused editing as needed.
- Rebuild full channel layout while replacing edited channel path.
- Use fades around interval boundaries to avoid clicks.
- Remux all untouched streams with copy mode.

Guardrails:

- Never overwrite source file.
- Validate channel order before and after render.
- Provide loudness and clipping checks post-render.

---

## 8b) Codec / Container Support Matrix (decide before Phase 4)

The clean track is always a re-encode. This matrix defines what the clean track becomes per source. Original tracks are always preserved untouched regardless of source codec.

| Source audio codec | Re-encode losslessly w/ open source? | v1 clean-track output |
| --- | --- | --- |
| PCM / FLAC | Yes (lossless) | FLAC or PCM (lossless) |
| AC-3 (Dolby Digital) | Re-encode lossy | AC-3 640k (transcode) |
| E-AC3 (DD+) | Weak/limited encoder | AC-3 640k or Opus (transcode) |
| AAC | Re-encode lossy | AC-3 or Opus (transcode) |
| DTS | No usable encoder | AC-3 640k or FLAC (transcode/decode) |
| DTS-HD MA | No (lossless core not re-encodable) | FLAC (from decoded PCM) or AC-3 |
| TrueHD / Atmos | No usable encoder; object audio lost | FLAC (from decoded core) or AC-3 |

Container decisions:

- **LOCKED: MKV is the only output container for v1** (best multi-track + flag support).
- **MP4 out of scope for v1.**
- Final muxing via **mkvmerge**; FFmpeg renders the clean track only.

Note on object-based audio (Atmos): editing collapses it to a channel bed; the original Atmos track is preserved but the clean track is bed-only. Surface this clearly in the UI.

---

## 9) UX Blueprint (MVP Screens)

- Library/Open screen
- Editor screen (video player, waveform, transcript, event list)
- Track inspector (stream and channel details)
- AI suggestions panel (accept/reject/adjust)
- Export wizard (preset, track naming, destination)
- Job monitor (current run + logs)

---

## 10) Testing Strategy

- Unit tests:
  - event math, fade generation, channel mapping
- Integration tests:
  - end-to-end render+remux on short fixtures
- Golden tests:
  - compare output **stream maps and properties** (track count, codecs, layouts, flags, durations) — **not raw bytes**, since encoders are not bit-deterministic across versions/threads. Pin the FFmpeg version for these tests.
- Parity tests (see Section 5b):
  - **Phase 3 (done):** compiler-level — preview `lavfi` path uses the same compiled graph as export, with mpv stream-label mapping only.
  - **Phase 4:** integration-level — preview and export render consume identical compiler output for a given Operation set.
- Regression packs:
  - stereo, 5.1, 7.1, odd channel layouts, multiple audio tracks, and at least one lossy-surround source (AC-3/DTS) to exercise the transcode path.

Success metrics:

- Zero source mutation incidents.
- Stream preservation accuracy.
- Preview/export parity score.
- Profanity detection precision/recall targets (after AI phase).

---

## 11) Risks and Mitigations

- Channel layout edge cases -> robust mapping + explicit warnings.
- AI false positives/negatives -> review-required workflow + thresholds.
- Playback sync issues -> dedicated sync tests + simplified v1 preview rules.
- Toolchain differences across systems -> startup diagnostics and health checks.
- Performance on long movies -> caching, proxy assets, chunked processing.

---

## 12) Suggested 17-Week Execution Plan

- Weeks 1-2: Phases 0-1
- Weeks 3-5: Phase 2
- Weeks 6-7: Phase 3
- Weeks 8-9: Phase 4
- Weeks 10-12: Phase 5
- Weeks 13-14: Phase 6
- Week 15: Phase 7 (subtitles)
- Week 16: Phase 8 (video editing starter)
- Week 17: Phase 9 baseline release prep

Adjust based on solo vs team development pace.

---

## 13) Architectural Decisions to Lock Before Coding

These are cheap to decide now and expensive to retrofit. Each is reflected in the sections above.

1. **Time model** = LOCKED: exact rational seconds (`Fraction`), quantized to the sample grid at compile time; floats are display-only (5c).
2. **FiltergraphCompiler** = LOCKED commit-to-principle: one compiler, shared by preview (mpv `lavfi`) and export; no duplicate filter logic. Module/API from Phase 2; mpv wiring Phase 3; parity tests Phase 4 (5b).
3. **Clean track is a re-encode**; commit to the codec/container matrix. **LOCKED: MKV-only container for v1** (8b).
4. **Operation model** generalizes "EditEvent" so video/subtitle/trim ops fit later without a core refactor. **LOCKED: no trims in v1** (localized filters only) (Section 14).
5. **JobRunner + Qt threading model** exist from Phase 0. **LOCKED concurrency pattern**: subprocess-per-job workers, results to UI via Qt signals/slots only, cooperative cancellation, bounded queue (5d).
6. **Dialogue-targeting per layout** = LOCKED v1 defaults: 5.1/7.1 → `center_channel` (`FC`); stereo/unknown → `full_mix`; persist on track + show in inspector; bleed warning (5e).
7. **Project fingerprint + schema migration policy** from the first project file. **LOCKED: `project_version` is written into every project file from day one** (Section 7).
8. **Export strategy by edit type** = LOCKED: **no video ops** → append clean audio track to a remuxed MKV (Phase 4); **any video op** → new self-contained edited MKV with re-encoded video and timeline-matched audio/subs/chapters (Phase 8). No dual-video-track-in-one-file approach.
9. **Subtitle workflow** = LOCKED (Phase 7): embedded text first, else cached Whisper; `subtitle_cues[]` with read-only `text` and user-editable `edited_text` (operation-aware, per-cue reset); UI overlay preview; export **Generated**/**Edited** append checkboxes (default on); **Edited** subtitle track name matches clean audio (`Edited`); no PGS OCR in v1. See Phase 7 Subtitle workflow.

---

## 14) Operation Model (Canonical Edit Object)

Generalize the original profanity-only "EditEvent" into a single `Operation` type so future video/subtitle/trim work reuses the same model, project file, and pipeline.

```
Operation {
  id: string
  type: "gain" | "mute" | "duck" | "beep" | "blur" | "trim" | "subtitle" | ...
  scope: {
    stream: int            // target audio/video stream
    channels: [string]     // e.g. ["FC"] for center; empty = whole stream
    time_range: { start, end }   // in samples/rational time (see 5c)
  }
  params: { ... }          // type-specific (e.g. duck_db, beep_freq, fade_in, fade_out)
  order: int               // precedence when operations overlap
  enabled: bool
  source: "user" | "ai"    // provenance for review workflows
}
```

Notes and constraints:

- **Audio-only is implemented in v1**, but the envelope already supports other scopes so Phase 7 (subtitles) and Phase 8 (video) are additive, not a rewrite.
- **Trim/time-remap is special**: it changes the mapping between source time and output time. **LOCKED v1 stance: no trims** — v1 uses localized filters only (mute/duck/beep/gain on time ranges), which keeps the time model (5c) free of segment remapping. `trim` remains a reserved `type` for a future phase; if introduced, the time model must add a source→output segment mapping at that time. When trims are implemented, chapter markers (if present) must be adjusted only as needed — drop chapters in removed spans and shift the timestamps of chapters after each cut; no general-purpose chapter/metadata UI.
- Each `type` maps to a processor in the FiltergraphCompiler; new features = new processors, no core changes.
- `order` + overlap rules must be defined (e.g. later operation wins, or gains compose) and unit-tested.

---

## 15) Immediate Next Steps

1. Confirm final stack choice (Python/PySide6 recommended).
2. Define v1 scope boundary (through Phase 4). Trim stance is LOCKED: no trims in v1 (Section 14).
3. Build repo skeleton, toolchain verifier, **time model**, and **JobRunner skeleton**.
4. Implement probe parser + project schema (with fingerprint + `project_version`) first.
5. Introduce the **FiltergraphCompiler** module in Phase 2; wire preview in Phase 3; add parity tests in Phase 4 (5b).
6. Deliver manual editing MVP before AI.

This sequence minimizes risk and gives a usable product quickly.

