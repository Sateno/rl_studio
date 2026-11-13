"""Manual Narration Bookmarks: named times in a local recording that a scene waits for.

Covers the bookmark rules (names, duplicates, times, files), LocalWavService with bookmarks
(checked at creation and at every render, not part of the cache key, recordings without
bookmarks unchanged), the scene timing (nearest frame, passed bookmarks, unknown names), the
expected-reward bookmarks lesson and its verifier, and, in the tests marked ``integration``,
real renders in which every animation must start within one frame of its bookmark.

Bookmark times in these tests are authored explicitly. Each test can be run on its own, for
example:

    UV_NO_DEV=1 uv run --frozen pytest -v "tests/test_local_wav_bookmarks.py::test_invalid_bookmark_time_is_rejected[nan]"
"""

import json
import math
import os
import re
import wave
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest
from manim import tempconfig

from manim_voiceover import VoiceoverScene
from manim_voiceover.services.cue_bindings import load_cue_manifest
from manim_voiceover.services.local_wav import LocalWavService
from manim_voiceover.services.local_wav_bookmarks import (
    BookmarkError,
    BookmarkFileError,
    BookmarkOutOfRangeError,
    Bookmarks,
    DuplicateBookmarkError,
    InvalidBookmarkNameError,
    InvalidBookmarkTimeError,
    UnknownBookmarkError,
    load_bookmarks,
    parse_bookmarks,
)
from manim_voiceover.tracker import AUDIO_OFFSET_RESOLUTION, VoiceoverTracker
from tests.test_cue_bindings_verifier import write_band_limited
from tests.test_expected_reward_lesson import ROOT, load_module
from tests.test_local_wav_service import TRANSCRIPT, file_identity, sha256, write_pcm16

ASSETS = ROOT / "examples" / "rl_lessons" / "assets"
LESSON_FILE = ROOT / "examples" / "rl_lessons" / "expected_reward_bookmarks.py"
VERIFIER_FILE = ROOT / "scripts" / "verify_local_wav_bookmarks_render.py"
needs_lesson = pytest.mark.skipif(
    not (LESSON_FILE.is_file() and VERIFIER_FILE.is_file()),
    reason="examples and scripts are not copied into mutmut's generated worktree",
)
FPS = 15


