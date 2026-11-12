"""S02: the text-only expected-reward lesson and its independent expected timeline.

Covers the scene (cue order, transcripts, text-only panels, tracked manifest, failing
before rendering), the timeline model in scripts/s02_timeline.py, and, in the tests
marked ``integration``, real Manim renders of the lesson scene compared with timelines
computed before rendering.
"""

import importlib.util
import json
import sys
import wave
from pathlib import Path

import av
import numpy as np
import pytest
from manim import MathTex, Tex, Text, VGroup, tempconfig
from manim.constants import QUALITIES

from manim_voiceover.services.cue_bindings import load_cue_manifest
from tests.test_local_wav_service import sha256

ROOT = Path(__file__).resolve().parents[1]
LESSON_FILE = ROOT / "examples" / "rl_lessons" / "expected_reward_cues.py"
ASSETS = ROOT / "examples" / "rl_lessons" / "assets"
pytestmark = pytest.mark.skipif(
    not LESSON_FILE.is_file(), reason="examples and scripts are not copied into mutmut's generated worktree"
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lesson():
    return load_module("s02_expected_reward_lesson", LESSON_FILE)


@pytest.fixture(scope="module")
def timeline():
    return load_module("s02_timeline", ROOT / "scripts" / "s02_timeline.py")


# Frame counts of sections 0, 2 and 3 (mono, 16-bit PCM, 22050 Hz), from S02_EXPORT_PROVENANCE.json.
LESSON_FRAMES = {"question": 265874, "calculation": 161039, "decision": 317010}


def write_noise(path, seconds, seed, rate=22050):
    """Seeded noise: a recording whose position in a soundtrack is unambiguous."""
    samples = np.random.default_rng(seed).integers(-6000, 6000, int(seconds * rate), dtype=np.int16)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.astype("<i2").tobytes())
    return path


def read_pcm16_mono(path):
    with wave.open(str(path), "rb") as audio:
        assert audio.getsampwidth() == 2
        data = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        return data.reshape(-1, audio.getnchannels())[:, 0], audio.getframerate()


def locate_exactly(source, track):
    """Sample offset of ``source`` in ``track``, found by correlation and confirmed sample for sample."""
    scores = np.fft.irfft(
        np.fft.rfft(track.astype(float), len(track) + len(source))
        * np.conj(np.fft.rfft(source.astype(float), len(track) + len(source)))
    )
    offset = int(np.argmax(scores[: len(track) - len(source) + 1]))
    assert np.array_equal(track[offset : offset + len(source)], source), "recording is not present unaltered"
    return offset


def video_frame_count(movie):
    with av.open(str(movie)) as container:
        return sum(1 for _ in container.decode(video=0))


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------


def test_scene_decides_the_three_cue_order(lesson):
    assert lesson.CUE_ORDER == ("question", "calculation", "decision")


def test_transcripts_are_sections_0_2_3_verbatim(lesson):
    sections = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))["sections"]
    assert [lesson.TRANSCRIPTS[cue] for cue in lesson.CUE_ORDER] == [sections[0], sections[2], sections[3]]


def test_tracked_manifest_binds_sections_0_2_3(lesson):
    manifest = load_cue_manifest(lesson.DEFAULT_MANIFEST)

    assert lesson.DEFAULT_MANIFEST == ASSETS / "expected_reward_cues.json"
    assert dict(manifest.bindings) == {
        "question": "section_0.wav",
        "calculation": "section_2.wav",
        "decision": "section_3.wav",
    }
    cues = manifest.resolve(lesson.CUE_ORDER)
    assert [cue.sha256 for cue in cues] == [
        "054e095cc4176e37fdfd48a2e8d5024c7b6a634e354d598e2b37f861c934a6d7",
        "23a264b385bab216e832b3d7b98e9b09e96237b43beeafc063284ae1874f20b2",
        "bae895aab3f02e806087829c8fe68c88626a8722769ad5cfd1f8c5174b1a9c4a",
    ]


def panel_texts(panel):
    return ["".join(text.text.split()) for text in panel.submobjects]


