"""S02: the render verifier (scripts/verify_s02_render.py).

Unit tests cover offset measurement and the codec-offset rule. The tests marked
``integration`` render the real lesson scene with SYNTHETIC recordings (band-limited
seeded noise), following the same record steps as scripts/s02.sh, and check that the
verifier passes correct renders and fails renders checked against the wrong
calculation recording, renders whose ending is cut, and rewritten cached copies. The
synthetic A -> B -> B flow tests the verifier's cache checks; it is not evidence for
the lesson's B render, which needs the approved alternate recording.
"""

import hashlib
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from manim import tempconfig
from scipy.signal import butter, lfilter

from tests.test_s02_expected_reward import LESSON_FILE, ROOT, load_module, render_settings

pytestmark = pytest.mark.skipif(
    not (ROOT / "scripts" / "verify_s02_render.py").is_file(),
    reason="examples and scripts are not copied into mutmut's generated worktree",
)
SECONDS = {"question": 1.3, "calculation": 0.9, "decision": 1.1}


@pytest.fixture(scope="module")
def verifier():
    return load_module("s02_verifier", ROOT / "scripts" / "verify_s02_render.py")


@pytest.fixture(scope="module")
def timeline():
    return load_module("s02_timeline_for_verifier", ROOT / "scripts" / "s02_timeline.py")


@pytest.fixture(scope="module")
def lesson():
    return load_module("s02_lesson_for_verifier", LESSON_FILE)


def write_band_limited(path, seconds, seed, rate=22050):
    """Seeded noise below 3 kHz: unambiguous to locate and preserved well by AAC."""
    noise = np.random.default_rng(seed).standard_normal(int(seconds * rate))
    b, a = butter(4, 3000 / (rate / 2))
    samples = np.clip(lfilter(b, a, noise) * 6000, -32000, 32000).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())
    return path


def write_manifest(directory, bindings, name):
    path = directory / name
    path.write_text(json.dumps({"version": 1, "cues": [{"id": c, "wav": w} for c, w in bindings.items()]}), encoding="utf-8")
    return path


def sources_lines(directory):
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(directory.iterdir()))


class Pipeline:
    """scripts/s02.sh's record steps around a real render of the lesson scene, in a temporary media folder."""

    def __init__(self, base, lesson, timeline, verifier):
        self.base, self.lesson, self.timeline, self.verifier = base, lesson, timeline, verifier
        self.work = base / "cues"
        self.cache = base / "voiceovers"
        self.work.mkdir(parents=True)
        for seed, cue in enumerate(SECONDS):
            write_band_limited(self.work / f"{cue}.wav", SECONDS[cue], seed=seed)
        self.manifest_a = write_manifest(self.work, {c: f"{c}.wav" for c in SECONDS}, "manifest-a.json")

    def render(self, name, manifest, hold=None, scene_class=None):
        record = self.base / "runs" / name
        record.mkdir(parents=True)
        (record / "expected.json").write_text(json.dumps(self.timeline.timeline_for_manifest(manifest)), encoding="utf-8")
        (record / "sources-before.sha256").write_text(sources_lines(self.work), encoding="utf-8")
        (record / "cache-before.json").write_text(json.dumps(self.verifier.snapshot_cache(self.cache)), encoding="utf-8")
        previous_hold, previous_manifest = self.lesson.FINAL_HOLD_SECONDS, os.environ.get("S02_CUE_MANIFEST")
        os.environ["S02_CUE_MANIFEST"] = str(manifest)
        if hold is not None:
            self.lesson.FINAL_HOLD_SECONDS = hold
        try:
            with tempconfig({**render_settings(self.base), "media_dir": str(self.base), "output_file": f"run-{name}"}):
                scene = (scene_class or self.lesson.ExpectedRewardCues)()
                scene.render()
                video = Path(scene.renderer.file_writer.movie_file_path)
        finally:
            self.lesson.FINAL_HOLD_SECONDS = previous_hold
            if previous_manifest is None:
                os.environ.pop("S02_CUE_MANIFEST", None)
            else:
                os.environ["S02_CUE_MANIFEST"] = previous_manifest
        (record / "sources-after.sha256").write_text(sources_lines(self.work), encoding="utf-8")
        (record / "cache-after.json").write_text(json.dumps(self.verifier.snapshot_cache(self.cache)), encoding="utf-8")
        return record, video

    def verify(self, record, video, expected=None):
        return self.verifier.verify(record, video, self.work, self.cache, expected)