def write_bookmark_file(path, entries, version=1):
    path.write_text(
        json.dumps({"version": version, "bookmarks": [{"name": name, "seconds": seconds} for name, seconds in entries]}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def recording(tmp_path):
    """2.0 seconds of mono 16-bit PCM at 8000 Hz."""
    return write_pcm16(tmp_path / "narration.wav", frames=16000)


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


# ---------------------------------------------------------------------------
# Bookmark rules
# ---------------------------------------------------------------------------


def test_bookmarks_from_a_mapping_are_validated_and_read_only():
    bookmarks = parse_bookmarks({"question": 0, "result": 1.25})

    assert dict(bookmarks.times) == {"question": 0.0, "result": 1.25}
    assert all(type(seconds) is float for seconds in bookmarks.times.values())
    assert isinstance(bookmarks.times, MappingProxyType)


def test_bookmarks_from_name_and_seconds_pairs_keep_their_order():
    bookmarks = parse_bookmarks([("late", 1.5), ("early", 0.5)])

    assert list(bookmarks.times.items()) == [("late", 1.5), ("early", 0.5)]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"], ids=["plain", "utf8_bom"])
def test_bookmark_file_is_loaded(tmp_path, encoding):
    path = tmp_path / "bookmarks.json"
    path.write_text(json.dumps({"version": 1, "bookmarks": [{"name": "result", "seconds": 1.25}]}), encoding=encoding)

    assert dict(load_bookmarks(path).times) == {"result": 1.25}


@pytest.mark.parametrize("as_type", [str, Path], ids=["string_path", "path_object"])
def test_bookmark_file_path_may_be_a_string_or_a_path(recording, cache_dir, tmp_path, as_type):
    path = write_bookmark_file(tmp_path / "bookmarks.json", [("result", 1.25)])

    service = LocalWavService(recording, bookmarks=as_type(path), cache_dir=cache_dir)

    assert dict(service.bookmarks.times) == {"result": 1.25}


def test_longest_valid_name_has_64_characters():
    name = "a" + "b_9" * 21
    assert len(name) == 64

    assert list(parse_bookmarks({name: 1}).times) == [name]


INVALID_NAMES = {
    "empty": "",
    "leading_digit": "1st",
    "leading_underscore": "_result",
    "uppercase": "Result",
    "space": "the result",
    "hyphen": "the-result",
    "dot": "result.1",
    "non_ascii": "r\u00e9sultat",
    "too_long_65": "a" * 65,
    "integer": 7,
    "none": None,
}


@pytest.mark.parametrize("name", INVALID_NAMES.values(), ids=INVALID_NAMES.keys())
def test_invalid_bookmark_name_is_rejected(name):
    with pytest.raises(InvalidBookmarkNameError, match="lowercase ASCII letters, digits or underscores"):
        parse_bookmarks([(name, 1.0)])


def test_invalid_bookmark_name_in_a_file_is_rejected(tmp_path):
    path = write_bookmark_file(tmp_path / "bookmarks.json", [("ok", 0.5), ("Not OK", 1.0)])

    with pytest.raises(InvalidBookmarkNameError, match="entry 1"):
        load_bookmarks(path)


def test_duplicate_bookmark_name_in_pairs_is_rejected():
    with pytest.raises(DuplicateBookmarkError, match=r"'result' appears more than once \(bookmarks, entries 0 and 2\)"):
        parse_bookmarks([("result", 0.5), ("other", 0.7), ("result", 1.0)])


def test_duplicate_bookmark_name_in_a_file_is_rejected(tmp_path):
    path = write_bookmark_file(tmp_path / "bookmarks.json", [("result", 0.5), ("result", 0.5)])

    with pytest.raises(DuplicateBookmarkError, match="entries 0 and 1"):
        load_bookmarks(path)


REPEATED_KEYS = {
    "top_level": '{"version": 1, "version": 1, "bookmarks": [{"name": "a", "seconds": 1}]}',
    "entry": '{"version": 1, "bookmarks": [{"name": "a", "name": "b", "seconds": 1}]}',
}


@pytest.mark.parametrize("text", REPEATED_KEYS.values(), ids=REPEATED_KEYS.keys())
def test_repeated_json_key_is_rejected(tmp_path, text):
    path = tmp_path / "bookmarks.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(BookmarkFileError, match="repeats a JSON key"):
        load_bookmarks(path)


INVALID_TIMES = {
    "string_number": "1.5",
    "boolean": True,
    "null": None,
    "list": [1.5],
    "nan": math.nan,
    "infinity": math.inf,
    "negative_infinity": -math.inf,
}


@pytest.mark.parametrize("seconds", INVALID_TIMES.values(), ids=INVALID_TIMES.keys())
def test_invalid_bookmark_time_is_rejected(seconds):
    with pytest.raises(InvalidBookmarkTimeError, match="'result'"):
        parse_bookmarks({"result": seconds})


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_json_nan_and_infinity_literals_are_rejected(tmp_path, literal):
    path = tmp_path / "bookmarks.json"
    path.write_text('{"version": 1, "bookmarks": [{"name": "result", "seconds": %s}]}' % literal, encoding="utf-8")

    with pytest.raises(InvalidBookmarkTimeError, match="finite"):
        load_bookmarks(path)


def test_negative_bookmark_time_is_before_the_recording():
    with pytest.raises(BookmarkOutOfRangeError, match="before the start of the recording"):
        parse_bookmarks({"result": -0.001})


@pytest.mark.parametrize("value", [{}, []], ids=["empty_mapping", "empty_pairs"])
def test_no_bookmarks_is_rejected(value):
    with pytest.raises(BookmarkError, match="No bookmarks given"):
        parse_bookmarks(value)


MALFORMED_FILES = {
    "not_json": b"{version: 1}",
    "not_utf8": b'{"version": 1, "bookmarks": [{"name": "\xff", "seconds": 1}]}',
    "list_document": b"[]",
    "missing_version": b'{"bookmarks": []}',
    "extra_key": b'{"version": 1, "bookmarks": [], "recording": "a.wav"}',
    "wrong_version": b'{"version": 2, "bookmarks": [{"name": "a", "seconds": 1}]}',
    "version_as_string": b'{"version": "1", "bookmarks": [{"name": "a", "seconds": 1}]}',
    "bookmarks_not_a_list": b'{"version": 1, "bookmarks": {"a": 1}}',
    "entry_not_an_object": b'{"version": 1, "bookmarks": [["a", 1]]}',
    "entry_missing_seconds": b'{"version": 1, "bookmarks": [{"name": "a"}]}',
    "entry_extra_key": b'{"version": 1, "bookmarks": [{"name": "a", "seconds": 1, "note": "x"}]}',
}


@pytest.mark.parametrize("data", MALFORMED_FILES.values(), ids=MALFORMED_FILES.keys())
def test_malformed_bookmark_file_is_rejected(tmp_path, data):
    path = tmp_path / "bookmarks.json"
    path.write_bytes(data)

    with pytest.raises(BookmarkFileError, match=re.escape(str(path))):
        load_bookmarks(path)


def test_bookmark_file_with_an_empty_list_is_rejected(tmp_path):
    path = write_bookmark_file(tmp_path / "bookmarks.json", [])

    with pytest.raises(BookmarkError, match="No bookmarks given"):
        load_bookmarks(path)


def test_missing_bookmark_file_is_a_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_bookmarks(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "entry", [("result",), ("result", 1.0, "extra"), "result"], ids=["one_value", "three_values", "string"]
)
def test_malformed_pair_is_rejected(entry):
    with pytest.raises(BookmarkError, match=r"must be a \(name, seconds\) pair"):
        parse_bookmarks([entry])


@pytest.mark.parametrize("value", [42, b"bookmarks.json"], ids=["number", "bytes"])
def test_bookmarks_of_an_unsupported_type_are_a_type_error(recording, cache_dir, value):
    with pytest.raises(TypeError):
        LocalWavService(recording, bookmarks=value, cache_dir=cache_dir)


def test_bookmarks_constructed_directly_are_validated_again(recording, cache_dir):
    with pytest.raises(InvalidBookmarkNameError):
        LocalWavService(recording, bookmarks=Bookmarks(times={"Bad Name": 1.0}), cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# LocalWavService with bookmarks
# ---------------------------------------------------------------------------


class ClockScene:
    """A VoiceoverScene whose waits advance a clock instead of rendering (validation only, no audio)."""

    def __init__(self, service, start_time=0.0):
        self.scene = VoiceoverScene.__new__(VoiceoverScene)
        scene = self.scene
        scene.renderer = SimpleNamespace(time=start_time, skip_animations=False, _original_skipping_status=False)
        scene.sounds, scene.waits = [], []
        scene.add_sound = lambda path: scene.sounds.append(path)

        def wait(duration):
            scene.waits.append(duration)
            scene.renderer.time += duration

        scene.wait = wait
        scene.add_subcaption = lambda text, duration, offset: None
        scene.set_speech_service(service)


def voiceover_data(service, text=TRANSCRIPT):
    return service._wrap_generate_from_text(text)


def test_recording_without_bookmarks_behaves_exactly_as_before(recording, cache_dir):
    service = LocalWavService(recording, cache_dir=cache_dir)
    data = voiceover_data(service)
    clock = ClockScene(service)

    with clock.scene.voiceover(text=TRANSCRIPT) as tracker:
        with pytest.raises(Exception, match="Word boundaries are required"):  # the existing message, unchanged
            clock.scene.wait_until_bookmark("result")

    assert service.bookmarks is None
    assert "manual_bookmarks" not in data
    assert set(data) == {"input_text", "input_data", "original_audio", "final_audio"}
    assert data["input_data"] == {"input_text": TRANSCRIPT, "service": "local_wav", "config": {"sha256": sha256(recording)}}
    assert tracker.manual_bookmarks is None
    entries = json.loads((cache_dir / "cache.json").read_text(encoding="utf-8"))
    assert all("manual_bookmarks" not in entry for entry in entries)


@pytest.mark.parametrize("seconds", [0.0, 2.0], ids=["start", "end"])
def test_bookmark_at_the_start_or_the_end_of_the_recording_is_accepted(recording, cache_dir, seconds):
    service = LocalWavService(recording, bookmarks={"edge": seconds}, cache_dir=cache_dir)

    assert voiceover_data(service)["manual_bookmarks"] == {"edge": seconds}


def test_bookmark_after_the_end_of_the_recording_is_rejected_when_the_service_is_created(recording, cache_dir):
    with pytest.raises(BookmarkOutOfRangeError, match=r"'late' at 2.001 s is after the end of the recording \(2 s\)"):
        LocalWavService(recording, bookmarks={"late": 2.001}, cache_dir=cache_dir)


def test_bookmark_after_the_end_of_a_shorter_replacement_is_rejected_at_render(recording, cache_dir):
    service = LocalWavService(recording, bookmarks={"result": 1.5}, cache_dir=cache_dir)
    write_pcm16(recording, frames=8000)  # the recording is replaced by a 1.0 s take

    with pytest.raises(BookmarkOutOfRangeError, match="after the end of the recording"):
        voiceover_data(service)


def test_bookmarks_are_not_part_of_the_cache_key(recording, cache_dir):
    plain = voiceover_data(LocalWavService(recording, cache_dir=cache_dir))
    copy = cache_dir / plain["original_audio"]
    before = file_identity(copy)

    marked = voiceover_data(LocalWavService(recording, bookmarks={"result": 1.0}, cache_dir=cache_dir))

    assert marked["input_data"] == plain["input_data"]
    assert marked["original_audio"] == plain["original_audio"]
    assert file_identity(copy) == before  # the cached copy was reused, not written again
    assert sorted(path.name for path in cache_dir.iterdir()) == sorted(["cache.json", copy.name])


def test_changed_bookmarks_are_used_on_the_next_render_with_the_same_cached_copy(recording, cache_dir):
    first = voiceover_data(LocalWavService(recording, bookmarks={"result": 1.0}, cache_dir=cache_dir))
    copy = cache_dir / first["original_audio"]
    before = file_identity(copy)

    second = voiceover_data(LocalWavService(recording, bookmarks={"result": 1.5, "extra": 0.25}, cache_dir=cache_dir))

    assert second["manual_bookmarks"] == {"result": 1.5, "extra": 0.25}
    assert second["original_audio"] == first["original_audio"]
    assert file_identity(copy) == before


def test_render_without_bookmarks_after_one_with_bookmarks_has_none(recording, cache_dir):
    voiceover_data(LocalWavService(recording, bookmarks={"result": 1.0}, cache_dir=cache_dir))

    plain = voiceover_data(LocalWavService(recording, cache_dir=cache_dir))

    assert "manual_bookmarks" not in plain


def test_source_recording_and_bookmark_file_are_only_read(recording, cache_dir, tmp_path):
    path = write_bookmark_file(tmp_path / "bookmarks.json", [("result", 1.0)])
    identities = {file: (sha256(file), file_identity(file)) for file in (recording, path)}

    service = LocalWavService(recording, bookmarks=path, cache_dir=cache_dir)
    voiceover_data(service)
    voiceover_data(service)

    assert {file: (sha256(file), file_identity(file)) for file in (recording, path)} == identities


def test_bookmarks_cannot_be_combined_with_transcription(recording, cache_dir):
    with pytest.raises(BookmarkError, match="cannot be combined with transcription_model"):
        LocalWavService(recording, bookmarks={"result": 1.0}, transcription_model="base", cache_dir=cache_dir)


def test_inline_bookmark_tags_cannot_be_combined_with_manual_bookmarks(recording, cache_dir):
    service = LocalWavService(recording, bookmarks={"result": 1.0}, cache_dir=cache_dir)

    with pytest.raises(BookmarkError, match="use one or the other"):
        voiceover_data(service, text="One quarter <bookmark mark='result'/> times zero.")


def test_cue_binding_service_passes_bookmarks_on(tmp_path, cache_dir):
    write_pcm16(tmp_path / "calculation.wav", frames=16000)
    manifest = tmp_path / "cues.json"
    manifest.write_text(
        json.dumps({"version": 1, "cues": [{"id": "calculation", "wav": "calculation.wav"}]}), encoding="utf-8"
    )
    (cue,) = load_cue_manifest(manifest).resolve(["calculation"])

    service = cue.service(bookmarks={"result": 1.25}, cache_dir=cache_dir)

    assert voiceover_data(service)["manual_bookmarks"] == {"result": 1.25}
    assert cue.service(cache_dir=cache_dir).bookmarks is None  # without bookmarks, as before


# ---------------------------------------------------------------------------
# Scene timing
# ---------------------------------------------------------------------------


def test_bookmark_times_start_at_the_voiceover(recording, cache_dir):
    clock = ClockScene(LocalWavService(recording, bookmarks={"a": 0.5, "b": 1.5}, cache_dir=cache_dir), start_time=3.0)

    with clock.scene.voiceover(text=TRANSCRIPT) as tracker:
        assert tracker.bookmark_times == {"a": 3.5, "b": 4.5}
        assert tracker.time_until_bookmark("b") == pytest.approx(1.5)


def test_wait_until_bookmark_waits_to_the_nearest_frame(recording, cache_dir):
    clock = ClockScene(LocalWavService(recording, bookmarks={"a": 0.5, "b": 1.5}, cache_dir=cache_dir))

    with tempconfig({"frame_rate": FPS}), clock.scene.voiceover(text=TRANSCRIPT):
        clock.scene.wait_until_bookmark("a")
        clock.scene.wait_until_bookmark("b")

    # Half a frame is added so that Manim's rounding down of the wait gives the nearest frame.
    assert clock.scene.waits[:2] == pytest.approx([0.5 + 0.5 / FPS, 1.0])


# Scene time relative to the bookmark when the scene waits for it.
DUE_OR_PASSED = {"less_than_half_a_frame_ahead": -0.4 / FPS, "exactly_due": 0.0, "passed": 0.25}


@pytest.mark.parametrize("offset", DUE_OR_PASSED.values(), ids=DUE_OR_PASSED.keys())
def test_bookmark_that_is_due_or_has_passed_does_not_wait_as_before(recording, cache_dir, offset):
    clock = ClockScene(LocalWavService(recording, bookmarks={"a": 0.5}, cache_dir=cache_dir))

    with tempconfig({"frame_rate": FPS}), clock.scene.voiceover(text=TRANSCRIPT) as tracker:
        clock.scene.renderer.time = 0.5 + offset
        waits_before = len(clock.scene.waits)
        clock.scene.wait_until_bookmark("a")  # no error for a passed bookmark: the existing behaviour
        waited = clock.scene.waits[waits_before:]
        remaining = tracker.time_until_bookmark("a")

    assert waited == []
    assert remaining == pytest.approx(max(-offset, 0.0))


def test_unknown_bookmark_is_rejected_with_the_available_names(recording, cache_dir):
    clock = ClockScene(LocalWavService(recording, bookmarks={"result": 1.0, "question": 0.5}, cache_dir=cache_dir))

    with clock.scene.voiceover(text=TRANSCRIPT):
        with pytest.raises(
            UnknownBookmarkError, match=r"Unknown bookmark 'answer'; the recording's bookmarks are: question, result"
        ):
            clock.scene.wait_until_bookmark("answer")


def test_unknown_bookmark_error_is_a_bookmark_error_and_a_value_error():
    assert issubclass(UnknownBookmarkError, BookmarkError)
    assert issubclass(BookmarkError, ValueError)


def test_wait_until_bookmark_outside_a_voiceover_is_rejected_as_before(recording, cache_dir):
    clock = ClockScene(LocalWavService(recording, bookmarks={"result": 1.0}, cache_dir=cache_dir))

    with pytest.raises(RuntimeError, match="No active voiceover tracker"):
        clock.scene.wait_until_bookmark("result")


def test_text_bookmarks_of_transcribed_voiceovers_keep_rounding_down(tmp_path):
    """Word-boundary (SSML) bookmarks are unchanged: no half frame is added."""
    scene = VoiceoverScene.__new__(VoiceoverScene)
    scene.renderer = SimpleNamespace(time=0.0)
    scene.waits = []
    scene.wait = scene.waits.append
    write_pcm16(tmp_path / "voice.wav", frames=16000)
    data = {
        "input_text": "First <bookmark mark='second'/>second",
        "final_audio": "voice.wav",
        "word_boundaries": [
            {"audio_offset": 0, "text_offset": 0, "word_length": 5, "text": "First", "boundary_type": "Word"},
            {"audio_offset": AUDIO_OFFSET_RESOLUTION, "text_offset": 6, "word_length": 6, "text": "second"},
        ],
    }
    scene.current_tracker = VoiceoverTracker(scene, data, tmp_path)

    with tempconfig({"frame_rate": FPS}):
        scene.wait_until_bookmark("second")

    assert scene.current_tracker.manual_bookmarks is None
    assert scene.waits == [pytest.approx(scene.current_tracker.bookmark_times["second"])]


# ---------------------------------------------------------------------------
# The expected-reward bookmarks lesson and its verifier
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lesson():
    return load_module("expected_reward_bookmarks_lesson", LESSON_FILE)


@pytest.fixture(scope="module")
def verifier():
    return load_module("local_wav_bookmarks_verifier", VERIFIER_FILE)


LAYOUT = ("BOOKMARK_ORDER", "REVEAL_SECONDS", "FINAL_HOLD_SECONDS", "MARKER_SIDE", "MARKER_X", "MARKER_SPACING", "MARKER_Y")
LESSON_BOOKMARKS = {"quarter_times_zero": 0.12, "three_quarters_times_four": 2.17, "expected_reward_three": 4.57}


@needs_lesson
def test_lesson_layout_matches_the_verifier_declaration(lesson, verifier):
    assert {name: getattr(lesson, name) for name in LAYOUT} == {name: getattr(verifier, name) for name in LAYOUT}


@needs_lesson
def test_lesson_bookmark_file_has_the_authored_times_and_fits_its_recording(lesson, cache_dir):
    bookmarks = load_bookmarks(lesson.DEFAULT_BOOKMARKS)

    assert dict(bookmarks.times) == LESSON_BOOKMARKS
    assert tuple(bookmarks.times) == lesson.BOOKMARK_ORDER
    service = LocalWavService(lesson.DEFAULT_RECORDING, bookmarks=bookmarks, cache_dir=cache_dir)  # within the recording
    assert dict(service.bookmarks.times) == LESSON_BOOKMARKS


@needs_lesson
def test_lesson_transcript_is_section_2_verbatim(lesson):
    sections = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))["sections"]
    assert lesson.TRANSCRIPT == sections[2]
    assert lesson.DEFAULT_RECORDING == ASSETS / "section_2.wav"


def voiced_phrase_onsets(path, level=300, pause_seconds=0.12):
    """Starts of the voiced stretches of a recording that follow a pause (20 ms RMS envelope)."""
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float)
    window = int(0.02 * rate)
    envelope = np.sqrt(np.convolve(samples**2, np.ones(window) / window, "same"))
    voiced = np.flatnonzero(envelope > level)
    gaps = np.flatnonzero(np.diff(voiced) > pause_seconds * rate)
    return [voiced[0] / rate] + [voiced[gap + 1] / rate for gap in gaps]


