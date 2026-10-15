"""FiltergraphCompiler — single source of truth for audio filter strings (5b)."""

from __future__ import annotations

from dataclasses import dataclass

from corrcut.domain.channel_layout import default_layout_for_channels, known_layout_channel_labels
from corrcut.domain.channel_scope import operation_targets_channel
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import TrackRecord

# Re-export for tests and audition helpers.
layout_channel_labels = known_layout_channel_labels


@dataclass(frozen=True)
class CompileContext:
    track: TrackRecord
    input_label: str = "0:a"
    output_label: str = "aout"
    # Subtract from operation times (audition -ss); export uses 0.
    filter_time_origin_s: float = 0.0
    # Audition-only: preview edited channel(s) without full 5.1 join/downmix.
    audition_preview_channels: frozenset[str] | None = None

    @property
    def sample_rate(self) -> int:
        return self.track.sample_rate or 48000


@dataclass(frozen=True)
class CompiledFiltergraph:
    """FFmpeg -filter_complex fragment (without outer map flags)."""

    filter_complex: str
    output_audio_label: str = "aout"


def _clamp_fade_durations(duration_s: float, fade_in_s: float, fade_out_s: float) -> tuple[float, float]:
    if duration_s <= 0:
        return 0.0, 0.0
    half = duration_s / 2.0
    return min(fade_in_s, half), min(fade_out_s, half)