def failed(result):
    return set(result["failures"])


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shift", [0, 1, 470, 1234, -300])
def test_offset_is_measured_from_the_signals(verifier, shift):
    reference = np.random.default_rng(5).integers(-5000, 5000, 20000).astype(np.int16)
    delayed = np.concatenate([np.zeros(shift, np.int16), reference]) if shift >= 0 else reference[-shift:]
    assert verifier.measure_offset(reference, delayed) == shift


@pytest.mark.parametrize(
    "samples, explained",
    [(470, True), (471, True), (469, False), (472, False), (0, False), (940, False), (-470, False)],
)
def test_codec_offset_must_equal_one_codec_frame_within_one_sample(verifier, samples, explained):
    codec = {"name": "aac", "rate": 48000, "frame_size": 1024}  # 470.4 samples at 22050 Hz: 470 and 471 are within one
    assert verifier.codec_check(samples, 22050, codec)["passed"] is explained


def test_locate_finds_a_reference_inside_a_track(verifier):
    reference = np.random.default_rng(2).integers(-5000, 5000, 3000).astype(np.int16)
    track = np.concatenate([np.zeros(12345, np.int16), reference, np.zeros(777, np.int16)])
    assert verifier.locate(reference, track) == 12345
    assert verifier.locate(track, reference) == 0  # a reference longer than the track is not inside it


def test_cache_checks_detect_rewritten_missing_and_unexpected_copies(verifier):
    copy = {"sha256": "a" * 64, "inode": 1, "size": 10, "mtime_ns": 1, "ctime_ns": 1}
    expected = {"cues": [{"cue_id": "q"}], "recordings": {"q": {"sha256": "a" * 64}}}
    before = {"copies": {"q.wav": copy}, "cache_json_entries": 1}
    rewritten = {"copies": {"q.wav": {**copy, "inode": 2}}, "cache_json_entries": 2}
    checks = {c["check"]: c["passed"] for c in verifier.cache_checks(expected, before, rewritten, rewritten)}
    assert checks["cache: copies cached before the render were not rewritten"] is False
    assert checks["cache: new copies are exactly the recordings not cached before the render"] is True

    extra = {"copies": {"q.wav": copy, "x.wav": {**copy, "sha256": "b" * 64}}, "cache_json_entries": 2}
    checks = {c["check"]: c["passed"] for c in verifier.cache_checks(expected, before, extra, extra)}
    assert checks["cache: new copies are exactly the recordings not cached before the render"] is False

    empty = {"copies": {}, "cache_json_entries": 0}
    checks = {c["check"]: c["passed"] for c in verifier.cache_checks(expected, empty, empty, empty)}
    assert checks["cache: every cue's recording has a cached copy with its bytes"] is False
    assert checks["cache: one cache.json entry per cue"] is False


# ---------------------------------------------------------------------------
# Real renders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered_a(tmp_path_factory, lesson, timeline, verifier):
    pipeline = Pipeline(tmp_path_factory.mktemp("s02") / "media", lesson, timeline, verifier)
    record, video = pipeline.render("01-a", pipeline.manifest_a)
    return pipeline, record, video


@pytest.mark.integration
def test_correct_render_passes_every_check(rendered_a):
    pipeline, record, video = rendered_a

    result = pipeline.verify(record, video)

    assert result["passed"], result["failures"]
    assert len(result["checks"]) == 29
    assert [cue["cue_id"] for cue in result["cues"]] == ["question", "calculation", "decision"]
    assert all(cue["present_unaltered"] for cue in result["cues"])