@pytest.mark.parametrize("cue_id", ["question", "calculation", "decision"])
def test_panels_are_text_only(lesson, cue_id):
    panel = lesson.build_panel(cue_id)

    assert isinstance(panel, VGroup) and panel.submobjects
    assert all(type(part) is Text for part in panel.submobjects)
    assert not any(isinstance(part, (MathTex, Tex)) for part in panel.family_members_with_points())
    assert panel.width <= lesson.MAX_PANEL_WIDTH + 1e-6


def test_panel_content(lesson):
    assert panel_texts(lesson.build_panel("question")) == [
        "Whichactionshouldwechoose?",
        "A:reward2,everytime",
        "B:reward4withprobability0.75;otherwise0",
    ]
    assert "0.25(0)+0.75(4)=3" in panel_texts(lesson.build_panel("calculation"))
    assert panel_texts(lesson.build_panel("decision"))[0] == "3>2"


def test_unknown_panel_is_rejected(lesson):
    with pytest.raises(ValueError, match="^No panel for cue 'method'$"):
        lesson.build_panel("method")


def test_scene_layout_matches_the_independently_declared_timeline(lesson, timeline):
    assert (lesson.FADE_IN_SECONDS, lesson.FADE_OUT_SECONDS, lesson.FINAL_HOLD_SECONDS) == (
        timeline.FADE_IN_SECONDS,
        timeline.FADE_OUT_SECONDS,
        timeline.FINAL_HOLD_SECONDS,
    )
    assert lesson.CUE_ORDER == timeline.CUE_ORDER
    assert QUALITIES["low_quality"]["frame_rate"] == timeline.FPS  # scripts/s02.sh renders with -ql


def render_settings(tmp_path):
    return {
        "media_dir": str(tmp_path / "media"),
        "pixel_height": 54,
        "pixel_width": 96,
        "frame_rate": 15,
        "disable_caching": True,
        "output_file": "lesson",
        "verbosity": "ERROR",
        "progress_bar": "none",
    }


