"""Independent expected timeline for the expected-reward Cue Bindings lesson.

The timeline is computed only from the recordings' WAV headers, the lesson layout
declared below and Manim's frame rules (scripts/render_verification.py). It does not
import Manim, Manim Voiceover, the cue resolver or the scene, so it can check a render
instead of repeating it.

Timings are compared against a render with a tolerance of TOLERANCE_FRAMES, declared
here before any render; a codec allowance, if any, must be measured, not assumed.

Usage:
    python scripts/expected_reward_timeline.py expect MANIFEST      # expected timeline as JSON
    python scripts/expected_reward_timeline.py compare A.json B.json  # compare two expected timelines
"""

import argparse
import hashlib
import json
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

from render_verification import RENDER_FPS, FrameClock, animation_frames, static_wait_frames, validated_run_time

FPS = RENDER_FPS  # manim -ql
FADE_IN_SECONDS = 0.4
FADE_OUT_SECONDS = 0.3
FINAL_HOLD_SECONDS = 0.25
TOLERANCE_FRAMES = 1
CUE_ORDER = ("question", "calculation", "decision")
# Manim's frame rules, shared with the verifiers; re-exported for this timeline's users and tests.
__all__ = ["animation_frames", "static_wait_frames", "validated_run_time", "expected_timeline", "timeline_for_manifest"]


def expected_timeline(cues: Sequence[tuple[str, float]], fps: int = FPS) -> dict[str, object]:
    """The expected timeline for (cue ID, recording duration in seconds) pairs, in scene order."""
    clock = FrameClock(fps)
    entries: list[dict[str, object]] = []
    for index, (cue_id, duration) in enumerate(cues):
        start_time, start_frame = clock.time, clock.frames
        clock.animate(FADE_IN_SECONDS)
        remaining = max(start_time + duration - clock.time, 0.0)
        if remaining > 1 / fps:
            clock.wait_static(remaining)
        block_end_frame = clock.frames
        if index < len(cues) - 1:
            clock.animate(FADE_OUT_SECONDS)
        onset_ms = int(1000 * start_time)
        entries.append(
            {
                "cue_id": cue_id,
                "duration_seconds": duration,
                "start_frame": start_frame,
                "audio_onset_seconds": onset_ms / 1000,
                "audio_end_seconds": onset_ms / 1000 + duration,
                "block_end_frame": block_end_frame,
                "block_end_seconds": block_end_frame / fps,
            }
        )
    clock.wait_static(FINAL_HOLD_SECONDS)
    return _with_checks(entries, clock.frames, fps)


def _with_checks(entries: list[dict[str, object]], video_frames: int, fps: int) -> dict[str, object]:
    starts = [float(str(entry["audio_onset_seconds"])) for entry in entries]
    ends = [float(str(entry["audio_end_seconds"])) for entry in entries]
    gaps = [next_start - end for end, next_start in zip(ends, starts[1:])]
    video_seconds = video_frames / fps
    return {
        "fps": fps,
        "tolerance_frames": TOLERANCE_FRAMES,
        "tolerance_seconds": TOLERANCE_FRAMES / fps,
        "layout": {
            "fade_in_seconds": FADE_IN_SECONDS,
            "fade_out_seconds": FADE_OUT_SECONDS,
            "final_hold_seconds": FINAL_HOLD_SECONDS,
            "last_panel_fades_out": False,
        },
        "cues": entries,
        "gaps_after_audio_seconds": gaps,
        "video_frames": video_frames,
        "video_seconds": video_seconds,
        "last_audio_end_seconds": ends[-1],
        "final_margin_seconds": video_seconds - ends[-1],
        "no_overlap": all(gap >= 0 for gap in gaps),
        "last_recording_completes": video_seconds >= ends[-1],
    }


def _read_wav(path: Path) -> dict[str, object]:
    with wave.open(str(path), "rb") as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
    return {
        "wav": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "frames": frames,
        "sample_rate": rate,
        "duration_seconds": frames / rate,
    }


def timeline_for_manifest(manifest_path: Path, cue_order: Sequence[str] = CUE_ORDER) -> dict[str, object]:
    """Read the manifest and WAV headers directly, without the cue resolver, and return the expected timeline."""
    document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    bindings = {entry["id"]: entry["wav"] for entry in document["cues"]}
    recordings = {cue_id: _read_wav(manifest_path.parent / bindings[cue_id]) for cue_id in dict.fromkeys(cue_order)}
    timeline = expected_timeline([(cue_id, float(str(recordings[cue_id]["duration_seconds"]))) for cue_id in cue_order])
    return {"manifest": str(manifest_path), "recordings": recordings, **timeline}


def _start_frames(timeline: dict[str, object]) -> dict[str, int]:
    cues = timeline["cues"]
    if not isinstance(cues, list):
        raise ValueError("expected timeline has no cue list")
    return {str(entry["cue_id"]): int(entry["start_frame"]) for entry in cues}


def compare_timelines(first: dict[str, object], second: dict[str, object]) -> dict[str, dict[str, float]]:
    """Compare the cue start frames of two independently computed expected timelines."""
    before, after = _start_frames(first), _start_frames(second)
    return {
        cue_id: {
            "first_start_frame": before[cue_id],
            "second_start_frame": after[cue_id],
            "start_shift_frames": after[cue_id] - before[cue_id],
            "start_shift_seconds": (after[cue_id] - before[cue_id]) / FPS,
        }
        for cue_id in before
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("expect").add_argument("manifest", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "expect":
        result = timeline_for_manifest(arguments.manifest)
    else:
        first, second = (json.loads(path.read_text(encoding="utf-8")) for path in (arguments.first, arguments.second))
        result = compare_timelines(first, second)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