@pytest.mark.integration
def test_codec_offset_is_measured_and_matches_the_reported_codec_frame(rendered_a):
    pipeline, record, video = rendered_a
    codec = next(c for c in pipeline.verify(record, video)["checks"] if c["check"].startswith("codec offset"))

    assert codec["passed"]
    assert codec["measured_samples"] > 0
    assert codec["codec"]["frame_size"] / codec["codec"]["rate"] == codec["codec_frame_seconds"]
    assert abs(codec["measured_seconds"] - codec["codec_frame_seconds"]) <= 1 / 22050


@pytest.mark.integration
@pytest.mark.parametrize("alternate_seconds", [1.6, SECONDS["calculation"]], ids=["different length", "same length"])
def test_render_fails_against_the_wrong_calculation_recording(rendered_a, alternate_seconds):
    pipeline, record, video = rendered_a
    wrong = write_band_limited(pipeline.work / f"wrong_{alternate_seconds}.wav", alternate_seconds, seed=99)
    manifest = write_manifest(
        pipeline.work, {**{c: f"{c}.wav" for c in SECONDS}, "calculation": wrong.name}, f"wrong-{alternate_seconds}.json"
    )
    wrong_expected = record.parent / f"wrong-{alternate_seconds}.json"
    wrong_expected.write_text(json.dumps(pipeline.timeline.timeline_for_manifest(manifest)), encoding="utf-8")

    result = pipeline.verify(record, video, wrong_expected)

    assert not result["passed"]
    assert {
        "calculation: recording present sample for sample in the soundtrack",
        "calculation: whole cue correlates with the recording in the MP4",
        "cache: every cue's recording has a cached copy with its bytes",
    } <= failed(result)
    assert not any(failure.startswith("question:") for failure in failed(result))  # the question cue itself is fine
    assert "question -> calculation: first recording finishes before the next starts" in failed(result)
    order = next(c for c in result["checks"] if c["check"].startswith("question -> calculation"))
    assert order["not_measurable"] == "not found in the soundtrack: calculation"
    timing = {"decision: soundtrack onset within tolerance", "video frame count equals the expected frame count"}
    if alternate_seconds == SECONDS["calculation"]:
        assert not timing & failed(result)  # same length: only the content gives it away
    else:
        assert timing <= failed(result)


@pytest.mark.integration
def test_cut_ending_fails(lesson, timeline, verifier, tmp_path):
    class WithoutFinalHold(lesson.ExpectedRewardCues):
        """Test-only variant that drops the final hold, so the video stops up to one frame before the last recording.

        A shorter hold cannot do this: Manim raises any hold below one frame to one frame, which covers the block's
        sub-frame shortfall.
        """

        def wait(self, duration=1.0, **kwargs):
            if duration != lesson.FINAL_HOLD_SECONDS:
                super().wait(duration, **kwargs)

    pipeline = Pipeline(tmp_path / "media", lesson, timeline, verifier)
    record, video = pipeline.render("01-a", pipeline.manifest_a, scene_class=WithoutFinalHold)

    result = pipeline.verify(record, video)

    assert {
        "video frame count equals the expected frame count",
        "decision: last recording finishes before the video ends",
    } <= failed(result)


@pytest.mark.integration
def test_synthetic_a_b_b_shares_one_cache_and_detects_rewrites(lesson, timeline, verifier, tmp_path):
    pipeline = Pipeline(tmp_path / "media", lesson, timeline, verifier)
    write_band_limited(pipeline.work / "calculation_alt.wav", 1.6, seed=42)
    manifest_b = write_manifest(
        pipeline.work, {**{c: f"{c}.wav" for c in SECONDS}, "calculation": "calculation_alt.wav"}, "manifest-b.json"
    )
    runs = [pipeline.render(name, manifest) for name, manifest in [("01-a", pipeline.manifest_a), ("02-b", manifest_b)]]
    runs.append(pipeline.render("03-b", manifest_b))

    results = [pipeline.verify(record, video) for record, video in runs]
    assert [result["passed"] for result in results] == [True, True, True], [r["failures"] for r in results]
    new_copies = [next(c for c in r["checks"] if c["check"].startswith("cache: new copies"))["new_copies"] for r in results]
    assert [len(names) for names in new_copies] == [3, 1, 0]
    assert pipeline.verify(*runs[0])["passed"]  # A's record still holds after B and B again

    copy = pipeline.cache / new_copies[0][0]
    data = copy.read_bytes()
    copy.unlink()
    copy.write_bytes(data)  # same bytes, rewritten
    assert "cache: recorded copies are unchanged now" in failed(pipeline.verify(*runs[0]))


