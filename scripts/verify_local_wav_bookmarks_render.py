"""Verify a Manual Narration Bookmarks render: each bookmark's animation starts within one frame.

``expect`` computes, BEFORE rendering, the expected timeline of the expected-reward bookmarks
lesson (examples/rl_lessons/expected_reward_bookmarks.py) from the WAV header, the bookmark file
(read directly, not through the package), the layout declared below and Manim's frame rules
(scripts/render_verification.py). It does not import Manim, Manim Voiceover or the scene.

``verify`` measures the render in a media folder written by `scripts/dev.sh bookmarks render`
(MEDIA/expected.json, sources-*.sha256, cache-*.json, the video and Manim's uncompressed soundtrack)
and checks:

- the recording: present sample for sample in the soundtrack, at its expected onset; its final
  voiced second correlates with the MP4 audio; the video covers the whole recording;
- every bookmark: the frame in which its sync marker first appears (and then stays) is within
  one frame of the bookmark's time in the soundtrack (recording onset + bookmark seconds), and
  also in the MP4 after the measured codec offset; it equals the expected frame within the
  declared tolerance; the bookmarks trigger in order; and the check is sensitive: shifting a
  bookmark by two frames would make it fail;
- the video frame count equals the expected frame count;
- the sources (recording and bookmark file) are unchanged during the render and are the ones the
  expectations were computed from; the expectations were recorded before the video;
- the cache: a copy with the recording's bytes exists, copies cached before were not rewritten,
  and the voiceover added one cache.json entry.

Usage:
    python scripts/verify_local_wav_bookmarks_render.py expect RECORDING BOOKMARKS   # JSON on stdout
    python scripts/verify_local_wav_bookmarks_render.py snapshot-cache CACHE
    python scripts/verify_local_wav_bookmarks_render.py verify MEDIA [--expected FILE] [--output FILE]
"""

import argparse
import json
import sys
import wave
from collections.abc import Sequence
from pathlib import Path

import av
import numpy as np
from render_verification import (
    CORRELATION_THRESHOLD,
    RENDER_FPS,
    FrameClock,
    check,
    correlation,
    decode_mp4,
    final_voiced_second,
    locate,
    measure_offset,
    read_pcm16,
    sha256_file,
    snapshot_cache,
)

# Declared layout of examples/rl_lessons/expected_reward_bookmarks.py (a test keeps the two equal).
BOOKMARK_ORDER = ("quarter_times_zero", "three_quarters_times_four", "expected_reward_three")
REVEAL_SECONDS = 0.5
FINAL_HOLD_SECONDS = 0.25
MARKER_SIDE = 0.3
MARKER_X = -6.6
MARKER_SPACING = 0.5
MARKER_Y = -3.55
FRAME_HEIGHT = 8.0  # scene units shown from the bottom to the top of the picture (Manim's default)
MARKER_LEVEL = 128  # mean 8-bit luminance above which a marker counts as shown (background about 22, marker 255)
TOLERANCE_FRAMES = 1
SENSITIVITY_SHIFT_FRAMES = 2
VIDEO_DIR = Path("videos") / "expected_reward_bookmarks" / "480p15"
OUTPUT_NAME = "bookmarks"


# ---------------------------------------------------------------------------
# Expected timeline (computed before rendering)
# ---------------------------------------------------------------------------


def read_bookmark_file(path: Path) -> dict[str, float]:
    """Bookmark times from a version 1 file, read directly (the package's loader validates; this only reads)."""
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    return {entry["name"]: float(entry["seconds"]) for entry in document["bookmarks"]}


