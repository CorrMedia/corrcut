"""Editor window building blocks (menus, project session, jobs)."""

from corrcut.ui.editor.ai_job import EditorAiMixin
from corrcut.ui.editor.filmstrip import EditorFilmstripMixin
from corrcut.ui.editor.flags import EditorFlagsMixin
from corrcut.ui.editor.menus import EditorMenusMixin
from corrcut.ui.editor.operations import EditorOperationsMixin
from corrcut.ui.editor.project_session import EditorProjectMixin
from corrcut.ui.editor.scene_cuts import EditorSceneCutsMixin
from corrcut.ui.editor.subtitles import EditorSubtitlesMixin
from corrcut.ui.editor.undo import EditorUndoMixin
from corrcut.ui.editor.video_matte import EditorVideoMatteMixin
from corrcut.ui.editor.waveform import EditorWaveformMixin

__all__ = [
    "EditorAiMixin",
    "EditorFilmstripMixin",
    "EditorFlagsMixin",
    "EditorMenusMixin",
    "EditorOperationsMixin",
    "EditorProjectMixin",
    "EditorSceneCutsMixin",
    "EditorSubtitlesMixin",
    "EditorUndoMixin",
    "EditorVideoMatteMixin",
    "EditorWaveformMixin",
]