@pytest.mark.integration
def test_changed_recording_after_the_render_fails(lesson, timeline, verifier, tmp_path):
    pipeline = Pipeline(tmp_path / "media", lesson, timeline, verifier)
    record, video = pipeline.render("01-a", pipeline.manifest_a)
    write_band_limited(pipeline.work / "decision.wav", SECONDS["decision"], seed=7)

    assert "recordings are the ones the expected timeline was computed from" in failed(pipeline.verify(record, video))


@pytest.mark.integration
def test_verifier_only_writes_its_own_result(rendered_a, verifier, monkeypatch):
    pipeline, record, video = rendered_a

    def state():
        files = sorted(p for p in [*record.iterdir(), *pipeline.cache.iterdir(), *pipeline.work.iterdir()])
        return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), os.stat(p).st_mtime_ns) for p in files}

    before = state()
    monkeypatch.setattr(verifier, "default_paths", lambda _: (video, pipeline.work, pipeline.cache))
    assert verifier.main(["verify", str(record), "--expected", str(record / "expected.json")]) == 0
    assert state() == before  # with --expected nothing is written

    assert verifier.main(["verify", str(record)]) == 0
    after = state()
    written = set(after) - set(before)
    assert written == {str(record / "verification.json")}
    assert {key: after[key] for key in before} == before
    assert json.loads((record / "verification.json").read_text(encoding="utf-8"))["passed"] is True
    (record / "verification.json").unlink()


def test_snapshot_format_matches_the_recorded_cache_files(verifier, tmp_path):
    (tmp_path / "x.wav").write_bytes(b"RIFF")
    (tmp_path / "cache.json").write_text("[{}, {}]", encoding="utf-8")

    snapshot = verifier.snapshot_cache(tmp_path)

    assert snapshot["cache_json_entries"] == 2
    assert set(snapshot["copies"]["x.wav"]) == {"sha256", "inode", "size", "mtime_ns", "ctime_ns"}
    assert sys.modules["s02_verifier"] is verifier


@pytest.mark.integration
def test_verify_uses_the_measured_codec_offset_not_a_constant(rendered_a, verifier, monkeypatch):
    pipeline, record, video = rendered_a
    monkeypatch.setattr(verifier, "measure_offset", lambda reference, delayed: 0)

    result = pipeline.verify(record, video)

    assert result["codec_offset_seconds"] == 0
    assert "codec offset is measured and equals one audio codec frame" in failed(result)


@pytest.mark.integration
@pytest.mark.parametrize("shift_frames, passes", [(0.5, True), (-0.5, True), (1.5, False), (-1.5, False)])
def test_onset_tolerance_is_exactly_the_predeclared_frame(rendered_a, tmp_path, shift_frames, passes):
    pipeline, record, video = rendered_a
    expected = json.loads((record / "expected.json").read_text(encoding="utf-8"))
    assert expected["tolerance_seconds"] == 1 / expected["fps"]
    expected["cues"][1]["audio_onset_seconds"] += shift_frames / expected["fps"]
    moved = tmp_path / "moved.json"
    moved.write_text(json.dumps(expected), encoding="utf-8")

    result = pipeline.verify(record, video, moved)

    onset_checks = {
        "calculation: soundtrack onset within tolerance",
        "calculation: MP4 onset within tolerance after the measured codec offset",
    }
    assert (not onset_checks & failed(result)) is passes
    assert result["passed"] is passes


# ---------------------------------------------------------------------------
# Specific mismatch and never-overwritten results
# ---------------------------------------------------------------------------

ORDER = ["question", "calculation", "decision"]
PRESENT, CORRELATES = (
    "recording present sample for sample in the soundtrack",
    "whole cue correlates with the recording in the MP4",
)
REQUIRED = [f"calculation: {PRESENT}", f"calculation: {CORRELATES}"]
DOWNSTREAM = [
    "decision: last recording finishes before the video ends",
    "video frame count equals the expected frame count",
    "cache: every cue's recording has a cached copy with its bytes",
    "question -> calculation: first recording finishes before the next starts",
]
CODEC = "codec offset is measured and equals one audio codec frame"
SOURCES = "sources unchanged during the render"