@needs_lesson
def test_lesson_bookmarks_are_at_the_phrase_onsets_of_the_recording(lesson):
    onsets = voiced_phrase_onsets(lesson.DEFAULT_RECORDING)

    assert len(onsets) == len(LESSON_BOOKMARKS)  # three phrases, separated by the commas' pauses
    assert list(LESSON_BOOKMARKS.values()) == pytest.approx(onsets, abs=0.01)


@needs_lesson
def test_lesson_bookmarks_leave_room_for_each_reveal(lesson):
    times = list(LESSON_BOOKMARKS.values())
    assert all(later - earlier > lesson.REVEAL_SECONDS + 1 / FPS for earlier, later in zip(times, times[1:]))


@needs_lesson
def test_expected_lesson_timeline(verifier):
    timeline = verifier.expected_timeline(161039 / 22050, LESSON_BOOKMARKS)

    assert [entry["expected_frame"] for entry in timeline["bookmarks"]] == [2, 33, 69]
    assert timeline["video_frames"] == 112


@needs_lesson
@pytest.mark.parametrize("seconds", [0.0, 0.03, 0.04, 0.12, 1.0, 2.17])
def test_expected_trigger_frame_is_the_nearest_frame_to_a_first_bookmark(verifier, seconds):
    bookmarks = {"quarter_times_zero": seconds, "three_quarters_times_four": 3.0, "expected_reward_three": 4.0}

    frame = verifier.expected_timeline(5.0, bookmarks)["bookmarks"][0]["expected_frame"]

    assert frame == (math.floor(seconds * FPS + 0.5) if seconds * FPS + 0.5 > 1 else 0)
    assert abs(frame / FPS - seconds) <= 0.5 / FPS or frame == 0