class FiltergraphCompiler:
    """
    Compile Operations to FFmpeg lavfi filter chains.

    Overlapping operations on the same channel chain in (order, start) sequence;
    each op's filters run in series (see blueprint Section 14 — export may refine later).
    """

    def compile(
        self,
        operations: list[Operation],
        ctx: CompileContext,
    ) -> CompiledFiltergraph:
        active = [o for o in operations if o.enabled and o.scope.stream_index == ctx.track.stream_index]
        active.sort(key=lambda o: (o.order, o.time_range.start.seconds))

        if ctx.track.dialogue_target_strategy == DialogueTargetStrategy.CENTER_CHANNEL:
            return self._compile_center_channel(active, ctx)
        return self._compile_full_mix(active, ctx)

    def compile_for_audio_stream(
        self,
        operations: list[Operation],
        ctx: CompileContext,
        audio_ordinal: int,
    ) -> CompiledFiltergraph:
        """Compile using FFmpeg audio stream selector 0:a:N."""
        ctx_map = CompileContext(
            track=ctx.track,
            input_label=f"0:a:{audio_ordinal}",
            output_label=ctx.output_label,
            filter_time_origin_s=ctx.filter_time_origin_s,
            audition_preview_channels=ctx.audition_preview_channels,
        )
        return self.compile(operations, ctx_map)

    def _compile_full_mix(self, operations: list[Operation], ctx: CompileContext) -> CompiledFiltergraph:
        chain = ctx.input_label
        filters: list[str] = []
        for op in operations:
            filters.extend(self._operation_filters(op, ctx.sample_rate, ctx.filter_time_origin_s))

        if not filters:
            return CompiledFiltergraph(filter_complex=f"[{chain}]anull[{ctx.output_label}]", output_audio_label=ctx.output_label)

        filter_body = ",".join(filters)
        graph = f"[{chain}]{filter_body}[{ctx.output_label}]"
        return CompiledFiltergraph(filter_complex=graph, output_audio_label=ctx.output_label)

    def _compile_center_channel(
        self,
        operations: list[Operation],
        ctx: CompileContext,
    ) -> CompiledFiltergraph:
        layout = ctx.track.channel_layout or default_layout_for_channels(ctx.track.channels)
        chain = ctx.input_label

        labels = known_layout_channel_labels(layout, ctx.track.channels)
        if labels is None or "FC" not in labels:
            return self._compile_full_mix(operations, ctx)

        has_any_filter = any(
            self._filters_for_channel(operations, name, ctx.sample_rate, ctx.filter_time_origin_s)
            for name in labels
        )
        if ctx.audition_preview_channels is not None and has_any_filter:
            return self._compile_center_audition_preview(operations, ctx, labels)

        has_any_filter = False
        extract_steps: list[str] = []
        channel_steps: list[str] = []
        join_inputs: list[str] = []

        for name in labels:
            filters = self._filters_for_channel(
                operations, name, ctx.sample_rate, ctx.filter_time_origin_s
            )
            asp = f"asp_{name}"
            if name == "FC":
                mono_label = "fc_src"
            else:
                mono_label = f"ms_{name}"
            extract_steps.append(f"[{asp}]pan=mono|c0={name}[{mono_label}]")

            if filters:
                has_any_filter = True
                body = ",".join(filters)
                edited = "fc_e" if name == "FC" else f"ms_{name}_e"
                channel_steps.append(f"[{mono_label}]{body}[{edited}]")
                join_inputs.append(f"[{edited}]")
            else:
                join_inputs.append(f"[{mono_label}]")

        if not has_any_filter:
            return CompiledFiltergraph(
                filter_complex=f"[{chain}]anull[{ctx.output_label}]",
                output_audio_label=ctx.output_label,
            )

        n = len(labels)
        asplit_outputs = "".join(f"[asp_{name}]" for name in labels)
        prefix = f"[{chain}]asplit={n}{asplit_outputs}"
        extract = ";".join(extract_steps)
        middle = ";".join(channel_steps)
        join_body = "".join(join_inputs)
        # Pin each mono input to its output channel; every input is a mono (FC)
        # stream after pan, so join's implicit name-matching is ambiguous.
        join_map = "|".join(f"{i}.0-{name}" for i, name in enumerate(labels))
        graph = (
            f"{prefix};{extract};{middle};{join_body}"
            f"join=inputs={n}:channel_layout={layout}:map={join_map}[{ctx.output_label}]"
        )
        return CompiledFiltergraph(filter_complex=graph, output_audio_label=ctx.output_label)

    def _compile_center_audition_preview(
        self,
        operations: list[Operation],
        ctx: CompileContext,
        labels: list[str],
    ) -> CompiledFiltergraph:
        """Audition: hear edited channel(s) like the FC waveform, not a quiet 5.1 downmix."""
        preview = ctx.audition_preview_channels or frozenset({"FC"})
        ordered = [name for name in labels if name in preview]
        if not ordered:
            ordered = ["FC"] if "FC" in labels else [labels[0]]

        chain = ctx.input_label
        pts = "pts0"
        steps: list[str] = [f"[{chain}]asetpts=N/SR/TB[{pts}]"]

        if len(ordered) == 1:
            name = ordered[0]
            filters = self._filters_for_channel(
                operations, name, ctx.sample_rate, ctx.filter_time_origin_s
            )
            steps.append(f"[{pts}]pan=mono|c0={name}[mono_raw]")
            steps.append("[mono_raw]asetpts=PTS-STARTPTS[mono]")
            label = "mono"
            if filters:
                body = ",".join(filters)
                steps.append(f"[mono]{body}[edited]")
                label = "edited"
            steps.append(f"[{label}]pan=stereo|FL=c0|FR=c0[{ctx.output_label}]")
            return CompiledFiltergraph(filter_complex=";".join(steps), output_audio_label=ctx.output_label)

        asplit_outs = "".join(f"[asp_{name}]" for name in ordered)
        steps.append(f"[{pts}]asplit={len(ordered)}{asplit_outs}")
        stereo_labels: list[str] = []
        for name in ordered:
            filters = self._filters_for_channel(
                operations, name, ctx.sample_rate, ctx.filter_time_origin_s
            )
            mono_raw = f"mraw_{name}"
            mono = f"m_{name}"
            steps.append(f"[asp_{name}]pan=mono|c0={name}[{mono_raw}]")
            steps.append(f"[{mono_raw}]asetpts=PTS-STARTPTS[{mono}]")
            out = mono
            if filters:
                body = ",".join(filters)
                out = f"e_{name}"
                steps.append(f"[{mono}]{body}[{out}]")
            st = f"st_{name}"
            if name == "FC":
                steps.append(f"[{out}]pan=stereo|FL=0.707*c0|FR=0.707*c0[{st}]")
            elif name == "FL":
                steps.append(f"[{out}]pan=stereo|FL=c0|FR=0*c0[{st}]")
            else:
                steps.append(f"[{out}]pan=stereo|FL=0*c0|FR=c0[{st}]")
            stereo_labels.append(f"[{st}]")
        steps.append(
            f"{''.join(stereo_labels)}amix=inputs={len(stereo_labels)}:duration=longest[{ctx.output_label}]"
        )
        return CompiledFiltergraph(filter_complex=";".join(steps), output_audio_label=ctx.output_label)

    def _filters_for_channel(
        self,
        operations: list[Operation],
        channel_label: str,
        sample_rate: int,
        time_origin_s: float,
    ) -> list[str]:
        filters: list[str] = []
        for op in operations:
            if not operation_targets_channel(
                op.scope.channels,
                channel_label,
                per_channel_editing=True,
            ):
                continue
            filters.extend(self._operation_filters(op, sample_rate, time_origin_s))
        return filters

    def _operation_filters(
        self,
        op: Operation,
        sample_rate: int,
        time_origin_s: float = 0.0,
    ) -> list[str]:
        start_s = float(op.time_range.start.to_samples(sample_rate) / sample_rate) - time_origin_s
        end_s = float(op.time_range.end.to_samples(sample_rate) / sample_rate) - time_origin_s
        if end_s <= 0:
            return []
        if end_s <= start_s:
            return []

        params = op.censor_params()
        # Corr.json has no fade fields — preview uses hard edges.
        fi = 0.0
        fo = 0.0
        mute_already_active = start_s < 0
        if mute_already_active:
            start_s = 0.0

        if op.type == OperationType.MUTE:
            return self._mute_filters(start_s, end_s, fi, fo, mute_already_active)

        if op.type == OperationType.VOLUME:
            return self._duck_filters(
                start_s, end_s, fi, fo, params.gain, mute_already_active
            )

        if op.type == OperationType.BEEP:
            return self._beep_filters(
                start_s,
                end_s,
                fi,
                fo,
                params.beep_freq,
                mute_already_active,
                amp=params.beep_gain,
            )

        return []

    def _mute_filters(
        self,
        start_s: float,
        end_s: float,
        fade_in_s: float,
        fade_out_s: float,
        mute_already_active: bool,
    ) -> list[str]:
        # Use a scoped gain envelope so ramps only affect the mute window.
        # Using afade(in) globally can zero audio before the fade start time.
        return self._duck_filters(
            start_s,
            end_s,
            fade_in_s,
            fade_out_s,
            duck_lin=0.0,
            mute_already_active=mute_already_active,
        )

    def _duck_filters(
        self,
        start_s: float,
        end_s: float,
        fade_in_s: float,
        fade_out_s: float,
        duck_lin: float,
        mute_already_active: bool,
    ) -> list[str]:
        duration = end_s - start_s
        fi, fo = _clamp_fade_durations(duration, fade_in_s, fade_out_s)
        if mute_already_active:
            fi = 0.0

        if fi <= 0 and fo <= 0:
            return [f"volume={duck_lin}:enable='between(t,{start_s},{end_s})'"]

        s, e = start_s, end_s
        if fi > 0 and fo > 0:
            inner = (
                f"if(lt(t,{s}+{fi}),1-(1-{duck_lin})*(t-{s})/{fi},"
                f"if(lt(t,{e}-{fo}),{duck_lin},{duck_lin}+(1-{duck_lin})*(t-({e}-{fo}))/{fo}))"
            )
        elif fi > 0:
            inner = f"if(lt(t,{s}+{fi}),1-(1-{duck_lin})*(t-{s})/{fi},{duck_lin})"
        else:
            inner = (
                f"if(lt(t,{e}-{fo}),{duck_lin},{duck_lin}+(1-{duck_lin})*(t-({e}-{fo}))/{fo}))"
            )
        vol_expr = f"if(between(t,{s},{e}),{inner},1)"
        return [f"volume=volume='{vol_expr}':eval=frame"]

    def _beep_filters(
        self,
        start_s: float,
        end_s: float,
        fade_in_s: float,
        fade_out_s: float,
        beep_freq: float,
        mute_already_active: bool,
        *,
        amp: float = 0.3,
    ) -> list[str]:
        """Mute the source during the region, then overlay a sine censor tone."""
        filters = self._mute_filters(
            start_s, end_s, fade_in_s, fade_out_s, mute_already_active
        )
        s, e = start_s, end_s
        tone_amp = max(0.0, min(1.0, amp))
        freq = max(1.0, beep_freq)
        beep_expr = (
            f"val(0)+{tone_amp}*sin(2*PI*{freq}*t)*between(t,{s},{e})"
        )
        filters.append(f"aeval=exprs='{beep_expr}':c=same")
        return filters