def expected_timeline(duration: float, bookmarks: dict[str, float], fps: int = RENDER_FPS) -> dict[str, object]:
    """Frames of the lesson: each bookmark's trigger frame, then the end of the voiceover and the final hold."""
    clock = FrameClock(fps)
    start_time = clock.time  # the voiceover starts the scene
    entries = []
    for name in BOOKMARK_ORDER:
        # VoiceoverScene.wait_until_bookmark rounds a manual bookmark to the nearest frame (half a frame
        # added before Manim rounds the wait down); safe_wait does not wait for one frame or less.
        remaining = max(start_time + bookmarks[name] - clock.time, 0.0) + 0.5 / fps
        if remaining > 1 / fps:
            clock.wait_static(remaining)
        entries.append({"name": name, "seconds": bookmarks[name], "expected_frame": clock.frames})
        clock.animate(REVEAL_SECONDS)
    remaining = max(start_time + duration - clock.time, 0.0)
    if remaining > 1 / fps:
        clock.wait_static(remaining)
    clock.wait_static(FINAL_HOLD_SECONDS)
    return {
        "fps": fps,
        "tolerance_frames": TOLERANCE_FRAMES,
        "audio_onset_seconds": int(1000 * start_time) / 1000,
        "bookmarks": entries,
        "video_frames": clock.frames,
        "layout": {"reveal_seconds": REVEAL_SECONDS, "final_hold_seconds": FINAL_HOLD_SECONDS},
    }


def expect(recording: Path, bookmark_file: Path) -> dict[str, object]:
    with wave.open(str(recording), "rb") as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
    timeline = expected_timeline(frames / rate, read_bookmark_file(bookmark_file))
    sources = {
        "recording": {"path": str(recording), "sha256": sha256_file(recording), "frames": frames, "sample_rate": rate},
        "bookmark_file": {"path": str(bookmark_file), "sha256": sha256_file(bookmark_file)},
    }
    return {**sources, "recording_seconds": frames / rate, **timeline}


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def marker_box(index: int, width: int, height: int) -> tuple[slice, slice]:
    """Pixel rows and columns well inside sync marker ``index`` in a ``width`` x ``height`` picture."""
    scale = height / FRAME_HEIGHT  # pixels per scene unit
    column = width / 2 + (MARKER_X + index * MARKER_SPACING) * scale
    row = height / 2 - MARKER_Y * scale
    half = max(MARKER_SIDE * scale / 4, 0.5)  # a quarter of the side on each side of the centre
    return slice(round(row - half), round(row + half)), slice(round(column - half), round(column + half))


def marker_levels(video: Path, count: int) -> np.ndarray:
    """Mean luminance of each marker's box in every decoded video frame: shape (frames, markers)."""
    levels = []
    with av.open(str(video)) as container:
        for frame in container.decode(video=0):
            picture = frame.to_ndarray(format="gray")
            boxes = [marker_box(index, picture.shape[1], picture.shape[0]) for index in range(count)]
            levels.append([float(picture[rows, columns].mean()) for rows, columns in boxes])
    return np.array(levels)


def first_shown(levels: np.ndarray) -> tuple[int | None, bool]:
    """The first frame a marker is shown, and whether it stays shown until the end."""
    shown = levels > MARKER_LEVEL
    if not shown.any():
        return None, False
    first = int(np.argmax(shown))
    return first, bool(shown[first:].all())


def measure_recording(recording: Path, soundtrack: Path, mp4: dict) -> dict[str, object]:
    source, rate = read_pcm16(recording)
    track, track_rate = read_pcm16(soundtrack)
    if track_rate != rate:
        raise ValueError(f"recording rate {rate} differs from the soundtrack rate {track_rate}")
    onset = locate(source, track)
    offset = measure_offset(track, mp4["audio"])
    mp4_onset = locate(source, mp4["audio"])
    start, stop = final_voiced_second(source, rate)
    return {
        "rate": rate,
        "onset_seconds": onset / rate,
        "end_seconds": (onset + len(source)) / rate,
        "present_unaltered": bool(np.array_equal(track[onset : onset + len(source)], source)),
        "codec_offset_seconds": offset / rate,
        "mp4_final_voiced_second_correlation": correlation(
            mp4["audio"][mp4_onset + start : mp4_onset + stop], source[start:stop]
        ),
        "mp4_audio_seconds": len(mp4["audio"]) / rate,
    }