@needs_lesson
@pytest.mark.parametrize("size", [(854, 480), (426, 240)], ids=["480p", "240p"])
def test_marker_boxes_lie_inside_their_markers(verifier, size):
    width, height = size
    scale = height / verifier.FRAME_HEIGHT
    for index in range(3):
        rows, columns = verifier.marker_box(index, width, height)
        centre_x = width / 2 + (verifier.MARKER_X + index * verifier.MARKER_SPACING) * scale
        centre_y = height / 2 - verifier.MARKER_Y * scale
        half_side = verifier.MARKER_SIDE * scale / 2
        assert centre_x - half_side <= columns.start < columns.stop <= centre_x + half_side
        assert centre_y - half_side <= rows.start < rows.stop <= centre_y + half_side


@needs_lesson
def test_first_shown_finds_the_first_frame_and_whether_it_stays(verifier):
    assert verifier.first_shown(np.array([20.0, 20.0, 250.0, 250.0])) == (2, True)
    assert verifier.first_shown(np.array([20.0, 250.0, 20.0, 250.0])) == (1, False)
    assert verifier.first_shown(np.array([20.0, 20.0])) == (None, False)


# ---------------------------------------------------------------------------
# Real renders: every animation within one frame of its bookmark
# ---------------------------------------------------------------------------

