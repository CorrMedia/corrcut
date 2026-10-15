"""In-memory project model."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from corrcut.config import PROJECT_FILE_VERSION
from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.audio_language import (
    normalize_audio_language,
    resolve_track_language,
)
from corrcut.domain.channel_layout import channels_for_layout
from corrcut.domain.dialogue_target import (
    DialogueTargetStrategy,
    default_operation_channels,
)
from corrcut.domain.export import ExportRecord
from corrcut.domain.operation import (
    ITEM_LEVEL_STREAM_INDEX,
    Operation,
    OperationScope,
    is_audio_operation,
)
from corrcut.domain.scene_cuts import SceneCutCache
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.subtitle_settings import SubtitleSettings
from corrcut.domain.timeline_flag import TimelineFlag
from corrcut.domain.video_matte import VideoMatteCache
from corrcut.domain.whisper_cache import WhisperCache

if TYPE_CHECKING:
    from corrcut.engine.probe import MediaProbeResult


@dataclass
class SourceFileRecord:
    path: Path
    fingerprint: dict[str, object]
    probe: dict[str, object]

    def to_json(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "fingerprint": self.fingerprint,
            "probe": self.probe,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> SourceFileRecord:
        return cls(
            path=Path(str(data["path"])),
            fingerprint=dict(data.get("fingerprint", {})),
            probe=dict(data.get("probe", {})),
        )


@dataclass
class TrackRecord:
    stream_index: int
    codec: str
    channel_layout: str | None
    channels: int
    sample_rate: int | None
    language: str | None
    dialogue_target_strategy: DialogueTargetStrategy
    title: str | None = None
    is_default: bool = False

    def list_label(self) -> str:
        layout = self.channel_layout or f"{self.channels} ch"
        label = f"#{self.stream_index} {self.codec} {layout}"
        if self.language:
            label += f" ({self.language})"
        if self.title:
            label += f" — {self.title}"
        if self.is_default:
            label += " [default]"
        return label

    def to_json(self) -> dict[str, object]:
        return {
            "stream_index": self.stream_index,
            "codec": self.codec,
            "channel_layout": self.channel_layout,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "language": self.language,
            "title": self.title,
            "is_default": self.is_default,
            "dialogue_target_strategy": self.dialogue_target_strategy.value,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> TrackRecord:
        return cls(
            stream_index=int(data["stream_index"]),
            codec=str(data["codec"]),
            channel_layout=data.get("channel_layout"),
            channels=int(data["channels"]),
            sample_rate=int(data["sample_rate"]) if data.get("sample_rate") else None,
            language=data.get("language"),
            dialogue_target_strategy=DialogueTargetStrategy(
                str(data["dialogue_target_strategy"])
            ),
            title=data.get("title"),
            is_default=bool(data.get("is_default", False)),
        )

    @classmethod
    def from_audio_stream(cls, stream: object) -> TrackRecord:
        from corrcut.engine.probe import AudioStreamInfo

        assert isinstance(stream, AudioStreamInfo)
        return cls(
            stream_index=stream.index,
            codec=stream.codec,
            channel_layout=stream.channel_layout,
            channels=stream.channels,
            sample_rate=stream.sample_rate,
            language=stream.language,
            dialogue_target_strategy=stream.dialogue_target_strategy,
            title=stream.title,
            is_default=stream.is_default,
        )


@dataclass
class Project:
    project_version: int = PROJECT_FILE_VERSION
    source: SourceFileRecord | None = None
    tracks: list[TrackRecord] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    timeline_flags: list[TimelineFlag] = field(default_factory=list)
    exports: list[ExportRecord] = field(default_factory=list)
    ai_suggestions: list[AiSuggestion] = field(default_factory=list)
    subtitle_cue_sets: dict[str, list[SubtitleCue]] = field(default_factory=dict)
    active_subtitle_source: SubtitleSource | None = None
    subtitle_settings: SubtitleSettings = field(default_factory=SubtitleSettings)
    whisper_cache: WhisperCache | None = None
    scene_cuts: SceneCutCache | None = None
    preserve_letterbox_on_zoom: bool = True
    video_matte: VideoMatteCache | None = None
    selected_audio_stream_index: int | None = None
    audio_language_overrides: dict[str, str] = field(default_factory=dict)
    project_path: Path | None = None

    def selected_track(self) -> TrackRecord | None:
        if self.selected_audio_stream_index is None:
            return None
        for track in self.tracks:
            if track.stream_index == self.selected_audio_stream_index:
                return track
        return None

    @property
    def subtitle_cues(self) -> list[SubtitleCue]:
        from corrcut.engine.subtitles.cue_sets import active_cues

        return active_cues(self)

    @subtitle_cues.setter
    def subtitle_cues(self, cues: list[SubtitleCue]) -> None:
        from corrcut.engine.subtitles.cue_sets import set_active_cues

        set_active_cues(self, cues)

    def subtitle_source_kind(self) -> SubtitleSource | None:
        if self.active_subtitle_source is not None:
            return self.active_subtitle_source
        return None

    def has_whisper_subtitles(self) -> bool:
        from corrcut.engine.subtitles.cue_sets import cues_for_source

        return bool(cues_for_source(self, SubtitleSource.WHISPER))

    def has_ocr_subtitles(self) -> bool:
        from corrcut.engine.subtitles.cue_sets import cues_for_source

        return bool(cues_for_source(self, SubtitleSource.OCR))

    def has_generated_subtitle_track(self) -> bool:
        return self.has_whisper_subtitles() or self.has_ocr_subtitles()

    def has_embedded_subtitles(self) -> bool:
        from corrcut.engine.subtitles.cue_sets import cues_for_source

        return bool(cues_for_source(self, SubtitleSource.EMBEDDED))

    def available_subtitle_sources(self) -> list[SubtitleSource]:
        from corrcut.engine.subtitles.cue_sets import available_sources

        return available_sources(self)

    def track_language(self, track: TrackRecord) -> tuple[str, bool]:
        """Resolved corr language for *track* and whether English was assumed."""
        override = self.audio_language_overrides.get(str(track.stream_index))
        return resolve_track_language(track.language, track.title, override=override)

    def operation_language(self, op: Operation) -> str:
        """Language that groups this audio edit with every track that shares it."""
        tagged = normalize_audio_language(op.language)
        if tagged is not None:
            return tagged
        for track in self.tracks:
            if track.stream_index == op.scope.stream_index:
                return self.track_language(track)[0]
        return resolve_track_language(None)[0]

    def audio_op_matches_track(self, op: Operation, track: TrackRecord) -> bool:
        if op.is_item_level or not is_audio_operation(op.type):
            return False
        return self.operation_language(op) == self.track_language(track)[0]

    def set_track_language(self, stream_index: int, language: str | None) -> None:
        """Pin *stream_index* to *language*, or clear the pin when *language* is None.

        Edits authored on that stream follow the new tag. Edits authored on
        another stream keep theirs.
        """
        track = next((item for item in self.tracks if item.stream_index == stream_index), None)
        if track is None:
            return
        old, _assumed = self.track_language(track)
        key = str(stream_index)
        if language is None:
            self.audio_language_overrides.pop(key, None)
        else:
            code = normalize_audio_language(language) or resolve_track_language(None)[0]
            self.audio_language_overrides[key] = code
        new, _assumed = self.track_language(track)
        if old == new:
            return
        for op in self.operations:
            if not is_audio_operation(op.type) or op.scope.stream_index != stream_index:
                continue
            current = normalize_audio_language(op.language) or old
            if current == old:
                op.language = new

    def operations_for_selected_track(self) -> list[Operation]:
        """Enabled audio ops for the selected track's language, channels remapped."""
        track = self.selected_track()
        if track is None:
            idx = self.selected_audio_stream_index
            if idx is None:
                return []
            return [
                o
                for o in self.operations
                if o.enabled and o.scope.stream_index == idx and not o.is_item_level
            ]
        out: list[Operation] = []
        for op in self.operations:
            if not op.enabled or not self.audio_op_matches_track(op, track):
                continue
            mapped = channels_for_layout(op.scope.channels, track.channel_layout, track.channels)
            if mapped is None:
                continue
            out.append(
                replace(
                    op,
                    scope=OperationScope(stream_index=track.stream_index, channels=list(mapped)),
                )
            )
        return out

    def timeline_operations(self) -> list[Operation]:
        """Language-matched audio ops plus item-level skip/video ops."""
        track = self.selected_track()
        idx = self.selected_audio_stream_index
        rows: list[Operation] = []
        for op in self.operations:
            if op.scope.stream_index == ITEM_LEVEL_STREAM_INDEX or op.is_item_level or track is not None and self.audio_op_matches_track(op, track) or (
                track is None
                and idx is not None
                and op.scope.stream_index == idx
                and not op.is_item_level
            ):
                rows.append(op)
        return rows

    def select_track_for_audio_edits(self) -> None:
        """When imported audio edits share one language, hear the best matching track."""
        codes = [
            self.operation_language(op)
            for op in self.operations
            if is_audio_operation(op.type)
        ]
        if len(set(codes)) != 1:
            return
        match = track_for_edit_language(self.tracks, codes[0])
        if match is not None:
            self.selected_audio_stream_index = match.stream_index

    def suggestions_for_selected_track(
        self,
        *,
        pending_only: bool = False,
    ) -> list[AiSuggestion]:
        idx = self.selected_audio_stream_index
        if idx is None:
            return []
        rows = [s for s in self.ai_suggestions if s.stream_index == idx]
        if pending_only:
            rows = [s for s in rows if s.status == SuggestionStatus.PENDING]
        return sorted(rows, key=lambda s: s.time_range.start.seconds)

    def audio_ordinal_for_stream(self, stream_index: int) -> int:
        for i, track in enumerate(self.tracks):
            if track.stream_index == stream_index:
                return i
        return 0

    def default_channels_for_selected(self) -> list[str]:
        track = self.selected_track()
        if track is None:
            return []
        return self.default_channels_for_stream(track.stream_index)

    def default_channels_for_stream(self, stream_index: int) -> list[str]:
        for track in self.tracks:
            if track.stream_index == stream_index:
                return default_operation_channels(track.dialogue_target_strategy)
        return []

    @classmethod
    def from_probe(cls, probe: MediaProbeResult, fingerprint: dict[str, object]) -> Project:
        source = SourceFileRecord(
            path=probe.path.resolve(),
            fingerprint=fingerprint,
            probe=probe_to_dict(probe),
        )
        tracks = [TrackRecord.from_audio_stream(a) for a in probe.audio_streams]
        selected = _default_audio_stream_index(tracks)
        return cls(source=source, tracks=tracks, selected_audio_stream_index=selected)

    def refresh_probe_metadata(self, probe: MediaProbeResult) -> None:
        """Update cached probe and rebuild tracks from the live file.

        Relink / in-place replace often changes codec, channel layout, or stream
        indices. Keep the selected stream while it still exists. Pick a new
        default only when that stream is gone.
        """
        if self.source is None:
            return
        self.source.probe = probe_to_dict(probe)
        if not probe.audio_streams:
            return
        old_selected = self.selected_audio_stream_index
        self.tracks = [TrackRecord.from_audio_stream(stream) for stream in probe.audio_streams]
        indices = {track.stream_index for track in self.tracks}
        if old_selected not in indices:
            self.selected_audio_stream_index = _default_audio_stream_index(self.tracks)

    def to_json(self) -> dict[str, object]:
        return {
            "project_version": self.project_version,
            "source_files": [self.source.to_json()] if self.source else [],
            "tracks": [t.to_json() for t in self.tracks],
            "operations": [o.to_json() for o in self.operations],
            "timeline_flags": [f.to_json() for f in self.timeline_flags],
            "exports": [e.to_json() for e in self.exports],
            "ai_suggestions": [s.to_json() for s in self.ai_suggestions],
            "subtitle_cue_sets": {
                source: [cue.to_json() for cue in cues]
                for source, cues in self.subtitle_cue_sets.items()
                if cues
            },
            "active_subtitle_source": (
                self.active_subtitle_source.value
                if self.active_subtitle_source is not None
                else None
            ),
            "subtitle_cues": [c.to_json() for c in self.subtitle_cues],
            "subtitle_settings": self.subtitle_settings.to_json(),
            "whisper_cache": (
                self.whisper_cache.to_json() if self.whisper_cache is not None else None
            ),
            "scene_cuts": (
                self.scene_cuts.to_json() if self.scene_cuts is not None else None
            ),
            "preserve_letterbox_on_zoom": bool(self.preserve_letterbox_on_zoom),
            "video_matte": (
                self.video_matte.to_json() if self.video_matte is not None else None
            ),
            "selected_audio_stream_index": self.selected_audio_stream_index,
            "audio_language_overrides": dict(self.audio_language_overrides),
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Project:
        sources = data.get("source_files", [])
        source = SourceFileRecord.from_json(sources[0]) if sources else None
        tracks = [TrackRecord.from_json(t) for t in data.get("tracks", [])]
        operations = [Operation.from_json(o) for o in data.get("operations", [])]
        timeline_flags: list[TimelineFlag] = []
        for row in data.get("timeline_flags", []) or []:
            if isinstance(row, dict):
                try:
                    timeline_flags.append(TimelineFlag.from_json(row))
                except (TypeError, ValueError, KeyError):
                    continue
        exports = [ExportRecord.from_json(e) for e in data.get("exports", [])]
        ai_suggestions = [AiSuggestion.from_json(s) for s in data.get("ai_suggestions", [])]
        subtitle_cue_sets: dict[str, list[SubtitleCue]] = {}
        raw_sets = data.get("subtitle_cue_sets")
        if isinstance(raw_sets, dict):
            for source_key, rows in raw_sets.items():
                if not isinstance(rows, list):
                    continue
                subtitle_cue_sets[str(source_key)] = [
                    SubtitleCue.from_json(row) for row in rows if isinstance(row, dict)
                ]
        legacy_cues = [
            SubtitleCue.from_json(c)
            for c in data.get("subtitle_cues", [])
            if isinstance(c, dict)
        ]
        active_raw = data.get("active_subtitle_source")
        active_subtitle_source = (
            SubtitleSource(str(active_raw))
            if active_raw is not None and str(active_raw)
            else None
        )
        settings_raw = data.get("subtitle_settings")
        if not isinstance(settings_raw, dict):
            legacy = data.get("subtitle_censor")
            settings_raw = dict(legacy) if isinstance(legacy, dict) else None
        subtitle_settings = SubtitleSettings.from_json(settings_raw)
        whisper_raw = data.get("whisper_cache")
        whisper_cache = WhisperCache.from_json(
            dict(whisper_raw) if isinstance(whisper_raw, dict) else None
        )
        scene_raw = data.get("scene_cuts")
        scene_cuts = SceneCutCache.from_json(
            dict(scene_raw) if isinstance(scene_raw, dict) else None
        )
        preserve_raw = data.get("preserve_letterbox_on_zoom", True)
        if isinstance(preserve_raw, str):
            preserve_letterbox = preserve_raw.strip().lower() not in {"0", "false", "no"}
        else:
            preserve_letterbox = True if preserve_raw is None else bool(preserve_raw)
        matte_raw = data.get("video_matte")
        video_matte = VideoMatteCache.from_json(
            dict(matte_raw) if isinstance(matte_raw, dict) else None
        )
        selected = data.get("selected_audio_stream_index")
        overrides_raw = data.get("audio_language_overrides")
        overrides = (
            {str(key): str(value) for key, value in overrides_raw.items()}
            if isinstance(overrides_raw, dict)
            else {}
        )
        project = cls(
            project_version=int(data.get("project_version", PROJECT_FILE_VERSION)),
            source=source,
            tracks=tracks,
            operations=operations,
            timeline_flags=timeline_flags,
            exports=exports,
            ai_suggestions=ai_suggestions,
            subtitle_cue_sets=subtitle_cue_sets,
            active_subtitle_source=active_subtitle_source,
            subtitle_settings=subtitle_settings,
            whisper_cache=whisper_cache,
            scene_cuts=scene_cuts,
            preserve_letterbox_on_zoom=preserve_letterbox,
            video_matte=video_matte,
            selected_audio_stream_index=int(selected) if selected is not None else None,
            audio_language_overrides=overrides,
        )
        from corrcut.engine.subtitles.cue_sets import (
            ensure_active_source,
            migrate_legacy_subtitle_cues,
        )

        migrate_legacy_subtitle_cues(project, legacy_cues)
        if project.active_subtitle_source is None and project.subtitle_cue_sets:
            ensure_active_source(project)
        return project


def _title_surround_hint(track: TrackRecord) -> int:
    """Prefer disc labels like 'Surround 7.1' when every track is the same layout."""
    title = (track.title or "").strip().lower()
    if "7.1" in title:
        return 3
    if "5.1" in title or "6.1" in title:
        return 2
    if "2.1" in title:
        return 1
    return 0


def _audio_track_preference(track: TrackRecord) -> tuple[int, int, int, int, int]:
    """Higher tuple wins when choosing a default dialogue-bearing track.

    Real 7.1 / 8+ channels beat 5.1/6.1, then 2.1, then stereo. When an optimized
    encode downmixes everything to stereo but keeps Blu-ray titles, prefer the
    track titled 7.1 over a track titled Stereo. Container default is last.
    """
    layout = (track.channel_layout or "").strip().lower()
    channels = int(track.channels or 0)
    if layout.startswith("7.1") or channels >= 8:
        family = 4
    elif layout.startswith(("5.1", "6.1")) or channels in {6, 7}:
        family = 3
    elif layout.startswith("2.1") or channels in {3, 4, 5}:
        family = 2
    elif channels == 2 or layout in {"stereo", "2.0"}:
        family = 1
    else:
        family = 0
    return (
        family,
        _title_surround_hint(track),
        channels,
        1 if track.is_default else 0,
        -track.stream_index,
    )


def track_for_edit_language(tracks: list[TrackRecord], language: str) -> TrackRecord | None:
    """Best dialogue track whose container language matches *language*."""
    code = normalize_audio_language(language)
    if code is None or not tracks:
        return None
    matches = [
        track
        for track in tracks
        if resolve_track_language(track.language, track.title)[0] == code
    ]
    if not matches:
        return None
    return max(matches, key=_audio_track_preference)


def _default_audio_stream_index(tracks: list[TrackRecord]) -> int | None:
    if not tracks:
        return None
    return max(tracks, key=_audio_track_preference).stream_index


def probe_to_dict(probe: MediaProbeResult) -> dict[str, object]:
    from corrcut.engine.probe import probe_result_to_dict

    data = probe_result_to_dict(probe)
    data.pop("path", None)
    return data
