"""Verify an S02 render against the expectations recorded BEFORE it was rendered.

A render record (media/s02/runs/NN-take, written by scripts/s02.sh) holds expected.json
(the independent timeline from scripts/s02_timeline.py, with its tolerance),
cache-before.json and sources-before.sha256, all written before rendering, and
cache-after.json and sources-after.sha256, written after it. This script measures the
render and compares:

- every cue: present sample for sample in Manim's uncompressed soundtrack, at its
  expected onset; found at its expected onset in the MP4 after the measured codec
  offset; whole cue and final voiced second correlated with the recording in the MP4;
  finished before the next cue starts;
- the last recording: finished before the video ends, in the soundtrack and in the MP4;
- the codec offset: MEASURED by aligning the soundtrack with the decoded MP4 audio, and
  accepted only if it equals one frame of the audio codec, as the MP4 reports it;
- the video: decoded frame count equal to the expected frame count;
- the cached WAVs: one copy per recording with the recording's bytes; new copies are
  exactly the recordings that were not cached before; copies that existed before were
  not rewritten; one cache.json entry per cue; recorded copies still unchanged now;
- the sources: unchanged during the render and equal to the recordings that the
  expected timeline was computed from.

The verifier only reads the render, the record, the recordings and the cache, and never
overwrites a file. With the record's own expected.json, the first result is saved as the
record's verification.json and later ones as new files in MEDIA/checks/. A check against
other expectations (--expected) is only saved with --output.

With --expect-mismatch CUE, the check against other expectations must fail specifically
because of CUE's recording: CUE is not found sample for sample and does not correlate;
every check of the cues before CUE, the codec check and the record checks still pass; and
only checks that CUE's recording can affect fail (CUE, later cues, video length, cache).

Usage:
    python scripts/verify_s02_render.py verify media/s02/runs/01-a
    python scripts/verify_s02_render.py verify media/s02/runs/02-b --expected media/s02/runs/01-a/expected.json \
        --expect-mismatch calculation --output media/s02/checks/02-b-against-01-a.json
    python scripts/verify_s02_render.py snapshot-cache media/s02/voiceovers
"""

import argparse
import hashlib
import json
import os
import sys
import wave
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import av
import numpy as np

CORRELATION_THRESHOLD = 0.97  # as in the S01 render verifier
VOICED_LEVEL = 128  # |sample| above which a 16-bit sample counts as voiced, as in the S01 verifier
LESSON_VIDEO_DIR = Path("videos") / "expected_reward_cues" / "480p15"
CODEC_CHECK = "codec offset is measured and equals one audio codec frame"
RECORD_CHECKS = (
    "expectations were recorded before the video was rendered",
    "sources unchanged during the render",
    "recordings are the ones the expected timeline was computed from",
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_cache(cache: Path) -> dict[str, object]:
    """SHA-256 and file identity of every cached WAV, and the number of cache.json entries."""
    copies = {}
    for path in sorted(cache.glob("*.wav")):
        info = os.lstat(path)
        copies[path.name] = {
            "sha256": sha256_file(path),
            "inode": info.st_ino,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }
    index = cache / "cache.json"
    entries = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
    return {"copies": copies, "cache_json_entries": len(entries)}


def read_pcm16(path: Path) -> tuple[np.ndarray, int]:
    """First channel of a 16-bit PCM WAV file and its sample rate."""
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2:
            raise ValueError(f"{path} is not 16-bit PCM")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        return samples.reshape(-1, audio.getnchannels())[:, 0], audio.getframerate()


def decode_mp4(video: Path, rate: int) -> dict[str, object]:
    """Decoded video frame count, and the audio resampled to ``rate`` (mono) with the codec's frame size."""
    with av.open(str(video)) as container:
        stream = container.streams.audio[0]
        codec = {"name": stream.codec_context.name, "rate": stream.rate, "frame_size": stream.codec_context.frame_size}
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        chunks = []
        for frame in container.decode(audio=0):
            chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(frame))
        chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(None))
    with av.open(str(video)) as container:
        frames = sum(1 for _ in container.decode(video=0))
    return {"audio": np.concatenate(chunks), "video_frames": frames, "codec": codec}


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def _correlation_scores(reference: np.ndarray, track: np.ndarray) -> np.ndarray:
    size = len(track) + len(reference)
    return np.fft.irfft(np.fft.rfft(track.astype(float), size) * np.conj(np.fft.rfft(reference.astype(float), size)), size)


def locate(reference: np.ndarray, track: np.ndarray) -> int:
    """Sample offset in ``track`` where ``reference`` matches best (reference fully inside track)."""
    if len(reference) > len(track):
        return 0
    return int(np.argmax(_correlation_scores(reference, track)[: len(track) - len(reference) + 1]))