SYNTHETIC_BOOKMARKS = {"quarter_times_zero": 0.37, "three_quarters_times_four": 1.21, "expected_reward_three": 2.05}


def render_lesson(lesson, verifier, base, recording, bookmark_file, output):
    """`dev.sh bookmarks render`'s steps in a temporary media folder: expectations, render, records."""
    media = base / output
    media.mkdir(parents=True)
    (media / "expected.json").write_text(json.dumps(verifier.expect(recording, bookmark_file)), encoding="utf-8")
    sources = "".join(f"{sha256(path)}  {path.name}\n" for path in (recording, bookmark_file))
    (media / "sources-before.sha256").write_text(sources, encoding="utf-8")
    cache = media / "voiceovers"
    (media / "cache-before.json").write_text(json.dumps(verifier.snapshot_cache(cache)), encoding="utf-8")
    settings = {
        "media_dir": str(media),
        "pixel_width": 426,
        "pixel_height": 240,
        "frame_rate": FPS,
        "disable_caching": True,
        "output_file": output,
        "verbosity": "ERROR",
        "progress_bar": "none",
    }
    previous = {key: os.environ.get(key) for key in ("LOCAL_WAV_BOOKMARKS_AUDIO", "LOCAL_WAV_BOOKMARKS_FILE")}
    os.environ.update(LOCAL_WAV_BOOKMARKS_AUDIO=str(recording), LOCAL_WAV_BOOKMARKS_FILE=str(bookmark_file))
    try:
        with tempconfig(settings):
            scene = lesson.ExpectedRewardBookmarks()
            scene.render()
            video = Path(scene.renderer.file_writer.movie_file_path)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    (media / "sources-after.sha256").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in (recording, bookmark_file)), encoding="utf-8"
    )
    (media / "cache-after.json").write_text(json.dumps(verifier.snapshot_cache(cache)), encoding="utf-8")
    return media, video