def test_invalid_binding_fails_before_anything_is_rendered_or_cached(lesson, tmp_path, monkeypatch):
    directory = tmp_path / "cues"
    directory.mkdir()
    write_noise(directory / "question.wav", 0.6, seed=1)
    write_noise(directory / "decision.wav", 0.6, seed=3)
    manifest = directory / "cues.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "cues": [
                    {"id": "question", "wav": "question.wav"},
                    {"id": "calculation", "wav": "calculation.wav"},
                    {"id": "decision", "wav": "decision.wav"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("S02_CUE_MANIFEST", str(manifest))

    with tempconfig(render_settings(tmp_path)):
        with pytest.raises(FileNotFoundError, match="not found or not a regular file"):
            lesson.ExpectedRewardCues().render()

    assert not (tmp_path / "media" / "voiceovers").exists()
    assert not list((tmp_path / "media").rglob("*.mp4"))


# ---------------------------------------------------------------------------
# Independent timeline model
# ---------------------------------------------------------------------------


def test_frame_rules(timeline):
    assert timeline.animation_frames(0.4) == 6
    assert timeline.animation_frames(0.3) == 5
    assert timeline.static_wait_frames(0.25) == 3
    assert timeline.TOLERANCE_FRAMES == 1


@pytest.mark.parametrize("seconds", [0.001, 0.05, 1 / 15])
def test_run_times_shorter_than_a_frame_become_one_frame(timeline, seconds):
    # Manim's Scene.validate_run_time raises them to one frame (measured: a 0.05 s hold wrote one frame).
    assert timeline.animation_frames(seconds) == 1
    assert timeline.static_wait_frames(seconds) == 1


@pytest.mark.parametrize("seconds", [0, -0.1])
def test_run_times_of_zero_or_less_are_rejected_like_manim(timeline, seconds):
    with pytest.raises(ValueError, match="cannot render"):
        timeline.static_wait_frames(seconds)


def lesson_a_durations():
    return [(cue, frames / 22050) for cue, frames in LESSON_FRAMES.items()]


def test_expected_timeline_for_lesson_a(timeline):
    expected = timeline.expected_timeline(lesson_a_durations())

    assert [cue["start_frame"] for cue in expected["cues"]] == [0, 185, 299]
    assert [cue["block_end_frame"] for cue in expected["cues"]] == [180, 294, 514]
    assert expected["video_frames"] == 517
    assert expected["no_overlap"] and expected["last_recording_completes"]
    assert expected["final_margin_seconds"] == pytest.approx(0.1568, abs=1e-4)
    assert expected["layout"]["last_panel_fades_out"] is False


def test_timeline_for_tracked_manifest_reads_the_recordings_directly(lesson, timeline):
    expected = timeline.timeline_for_manifest(lesson.DEFAULT_MANIFEST)

    assert [expected["recordings"][cue]["frames"] for cue in timeline.CUE_ORDER] == list(LESSON_FRAMES.values())
    assert expected["cues"] == timeline.expected_timeline(lesson_a_durations())["cues"]


@pytest.mark.parametrize("calculation_seconds", [5.0, 161039 / 22050, 9.8], ids=["shorter", "same", "longer"])
def test_independent_timelines_differ_only_after_the_rebound_cue(timeline, calculation_seconds):
    """SYNTHETIC durations only: checks the model, not evidence for a B recording."""
    first = timeline.expected_timeline(lesson_a_durations())
    second = timeline.expected_timeline(
        [("question", LESSON_FRAMES["question"] / 22050), ("calculation", calculation_seconds), *lesson_a_durations()[2:]]
    )
    comparison = timeline.compare_timelines(first, second)

    def calculation_span(expected):  # frames from the calculation's start to the decision's start
        cues = {cue["cue_id"]: cue for cue in expected["cues"]}
        return cues["decision"]["start_frame"] - cues["calculation"]["start_frame"]

    assert comparison["question"]["start_shift_frames"] == 0
    assert comparison["calculation"]["start_shift_frames"] == 0
    assert comparison["decision"]["start_shift_frames"] == calculation_span(second) - calculation_span(first)
    assert second["no_overlap"] and second["last_recording_completes"]


def test_model_rejects_a_zero_final_hold_like_manim(timeline, monkeypatch):
    monkeypatch.setattr(timeline, "FINAL_HOLD_SECONDS", 0.0)
    with pytest.raises(ValueError, match="cannot render a run time of 0 seconds"):
        timeline.expected_timeline(lesson_a_durations())


@pytest.mark.parametrize("hold", [0.01, 1 / 15, 0.1, 0.25])
def test_any_valid_final_hold_covers_the_last_blocks_shortfall(timeline, monkeypatch, hold):
    # The last block ends less than one frame before its recording; Manim renders every hold as at least one frame.
    monkeypatch.setattr(timeline, "FINAL_HOLD_SECONDS", hold)
    expected = timeline.expected_timeline(lesson_a_durations())
    last = expected["cues"][-1]
    assert 0 < last["audio_end_seconds"] - last["block_end_seconds"] < 1 / 15
    assert expected["last_recording_completes"] is True


# ---------------------------------------------------------------------------
# Real renders of the lesson scene against timelines computed before rendering
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "seconds",
    [
        {"question": 1.3, "calculation": 0.9, "decision": 1.1},
        {"question": 0.77, "calculation": 1.234, "decision": 0.95},
    ],
    ids=["set-1", "set-2"],
)
def test_real_render_matches_the_expected_timeline(lesson, timeline, tmp_path, monkeypatch, seconds):
    directory = tmp_path / "cues"
    directory.mkdir()
    for seed, cue in enumerate(timeline.CUE_ORDER):
        write_noise(directory / f"{cue}.wav", seconds[cue], seed=seed)
    manifest = directory / "cues.json"
    manifest.write_text(
        json.dumps({"version": 1, "cues": [{"id": cue, "wav": f"{cue}.wav"} for cue in reversed(timeline.CUE_ORDER)]}),
        encoding="utf-8",
    )
    sources = {path.name: sha256(path) for path in directory.iterdir()}
    expected = timeline.timeline_for_manifest(manifest)  # computed before rendering
    monkeypatch.setenv("S02_CUE_MANIFEST", str(manifest))

    with tempconfig(render_settings(tmp_path)):
        scene = lesson.ExpectedRewardCues()
        scene.render()
        movie = Path(scene.renderer.file_writer.movie_file_path)

    track, rate = read_pcm16_mono(movie.with_suffix(".wav"))  # Manim's uncompressed soundtrack
    tolerance = expected["tolerance_frames"] / expected["fps"]
    for cue in expected["cues"]:
        source, source_rate = read_pcm16_mono(directory / f"{cue['cue_id']}.wav")
        assert source_rate == rate
        onset = locate_exactly(source, track) / rate
        assert abs(onset - cue["audio_onset_seconds"]) <= tolerance, (cue["cue_id"], onset, cue["audio_onset_seconds"])
    assert video_frame_count(movie) == expected["video_frames"]
    assert expected["no_overlap"] and expected["last_recording_completes"]
    assert {path.name: sha256(path) for path in directory.iterdir()} == sources