def measure_offset(reference: np.ndarray, delayed: np.ndarray) -> int:
    """Signed lag, in samples, of ``delayed`` relative to ``reference`` (positive: ``delayed`` is later)."""
    scores = _correlation_scores(reference, delayed)
    lag = int(np.argmax(scores))
    return lag - len(scores) if lag > len(scores) // 2 else lag


def correlation(first: np.ndarray, second: np.ndarray) -> float:
    length = min(len(first), len(second))
    a, b = first[:length].astype(float), second[:length].astype(float)
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / norm) if norm else 0.0


def final_voiced_second(source: np.ndarray, rate: int) -> tuple[int, int]:
    voiced = np.flatnonzero(np.abs(source.astype(int)) > VOICED_LEVEL)
    last = int(voiced[-1]) if len(voiced) else len(source) - 1
    return max(0, last - rate), last + 1


def check(name: str, passed: bool, **details: object) -> dict[str, object]:
    return {"check": name, "passed": bool(passed), **details}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def measure_cue(cue: dict, source: np.ndarray, track: np.ndarray, decoded: np.ndarray, offset: int, rate: int) -> dict:
    onset = locate(source, track)
    mp4_onset = locate(source, decoded)
    start, stop = final_voiced_second(source, rate)
    return {
        "cue_id": cue["cue_id"],
        "expected_onset_seconds": cue["audio_onset_seconds"],
        "soundtrack_onset_seconds": onset / rate,
        "soundtrack_end_seconds": (onset + len(source)) / rate,
        "present_unaltered": bool(np.array_equal(track[onset : onset + len(source)], source)),
        "mp4_onset_seconds": mp4_onset / rate,
        "mp4_onset_after_offset_seconds": (mp4_onset - offset) / rate,
        "mp4_whole_cue_correlation": correlation(decoded[mp4_onset : mp4_onset + len(source)], source),
        "mp4_final_voiced_second_correlation": correlation(decoded[mp4_onset + start : mp4_onset + stop], source[start:stop]),
        "final_voiced_sample_seconds": (onset + stop - 1) / rate,
    }


def cue_checks(measured: dict, tolerance: float) -> list[dict[str, object]]:
    cue = measured["cue_id"]
    expected = measured["expected_onset_seconds"]
    return [
        check(f"{cue}: recording present sample for sample in the soundtrack", measured["present_unaltered"]),
        check(
            f"{cue}: soundtrack onset within tolerance",
            abs(measured["soundtrack_onset_seconds"] - expected) <= tolerance,
            measured=measured["soundtrack_onset_seconds"],
            expected=expected,
        ),
        check(
            f"{cue}: MP4 onset within tolerance after the measured codec offset",
            abs(measured["mp4_onset_after_offset_seconds"] - expected) <= tolerance,
            measured=measured["mp4_onset_after_offset_seconds"],
            expected=expected,
        ),
        check(
            f"{cue}: whole cue correlates with the recording in the MP4",
            measured["mp4_whole_cue_correlation"] > CORRELATION_THRESHOLD,
            measured=measured["mp4_whole_cue_correlation"],
        ),
        check(
            f"{cue}: final voiced second correlates with the recording in the MP4",
            measured["mp4_final_voiced_second_correlation"] > CORRELATION_THRESHOLD,
            measured=measured["mp4_final_voiced_second_correlation"],
        ),
    ]


def order_check(before: dict, after: dict) -> dict[str, object]:
    """A cue must finish before the next starts; only measurable when both were found unaltered."""
    name = f"{before['cue_id']} -> {after['cue_id']}: first recording finishes before the next starts"
    missing = [cue["cue_id"] for cue in (before, after) if not cue["present_unaltered"]]
    if missing:
        return check(name, False, not_measurable=f"not found in the soundtrack: {', '.join(missing)}")
    return check(
        name,
        before["soundtrack_end_seconds"] <= after["soundtrack_onset_seconds"],
        end=before["soundtrack_end_seconds"],
        next_onset=after["soundtrack_onset_seconds"],
    )


def ending_checks(cues: list[dict], video_seconds: float, offset_seconds: float, decoded_seconds: float) -> list[dict]:
    checks = [order_check(before, after) for before, after in zip(cues, cues[1:])]
    last_end = cues[-1]["soundtrack_end_seconds"]
    checks.append(
        check(
            f"{cues[-1]['cue_id']}: last recording finishes before the video ends",
            last_end <= video_seconds,
            end=last_end,
            video_seconds=video_seconds,
        )
    )
    checks.append(
        check(
            f"{cues[-1]['cue_id']}: last recording finishes before the video ends in the MP4, including the codec offset",
            last_end + offset_seconds <= min(video_seconds, decoded_seconds),
            end=last_end + offset_seconds,
            video_seconds=video_seconds,
            mp4_audio_seconds=decoded_seconds,
        )
    )
    return checks