@pytest.fixture(scope="module")
def synthetic_render(tmp_path_factory, lesson, verifier):
    base = tmp_path_factory.mktemp("bookmarks")
    recording = write_band_limited(base / "narration.wav", 2.8, seed=11)
    bookmark_file = write_bookmark_file(base / "bookmarks.json", SYNTHETIC_BOOKMARKS.items())
    media, video = render_lesson(lesson, verifier, base, recording, bookmark_file, "synthetic")
    return base, media, video, recording, bookmark_file


@needs_lesson
@pytest.mark.integration
def test_real_render_starts_each_animation_within_one_frame_of_its_bookmark(verifier, synthetic_render):
    _, media, video, _, _ = synthetic_render

    result = verifier.verify(media, video=video)

    assert result["passed"], result["failures"]
    assert [entry["marker_frame"] for entry in result["bookmarks"]] == [
        entry["expected_frame"] for entry in result["bookmarks"]
    ]
    assert all(abs(entry["error_frames"]) <= 0.5 for entry in result["bookmarks"])  # the nearest frame
    assert all(abs(entry["error_frames_in_mp4"]) <= 1 for entry in result["bookmarks"])


@needs_lesson
@pytest.mark.integration
def test_verifier_fails_a_render_checked_against_bookmarks_two_frames_later(verifier, synthetic_render, tmp_path):
    _, media, video, recording, _ = synthetic_render
    shifted = write_bookmark_file(tmp_path / "shifted.json", [(name, t + 2 / FPS) for name, t in SYNTHETIC_BOOKMARKS.items()])
    expected = json.loads((media / "expected.json").read_text(encoding="utf-8"))
    expected["bookmarks"] = verifier.expect(recording, shifted)["bookmarks"]
    other = tmp_path / "expected-shifted.json"
    other.write_text(json.dumps(expected), encoding="utf-8")

    result = verifier.verify(media, expected_path=other, video=video)

    failed = set(result["failures"])
    for name in SYNTHETIC_BOOKMARKS:
        assert f"{name}: animation starts within one frame of the bookmark in the soundtrack" in failed
        assert f"{name}: animation starts at the expected frame" in failed
    assert not {name for name in failed if not name.startswith(tuple(SYNTHETIC_BOOKMARKS))}  # nothing else fails