# ---------------------------------------------------------------------------
# The candidate alternate calculation take (fixtures/s02)
# ---------------------------------------------------------------------------

ALTERNATE = ROOT / "fixtures" / "s02" / "calculation_alt.wav"
ALTERNATE_TEXT = "One quarter times zero, plus three quarters times four, gives an expected reward of three"


def test_candidate_take_matches_its_provenance_and_the_requested_format():
    info = json.loads((ALTERNATE.parent / "info.json").read_text(encoding="utf-8"))
    recorded = info["calculation_alt.wav"]
    with wave.open(str(ALTERNATE), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22050)
        frames = audio.getnframes()
    assert sha256(ALTERNATE) == recorded["sha256"] and frames == recorded["frames"]
    assert (ALTERNATE.parent / "calculation_alt.txt").read_text(encoding="utf-8") == ALTERNATE_TEXT
    assert info["status"].startswith("CANDIDATE")


def test_candidate_take_is_a_plain_wav_that_s01_accepts_without_ffmpeg():
    from manim_voiceover.services.local_wav import _decodes_directly, ensure_decodable, read_recording

    data = ALTERNATE.read_bytes()
    assert data[12:16] == b"fmt " and data[36:40] == b"data" and len(data) == 44 + int.from_bytes(data[40:44], "little")
    recording = read_recording(ALTERNATE)
    ensure_decodable(recording, ALTERNATE)
    assert _decodes_directly(recording, ALTERNATE)


def test_candidate_take_differs_from_section_2_in_hash_and_by_at_least_two_frames(timeline):
    section_2 = ASSETS / "section_2.wav"
    with wave.open(str(ALTERNATE), "rb") as first, wave.open(str(section_2), "rb") as second:
        difference = first.getnframes() / first.getframerate() - second.getnframes() / second.getframerate()
    assert sha256(ALTERNATE) != sha256(section_2)
    assert abs(difference) * timeline.FPS >= 2


def test_independent_a_and_b_timelines_move_only_the_decision(timeline):
    a = timeline.expected_timeline(lesson_a_durations())
    b = timeline.expected_timeline(
        [("question", LESSON_FRAMES["question"] / 22050), ("calculation", 132300 / 22050), lesson_a_durations()[2]]
    )
    shifts = {cue: value["start_shift_frames"] for cue, value in timeline.compare_timelines(a, b).items()}

    assert shifts["question"] == shifts["calculation"] == 0
    # A: 6 fade-in frames + int(6.9034 s * 15) = 103 wait frames + 5 fade-out frames = 114 frames from calculation to decision.
    # B: the 6.000 s take leaves exactly 5.6 s = 84 frames after the fade-in (a frame boundary; the model follows
    # Manim's floating-point clock and the real render confirms it): 6 + 84 + 5 = 95 frames. 95 - 114 = -19.
    assert shifts["decision"] == -19  # 299 -> 280: far outside the one-frame tolerance
    assert b["no_overlap"] and b["last_recording_completes"] and b["video_frames"] == 498