def fabricated(failures):
    names = [f"{cue}: {what}" for cue in ORDER for what in (PRESENT, CORRELATES)]
    names += [*DOWNSTREAM, CODEC, SOURCES, "expectations were recorded before the video was rendered"]
    return {
        "fps": 15,
        "passed": not failures,
        "failures": list(failures),
        "checks": [{"check": name, "passed": name not in failures} for name in names],
        "cues": [
            {
                "cue_id": cue,
                "present_unaltered": cue != "calculation",
                "soundtrack_onset_seconds": 1.0,
                "expected_onset_seconds": 1.0,
            }
            for cue in ORDER
        ],
    }


@pytest.mark.parametrize(
    "failures, specific",
    [
        (REQUIRED, True),
        ([*REQUIRED, *DOWNSTREAM], True),
        ([*REQUIRED, f"question: {PRESENT}"], False),  # an earlier cue failed
        ([*REQUIRED, CODEC], False),
        ([*REQUIRED, SOURCES], False),
        ([REQUIRED[1]], False),  # the recording was found sample for sample: not the wrong recording
        ([], False),  # nothing failed
    ],
    ids=["required only", "with downstream", "earlier cue", "codec", "sources", "recording found", "passed"],
)
def test_mismatch_is_specific_only_when_explained_by_the_cue(verifier, failures, specific):
    assert verifier.mismatch_report(fabricated(failures), "calculation")["specific"] is specific


def test_mismatch_report_rejects_an_unknown_cue(verifier):
    with pytest.raises(ValueError, match="'method' is not a cue"):
        verifier.mismatch_report(fabricated(REQUIRED), "method")


def test_results_are_never_overwritten(verifier, tmp_path):
    record = tmp_path / "media" / "runs" / "01-a"
    record.mkdir(parents=True)
    assert verifier.default_output(record) == record / "verification.json"
    verifier.write_new(record / "verification.json", "{}")

    again = verifier.default_output(record)
    assert again.parent == tmp_path / "media" / "checks" and again.name.startswith("verify-01-a-")
    with pytest.raises(FileExistsError):
        verifier.write_new(record / "verification.json", "{}")
    assert (record / "verification.json").read_text() == "{}"


def test_expect_mismatch_needs_other_expectations(verifier, tmp_path):
    with pytest.raises(SystemExit):
        verifier.main(["verify", str(tmp_path), "--expect-mismatch", "calculation"])


@pytest.mark.integration
def test_synthetic_b_fails_specifically_against_a_and_the_reverse(lesson, timeline, verifier, tmp_path):
    pipeline = Pipeline(tmp_path / "media", lesson, timeline, verifier)
    write_band_limited(pipeline.work / "calculation_alt.wav", 1.6, seed=42)
    manifest_b = write_manifest(
        pipeline.work, {**{c: f"{c}.wav" for c in SECONDS}, "calculation": "calculation_alt.wav"}, "manifest-b.json"
    )
    a = pipeline.render("01-a", pipeline.manifest_a)
    b = pipeline.render("02-b", manifest_b)

    b_against_a = pipeline.verify(*b, a[0] / "expected.json")
    a_against_b = pipeline.verify(*a, b[0] / "expected.json")
    a_against_a = pipeline.verify(*a, a[0] / "expected.json")

    for result in (b_against_a, a_against_b):
        report = verifier.mismatch_report(result, "calculation")
        assert report["specific"], report
        shift = timeline.compare_timelines(
            json.loads((a[0] / "expected.json").read_text()), json.loads((b[0] / "expected.json").read_text())
        )["decision"]["start_shift_frames"]
        found = report["found_cue_onset_minus_expected_frames"]
        assert found["question"] == 0
        assert abs(found["decision"]) == pytest.approx(abs(shift), abs=0.01) and abs(shift) >= 2
    assert verifier.mismatch_report(a_against_a, "calculation")["specific"] is False
