"""Historical export records stored on older project files.

The app no longer writes a movie file. Playback edits are a corr.json sidecar.
These types only round-trip ``project.exports`` from projects saved by earlier builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportManifest:
    """One past export entry. New sessions do not create these."""

    source_path: str
    output_path: str
    edited_stream_index: int
    preset: str
    clean_track_codec: str
    is_transcode: bool
    filter_complex: str
    clean_track_name: str
    clean_track_language: str | None
    set_clean_as_default: bool
    completed_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "output_path": self.output_path,
            "edited_stream_index": self.edited_stream_index,
            "preset": self.preset,
            "clean_track_codec": self.clean_track_codec,
            "is_transcode": self.is_transcode,
            "filter_complex": self.filter_complex,
            "clean_track_name": self.clean_track_name,
            "clean_track_language": self.clean_track_language,
            "set_clean_as_default": self.set_clean_as_default,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> ExportManifest:
        return cls(
            source_path=str(data["source_path"]),
            output_path=str(data["output_path"]),
            edited_stream_index=int(str(data["edited_stream_index"])),
            preset=str(data["preset"]),
            clean_track_codec=str(data["clean_track_codec"]),
            is_transcode=bool(data["is_transcode"]),
            filter_complex=str(data["filter_complex"]),
            clean_track_name=str(data["clean_track_name"]),
            clean_track_language=(
                str(data["clean_track_language"]) if data.get("clean_track_language") else None
            ),
            set_clean_as_default=bool(data.get("set_clean_as_default", False)),
            completed_at=str(data["completed_at"]),
        )


@dataclass
class ExportRecord:
    output_path: Path
    manifest: ExportManifest

    def to_json(self) -> dict[str, object]:
        return {
            "output_path": str(self.output_path),
            "manifest": self.manifest.to_json(),
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> ExportRecord:
        manifest_raw = data.get("manifest", {})
        assert isinstance(manifest_raw, dict)
        return cls(
            output_path=Path(str(data["output_path"])),
            manifest=ExportManifest.from_json(manifest_raw),
        )