def codec_check(offset: int, rate: int, codec: dict) -> dict[str, object]:
    """The measured offset must be one codec frame (the AAC encoder delay), within one measured sample."""
    codec_frame_seconds = codec["frame_size"] / codec["rate"]
    return check(
        CODEC_CHECK,
        abs(offset / rate - codec_frame_seconds) <= 1 / rate,
        measured_seconds=offset / rate,
        measured_samples=offset,
        codec_frame_seconds=codec_frame_seconds,
        codec=codec,
    )


def cache_checks(expected: dict, before: dict, after: dict, live: dict) -> list[dict[str, object]]:
    recordings = {cue["cue_id"]: expected["recordings"][cue["cue_id"]]["sha256"] for cue in expected["cues"]}
    before_hashes = {copy["sha256"] for copy in before["copies"].values()}
    after_hashes = {copy["sha256"] for copy in after["copies"].values()}
    new = {name: after["copies"][name]["sha256"] for name in set(after["copies"]) - set(before["copies"])}
    expected_new = sorted(set(recordings.values()) - before_hashes)
    rewritten = [name for name, copy in before["copies"].items() if after["copies"].get(name) != copy]
    changed_since = [name for name, copy in after["copies"].items() if live["copies"].get(name) != copy]
    entries = after["cache_json_entries"] - before["cache_json_entries"]
    return [
        check(
            "cache: every cue's recording has a cached copy with its bytes",
            set(recordings.values()) <= after_hashes,
            missing=sorted(cue for cue, digest in recordings.items() if digest not in after_hashes),
        ),
        check(
            "cache: new copies are exactly the recordings not cached before the render",
            sorted(new.values()) == expected_new,
            new_copies=sorted(new),
            expected_new_hashes=expected_new,
        ),
        check("cache: copies cached before the render were not rewritten", not rewritten, rewritten=rewritten),
        check("cache: one cache.json entry per cue", entries == len(expected["cues"]), added_entries=entries),
        check("cache: recorded copies are unchanged now", not changed_since, changed=changed_since),
    ]


def _sha256_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def record_checks(record: Path, expected: dict, work: Path, video: Path) -> list[dict[str, object]]:
    declared = [record / "expected.json", record / "cache-before.json"]
    current = {cue: sha256_file(work / info["wav"]) for cue, info in expected["recordings"].items()}
    before = _sha256_lines(record / "sources-before.sha256")
    return [
        check(
            RECORD_CHECKS[0],
            all(path.exists() and path.stat().st_mtime_ns < video.stat().st_mtime_ns for path in declared),
        ),
        check(
            RECORD_CHECKS[1],
            bool(before) and before == _sha256_lines(record / "sources-after.sha256"),
            files=len(before),
        ),
        check(
            RECORD_CHECKS[2],
            all(current[cue] == info["sha256"] for cue, info in expected["recordings"].items()),
        ),
    ]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(record: Path, video: Path, work: Path, cache: Path, expected_path: Path | None = None) -> dict[str, object]:
    """Measure ``video`` and compare it with ``record``'s pre-render expectations (or ``expected_path``)."""
    expected = json.loads((expected_path or record / "expected.json").read_text(encoding="utf-8"))
    track, rate = read_pcm16(video.with_suffix(".wav"))
    mp4 = decode_mp4(video, rate)
    offset = measure_offset(track, mp4["audio"])
    cues = []
    for cue in expected["cues"]:
        source, source_rate = read_pcm16(work / expected["recordings"][cue["cue_id"]]["wav"])
        if source_rate != rate:
            raise ValueError(f"{cue['cue_id']}: recording rate {source_rate} differs from the soundtrack rate {rate}")
        cues.append(measure_cue(cue, source, track, mp4["audio"], offset, rate))
    video_seconds = mp4["video_frames"] / expected["fps"]
    checks = [check for cue in cues for check in cue_checks(cue, expected["tolerance_seconds"])]
    checks += ending_checks(cues, video_seconds, offset / rate, len(mp4["audio"]) / rate)
    checks.append(codec_check(offset, rate, mp4["codec"]))
    checks.append(
        check(
            "video frame count equals the expected frame count",
            mp4["video_frames"] == expected["video_frames"],
            measured=mp4["video_frames"],
            expected=expected["video_frames"],
        )
    )
    before = json.loads((record / "cache-before.json").read_text(encoding="utf-8"))
    after = json.loads((record / "cache-after.json").read_text(encoding="utf-8"))
    checks += cache_checks(expected, before, after, snapshot_cache(cache))
    checks += record_checks(record, expected, work, video)
    failures = [item["check"] for item in checks if not item["passed"]]
    return {
        "record": str(record),
        "video": str(video),
        "expected": str(expected_path or record / "expected.json"),
        "passed": not failures,
        "failures": failures,
        "fps": expected["fps"],
        "tolerance_seconds": expected["tolerance_seconds"],
        "codec_offset_seconds": offset / rate,
        "video_frames": mp4["video_frames"],
        "video_seconds": video_seconds,
        "cues": cues,
        "checks": checks,
    }