def measure_bookmarks(expected: dict, recording: dict, levels: np.ndarray) -> list[dict[str, object]]:
    fps = expected["fps"]
    measured = []
    for index, entry in enumerate(expected["bookmarks"]):
        frame, stays = first_shown(levels[:, index])
        target = recording["onset_seconds"] + entry["seconds"]
        marker_seconds = None if frame is None else frame / fps
        measured.append(
            {
                **entry,
                "marker_frame": frame,
                "marker_stays_shown": stays,
                "marker_seconds": marker_seconds,
                "bookmark_in_soundtrack_seconds": target,
                "error_frames": None if frame is None else (marker_seconds - target) * fps,
                "error_frames_in_mp4": None
                if frame is None
                else (marker_seconds - target - recording["codec_offset_seconds"]) * fps,
            }
        )
    return measured


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def bookmark_checks(bookmarks: list[dict], tolerance_frames: int) -> list[dict[str, object]]:
    checks = []
    for entry in bookmarks:
        name, error, error_mp4 = entry["name"], entry["error_frames"], entry["error_frames_in_mp4"]
        found = entry["marker_frame"] is not None
        checks += [
            check(f"{name}: sync marker appears once and stays shown", found and entry["marker_stays_shown"]),
            check(
                f"{name}: animation starts within one frame of the bookmark in the soundtrack",
                found and abs(error) <= 1,
                error_frames=error,
            ),
            check(
                f"{name}: animation starts within one frame of the bookmark in the MP4 (after the codec offset)",
                found and abs(error_mp4) <= 1,
                error_frames=error_mp4,
            ),
            check(
                f"{name}: animation starts at the expected frame",
                found and abs(entry["marker_frame"] - entry["expected_frame"]) <= tolerance_frames,
                measured=entry["marker_frame"],
                expected=entry["expected_frame"],
            ),
            check(
                f"{name}: the check is sensitive (a bookmark {SENSITIVITY_SHIFT_FRAMES} frames earlier or later would fail)",
                found and all(abs(error + shift) > 1 for shift in (-SENSITIVITY_SHIFT_FRAMES, SENSITIVITY_SHIFT_FRAMES)),
            ),
        ]
    frames = [entry["marker_frame"] for entry in bookmarks]
    in_order = None not in frames and all(first < second for first, second in zip(frames, frames[1:]))
    checks.append(check("bookmarks trigger in their declared order", in_order, marker_frames=frames))
    return checks


def recording_checks(expected: dict, recording: dict, video_frames: int) -> list[dict[str, object]]:
    fps = expected["fps"]
    video_seconds = video_frames / fps
    return [
        check("recording present sample for sample in the soundtrack", recording["present_unaltered"]),
        check(
            "recording starts within one frame of its expected onset",
            abs(recording["onset_seconds"] - expected["audio_onset_seconds"]) <= 1 / fps,
            measured=recording["onset_seconds"],
            expected=expected["audio_onset_seconds"],
        ),
        check(
            "final voiced second correlates with the recording in the MP4",
            recording["mp4_final_voiced_second_correlation"] > CORRELATION_THRESHOLD,
            measured=recording["mp4_final_voiced_second_correlation"],
        ),
        check(
            "video covers the whole recording, including the codec offset",
            recording["end_seconds"] + recording["codec_offset_seconds"] <= min(video_seconds, recording["mp4_audio_seconds"]),
            recording_end=recording["end_seconds"] + recording["codec_offset_seconds"],
            video_seconds=video_seconds,
        ),
        check(
            "video frame count equals the expected frame count",
            video_frames == expected["video_frames"],
            measured=video_frames,
            expected=expected["video_frames"],
        ),
    ]


def source_checks(media: Path, expected: dict, video: Path) -> list[dict[str, object]]:
    before = (media / "sources-before.sha256").read_text(encoding="utf-8").splitlines()
    after = (media / "sources-after.sha256").read_text(encoding="utf-8").splitlines()
    current = {kind: sha256_file(Path(expected[kind]["path"])) for kind in ("recording", "bookmark_file")}
    declared = [media / "expected.json", media / "cache-before.json"]
    return [
        check(
            "expectations were recorded before the video was rendered",
            all(path.stat().st_mtime_ns < video.stat().st_mtime_ns for path in declared),
        ),
        check("sources unchanged during the render", bool(before) and before == after, files=len(before)),
        check(
            "recording and bookmark file are the ones the expectations were computed from",
            all(current[kind] == expected[kind]["sha256"] for kind in current),
        ),
    ]