@pytest.mark.integration
def test_bookmarks_do_not_change_the_rendered_audio_or_cached_copy(tmp_path):
    """The same voiceover with and without bookmarks (and no waits) renders identical audio and length."""
    recording = write_band_limited(tmp_path / "narration.wav", 1.2, seed=3)
    results = {}
    for label, bookmarks in (("plain", None), ("bookmarked", {"middle": 0.6})):

        class Plain(VoiceoverScene):
            def construct(self):
                self.set_speech_service(LocalWavService(recording, bookmarks=bookmarks, cache_dir=tmp_path / "cache"))
                with self.voiceover(text="Synthetic narration."):
                    pass

        settings = {
            "media_dir": str(tmp_path / label),
            "pixel_width": 96,
            "pixel_height": 54,
            "frame_rate": FPS,
            "disable_caching": True,
            "output_file": label,
            "verbosity": "ERROR",
            "progress_bar": "none",
        }
        with tempconfig(settings):
            scene = Plain()
            scene.render()
            movie = Path(scene.renderer.file_writer.movie_file_path)
        with wave.open(str(movie.with_suffix(".wav")), "rb") as audio:
            results[label] = (audio.getnframes(), audio.readframes(audio.getnframes()))

    assert results["plain"] == results["bookmarked"]
    assert len(list((tmp_path / "cache").glob("*.wav"))) == 1  # one cached copy, shared