def _involves(name: str, cues: Sequence[str]) -> bool:
    """Whether a check is about one of ``cues`` (per-cue checks "cue: ..." and pair checks "a -> b: ...")."""
    subject = name.split(":", 1)[0]
    return any(part.strip() in cues for part in subject.split("->"))


def mismatch_report(result: dict, cue: str) -> dict[str, object]:
    """Whether a failed check against other expectations is explained specifically by ``cue``'s recording."""
    order = [measured["cue_id"] for measured in result["cues"]]
    if cue not in order:
        raise ValueError(f"{cue!r} is not a cue of the expected timeline: {order}")
    affected = order[order.index(cue) :]
    failures = set(result["failures"])
    required = {
        f"{cue}: recording present sample for sample in the soundtrack",
        f"{cue}: whole cue correlates with the recording in the MP4",
    }
    may_fail = {
        name
        for name in (item["check"] for item in result["checks"])
        if _involves(name, affected) or name.startswith(("cache:", "video frame count"))
    }
    must_pass = [name for name in (item["check"] for item in result["checks"]) if name not in may_fail]
    unexpected = sorted(failures - may_fail)
    specific = not result["passed"] and required <= failures and not unexpected
    shifts = {
        found["cue_id"]: round((found["soundtrack_onset_seconds"] - found["expected_onset_seconds"]) * result["fps"], 3)
        for found in result["cues"]
        if found["present_unaltered"]
    }
    return {
        "cue": cue,
        "specific": specific,
        "required_failures": sorted(required),
        "missing_required_failures": sorted(required - failures),
        "unexpected_failures": unexpected,
        "checks_that_still_passed": len(must_pass),
        "found_cue_onset_minus_expected_frames": shifts,
    }


def default_output(record: Path) -> Path:
    """The record's verification.json the first time; afterwards a new file in MEDIA/checks (never overwritten)."""
    first = record / "verification.json"
    if not first.exists():
        return first
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return record.resolve().parent.parent / "checks" / f"verify-{record.name}-{stamp}.json"


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:  # never overwrite an existing result
        handle.write(text)


def default_paths(record: Path) -> tuple[Path, Path, Path]:
    """Video, working folder and cache of a record in the scripts/s02.sh layout (MEDIA/runs/NAME)."""
    media = record.resolve().parent.parent
    return media / LESSON_VIDEO_DIR / f"run-{record.name}.mp4", media / "cues", media / "voiceovers"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("verify")
    run.add_argument("record", type=Path)
    run.add_argument("--expected", type=Path, help="check against other expectations")
    run.add_argument("--expect-mismatch", metavar="CUE", help="with --expected: the check must fail specifically on CUE")
    run.add_argument("--output", type=Path, help="save the result to this new file")
    commands.add_parser("snapshot-cache").add_argument("cache", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "snapshot-cache":
        json.dump(snapshot_cache(arguments.cache), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if arguments.expect_mismatch and arguments.expected is None:
        parser.error("--expect-mismatch needs --expected")
    video, work, cache = default_paths(arguments.record)
    result = verify(arguments.record, video, work, cache, arguments.expected)
    ok = result["passed"]
    if arguments.expect_mismatch:
        result["mismatch"] = mismatch_report(result, arguments.expect_mismatch)
        ok = result["mismatch"]["specific"]
    destination = arguments.output or (default_output(arguments.record) if arguments.expected is None else None)
    text = json.dumps(result, indent=2) + "\n"
    if destination is not None:
        write_new(destination, text)
        result_note = f"saved to {destination}"
    else:
        result_note = "not saved"
    sys.stdout.write(text)
    for failure in result["failures"]:
        print(f"FAILED: {failure}", file=sys.stderr)
    outcome = "mismatch is specific" if arguments.expect_mismatch else "checks"
    print(f"{'PASSED' if ok else 'FAILED'}: {len(result['checks'])} {outcome}; {result_note}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