def cache_checks(expected: dict, before: dict, after: dict) -> list[dict[str, object]]:
    recording = expected["recording"]["sha256"]
    rewritten = [name for name, copy in before["copies"].items() if after["copies"].get(name) != copy]
    return [
        check(
            "cache: a cached copy has the recording's bytes",
            recording in {copy["sha256"] for copy in after["copies"].values()},
        ),
        check("cache: copies cached before the render were not rewritten", not rewritten, rewritten=rewritten),
        check(
            "cache: one cache.json entry for the voiceover",
            after["cache_json_entries"] - before["cache_json_entries"] == 1,
            added_entries=after["cache_json_entries"] - before["cache_json_entries"],
        ),
    ]


def verify(media: Path, expected_path: Path | None = None, video: Path | None = None) -> dict[str, object]:
    """Measure the render in ``media`` (the dev.sh layout, or ``video``) and compare it with the expectations."""
    expected = json.loads((expected_path or media / "expected.json").read_text(encoding="utf-8"))
    video = video or media / VIDEO_DIR / f"{OUTPUT_NAME}.mp4"
    recording_path = Path(expected["recording"]["path"])
    mp4 = decode_mp4(video, expected["recording"]["sample_rate"])
    recording = measure_recording(recording_path, video.with_suffix(".wav"), mp4)
    levels = marker_levels(video, len(expected["bookmarks"]))
    bookmarks = measure_bookmarks(expected, recording, levels)
    before = json.loads((media / "cache-before.json").read_text(encoding="utf-8"))
    after = json.loads((media / "cache-after.json").read_text(encoding="utf-8"))
    checks = bookmark_checks(bookmarks, expected["tolerance_frames"])
    checks += recording_checks(expected, recording, mp4["video_frames"])
    checks += source_checks(media, expected, video)
    checks += cache_checks(expected, before, after)
    failures = [item["check"] for item in checks if not item["passed"]]
    return {
        "video": str(video),
        "expected": str(expected_path or media / "expected.json"),
        "passed": not failures,
        "failures": failures,
        "fps": expected["fps"],
        "tolerance_frames": expected["tolerance_frames"],
        "video_frames": mp4["video_frames"],
        "recording": {key: value for key, value in recording.items() if key != "rate"},
        "bookmarks": bookmarks,
        "checks": checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    expect_parser = commands.add_parser("expect")
    expect_parser.add_argument("recording", type=Path)
    expect_parser.add_argument("bookmarks", type=Path)
    commands.add_parser("snapshot-cache").add_argument("cache", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("media", type=Path)
    verify_parser.add_argument("--expected", type=Path, help="check against other expectations")
    verify_parser.add_argument("--output", type=Path, help="write the result to this file")
    arguments = parser.parse_args(argv)
    if arguments.command == "expect":
        json.dump(expect(arguments.recording.resolve(), arguments.bookmarks.resolve()), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if arguments.command == "snapshot-cache":
        json.dump(snapshot_cache(arguments.cache), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    result = verify(arguments.media, arguments.expected)
    text = json.dumps(result, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(text, encoding="utf-8")
    for failure in result["failures"]:
        print(f"FAILED: {failure}", file=sys.stderr)
    for entry in result["bookmarks"]:
        print(
            f"{entry['name']}: bookmark {entry['seconds']:g} s, animation at frame {entry['marker_frame']} "
            f"(error {entry['error_frames']:+.2f} frames)"
            if entry["marker_frame"] is not None
            else f"{entry['name']}: sync marker not found",
            file=sys.stderr,
        )
    saved = f"saved to {arguments.output}" if arguments.output is not None else "not saved"
    print(f"{'PASSED' if result['passed'] else 'FAILED'}: {len(result['checks'])} checks; {saved}", file=sys.stderr)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
