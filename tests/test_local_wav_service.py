"""Tests for LocalWavService.

Most tests drive the service through ``mock_scene_voiceover``: a VoiceoverScene whose
add_sound, wait and subcaption calls are recorded instead of executed. They validate
the service (input checks, durations, cache, source preservation), but they do not
decode, mix or encode audio, so they are NOT proof that a recording renders.
Rendering is proven only by the tests marked ``integration``, which run real Manim
renders and inspect the resulting soundtrack and MP4.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
from manim import tempconfig
from pydub import AudioSegment

import manim_voiceover.services.local_wav as local_wav
from manim_voiceover import VoiceoverScene
from manim_voiceover.defaults import DEFAULT_VOICEOVER_CACHE_JSON_FILENAME
from manim_voiceover.services.local_wav import (
    AudioDecoderDependencyError,
    InvalidRecordingError,
    LocalWavService,
    read_recording,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_INFO = json.loads((FIXTURES / "info.json").read_text(encoding="utf-8"))
TRANSCRIPT = FIXTURE_INFO["transcript"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_pcm16(path, frames, rate=8000, channels=1, value=1000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<h", value) * frames * channels)
    return path


def riff(format_tag, channels, rate, bits, data, data_size=None, extra_fmt=b""):
    block_align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", format_tag, channels, rate, rate * block_align, block_align, bits) + extra_fmt
    size = len(data) if data_size is None else data_size
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_identity(path):
    """Everything that changes if a file is rewritten, replaced, renamed over or re-linked."""
    info = os.lstat(path)
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink)


def audio_files(cache_dir):
    return {path.name: file_identity(path) for path in Path(cache_dir).iterdir() if path.name != "cache.json"}


def wav_duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def mock_scene_voiceover(audio_path, cache_dir, text=TRANSCRIPT):
    """One voiceover block with a fresh service and a MOCK scene (validation only).

    Mirrors how a render uses the service, but add_sound only records the path:
    no audio is decoded, mixed or encoded. See the integration tests for real renders.
    """
    scene = VoiceoverScene.__new__(VoiceoverScene)
    scene.renderer = SimpleNamespace(time=0.0, skip_animations=False, _original_skipping_status=False)
    scene.sounds, scene.waits, scene.captions = [], [], []
    scene.add_sound = lambda path: scene.sounds.append(path)
    scene.wait = lambda duration: scene.waits.append(duration)
    scene.add_subcaption = lambda text, duration, offset: scene.captions.append((text, duration, offset))
    scene.set_speech_service(LocalWavService(audio_path, cache_dir=cache_dir))
    with scene.voiceover(text=text) as tracker:
        pass
    return scene, tracker


def fixture_copy(take, destination):
    shutil.copyfile(FIXTURES / f"take_{take}.wav", destination)
    return destination


@pytest.fixture
def source(tmp_path):
    return write_pcm16(tmp_path / "input.wav", frames=12000)


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------


def test_voiceover_scene_plays_cached_copy_of_recording(source, cache_dir):
    scene, tracker = mock_scene_voiceover(source, cache_dir)

    played = Path(scene.sounds[0])
    assert len(scene.sounds) == 1
    assert played.parent == cache_dir and played.suffix == ".wav"
    assert played != source and played.read_bytes() == source.read_bytes()
    assert " ".join(caption[0] for caption in scene.captions) == TRANSCRIPT
    last_text, last_duration, last_offset = scene.captions[-1]
    assert last_offset + last_duration <= tracker.duration
    assert scene.waits == [pytest.approx(tracker.duration)]

    entry = json.loads((cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text())[-1]
    assert entry["input_text"] == TRANSCRIPT
    assert entry["input_data"] == {
        "input_text": TRANSCRIPT,
        "service": "local_wav",
        "config": {"sha256": sha256(source)},
    }
    assert entry["original_audio"] == entry["final_audio"] == played.name


def test_bookmarks_are_excluded_from_the_cache_key(source, cache_dir):
    service = LocalWavService(source, cache_dir=cache_dir)

    plain = service._wrap_generate_from_text("Hello world")
    marked = service._wrap_generate_from_text("Hello <bookmark mark='A'/>world")

    assert marked["input_data"] == plain["input_data"]
    assert marked["final_audio"] == plain["final_audio"]


@pytest.mark.integration
def test_real_manim_render_contains_the_recording(tmp_path):
    recording = write_pcm16(tmp_path / "narration.wav", frames=6000, rate=8000)  # 0.75 s
    recording_hash = sha256(recording)

    class LocalWavScene(VoiceoverScene):
        def construct(self):
            self.set_speech_service(LocalWavService(recording))
            with self.voiceover(text="Short local narration") as tracker:
                self.voiceover_duration = tracker.duration
            self.wait(0.25)

    settings = {
        "media_dir": str(tmp_path / "media"),
        "pixel_height": 36,
        "pixel_width": 64,
        "frame_rate": 10,
        "disable_caching": True,
        "output_file": "local_wav",
        "verbosity": "ERROR",
        "progress_bar": "none",
    }
    with tempconfig(settings):
        scene = LocalWavScene()
        scene.render()
        movie = Path(scene.renderer.file_writer.movie_file_path)

    assert scene.voiceover_duration == pytest.approx(0.75)
    assert list((tmp_path / "media" / "voiceovers").glob("*.wav"))
    with av.open(str(movie)) as container:
        assert container.streams.video and container.streams.audio
        video = container.streams.video[0]
        assert float(video.duration * video.time_base) >= 0.75
        audio_rate = container.streams.audio[0].rate
        audio_samples = sum(frame.samples for frame in container.decode(audio=0))
    assert audio_samples / audio_rate >= 0.75
    assert sha256(recording) == recording_hash


def test_speed_adjustment_is_rejected(source, cache_dir, monkeypatch):
    monkeypatch.setattr("manim_voiceover.services.base.adjust_speed", lambda *args: pytest.fail("speed must not be adjusted"))

    with pytest.raises(ValueError, match="normal speed"):
        LocalWavService(source, cache_dir=cache_dir, global_speed=1.5)


def test_output_path_override_is_honoured_and_must_be_wav(source, cache_dir):
    service = LocalWavService(source, cache_dir=cache_dir)

    result = service._wrap_generate_from_text("Hello", path="narration.wav")
    assert result["final_audio"] == "narration.wav"
    assert (cache_dir / "narration.wav").read_bytes() == source.read_bytes()

    with pytest.raises(ValueError, match="must end with .wav"):
        service._wrap_generate_from_text("Other text", path="narration.mp3")


# ---------------------------------------------------------------------------
# Invalid inputs
# ---------------------------------------------------------------------------

PCM_SUBFORMAT = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
FLOAT_SUBFORMAT = b"\x03\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
VALID = riff(1, 1, 8000, 16, struct.pack("<h", 1000) * 800)

INVALID_RECORDINGS = {
    "empty file": (b"", InvalidRecordingError, "empty"),
    "header without frames": (riff(1, 1, 8000, 16, b""), InvalidRecordingError, "no audio frames"),
    "truncated sample data": (VALID[:-400], InvalidRecordingError, "only 600 are present"),
    "truncated header": (VALID[:30], InvalidRecordingError, "corrupt"),
    "not a RIFF file": (b"this is plain text, not audio" * 8, InvalidRecordingError, "corrupt"),
    "RIFF without WAVE chunks": (b"RIFF\x04\x00\x00\x00WAVE", InvalidRecordingError, "corrupt"),
    "inconsistent chunk size": (VALID[:16] + b"\xff\xff\xff\x7f" + VALID[20:], InvalidRecordingError, "corrupt"),
    "zero sample rate": (riff(1, 1, 0, 16, b"\x00\x00" * 10), InvalidRecordingError, "sample rate"),
    "8-bit PCM": (riff(1, 1, 8000, 8, b"\x80" * 100), InvalidRecordingError, "16-bit"),
    "24-bit PCM": (riff(1, 1, 8000, 24, b"\x00" * 300), InvalidRecordingError, "16-bit"),
    "32-bit float": (riff(3, 1, 8000, 32, b"\x00" * 400), InvalidRecordingError, "not an uncompressed PCM"),
    "mu-law compressed": (riff(7, 1, 8000, 8, b"\x00" * 100), InvalidRecordingError, "not an uncompressed PCM"),
    "extensible float": (
        riff(0xFFFE, 1, 8000, 32, b"\x00" * 400, extra_fmt=struct.pack("<HHI", 22, 32, 4) + FLOAT_SUBFORMAT),
        InvalidRecordingError,
        "not an uncompressed PCM",
    ),
    "MP3 data named .wav": (b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64, InvalidRecordingError, "corrupt"),
}


def make_invalid(tmp_path, case):
    if case == "missing file":
        return tmp_path / "missing.wav"
    if case == "directory":
        directory = tmp_path / "folder.wav"
        directory.mkdir()
        return directory
    path = tmp_path / "input.wav"
    path.write_bytes(INVALID_RECORDINGS[case][0])
    return path


ALL_INVALID = ["missing file", "directory", *INVALID_RECORDINGS]


def expected_error(case):
    if case in ("missing file", "directory"):
        return FileNotFoundError, "not found"
    return INVALID_RECORDINGS[case][1:]


@pytest.mark.parametrize("case", ALL_INVALID)
def test_invalid_recording_is_rejected_when_service_is_created(tmp_path, cache_dir, case):
    path = make_invalid(tmp_path, case)
    error, message = expected_error(case)
    before = path.read_bytes() if path.is_file() else None

    with pytest.raises(error, match=message):
        LocalWavService(path, cache_dir=cache_dir)

    assert not cache_dir.exists()
    if before is not None:
        assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing file", "empty file", "truncated sample data", "not a RIFF file", "24-bit PCM"])
def test_invalid_replacement_is_rejected_at_render_and_keeps_existing_cache(tmp_path, cache_dir, case):
    source = write_pcm16(tmp_path / "input.wav", frames=8000)
    service = LocalWavService(source, cache_dir=cache_dir)
    service._wrap_generate_from_text(TRANSCRIPT)
    cache_json = (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text()
    copies = audio_files(cache_dir)

    source.unlink()
    if case != "missing file":
        source.write_bytes(INVALID_RECORDINGS[case][0])
    error, message = expected_error(case)

    with pytest.raises(error, match=message):
        service._wrap_generate_from_text(TRANSCRIPT)

    assert (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text() == cache_json
    assert audio_files(cache_dir) == copies


def test_failed_copy_leaves_no_partial_file(source, cache_dir, monkeypatch):
    service = LocalWavService(source, cache_dir=cache_dir)

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(local_wav.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        service._wrap_generate_from_text(TRANSCRIPT)

    assert audio_files(cache_dir) == {}


# ---------------------------------------------------------------------------
# Source preservation
# ---------------------------------------------------------------------------


def test_source_recording_is_only_read(tmp_path):
    source_dir = tmp_path / "recordings"
    source_dir.mkdir()
    source = fixture_copy("a", source_dir / "input.wav")
    before_bytes = source.read_bytes()
    # Read-only file in a read-only directory: any write, rename or temp file next to it would fail.
    source.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    source_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    before_identity = file_identity(source)
    try:
        scene, _ = mock_scene_voiceover(source, tmp_path / "cache")
        mock_scene_voiceover(source, tmp_path / "cache")
        after_identity = file_identity(source)
    finally:
        source_dir.chmod(stat.S_IRWXU)
        source.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert source.read_bytes() == before_bytes
    assert after_identity == before_identity  # inode, size, mtime, ctime, mode and link count
    assert after_identity[5] == 1  # no hard links were created to the source
    assert sorted(path.name for path in source_dir.iterdir()) == ["input.wav"]
    cached = Path(scene.sounds[0])
    assert not cached.is_symlink()
    assert os.stat(cached).st_ino != os.stat(source).st_ino


def test_cached_copy_is_independent_of_source(source, cache_dir):
    scene, _ = mock_scene_voiceover(source, cache_dir)
    cached = Path(scene.sounds[0])
    original = source.read_bytes()

    cached.write_bytes(b"changed cache")
    assert source.read_bytes() == original

    cached.write_bytes(original)
    with open(source, "r+b") as handle:  # overwrite the source in place, like `cp new.wav input.wav`
        handle.seek(-2, os.SEEK_END)
        handle.write(b"\x00\x00")
    assert cached.read_bytes() == original


def test_refuses_to_write_cache_copy_over_the_source(source):
    before = file_identity(source), source.read_bytes()
    service = LocalWavService(source, cache_dir=source.parent)

    with pytest.raises(ValueError, match="overwrite the source"):
        service._wrap_generate_from_text(TRANSCRIPT, path=source.name)

    assert (file_identity(source), source.read_bytes()) == before


def test_supplied_fixtures_are_preserved(tmp_path):
    for take in ("a", "b"):
        mock_scene_voiceover(FIXTURES / f"take_{take}.wav", tmp_path / "cache")

    for filename, expected in FIXTURE_INFO["sha256"].items():
        assert sha256(FIXTURES / filename) == expected


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("take", ["a", "b"])
def test_voiceover_duration_is_the_recording_duration(tmp_path, take):
    source = fixture_copy(take, tmp_path / "input.wav")
    expected = wav_duration(source)

    scene, tracker = mock_scene_voiceover(source, tmp_path / "cache")

    assert tracker.duration == pytest.approx(expected, abs=1e-9)
    assert tracker.end_t - tracker.start_t == pytest.approx(expected, abs=1e-9)
    assert scene.waits == [pytest.approx(expected, abs=1e-9)]
    assert read_recording(source).duration == pytest.approx(expected, abs=1e-9)


def test_fixture_durations_differ():
    assert wav_duration(FIXTURES / "take_a.wav") == pytest.approx(126656 / 22050)
    assert wav_duration(FIXTURES / "take_b.wav") == pytest.approx(104762 / 22050)


def test_duration_for_stereo_and_extensible_pcm16(tmp_path, cache_dir):
    stereo = write_pcm16(tmp_path / "stereo.wav", frames=66150, rate=44100, channels=2)
    extensible = tmp_path / "extensible.wav"
    extensible.write_bytes(
        riff(0xFFFE, 1, 16000, 16, b"\x01\x00" * 4000, extra_fmt=struct.pack("<HHI", 22, 16, 4) + PCM_SUBFORMAT)
    )

    _, stereo_tracker = mock_scene_voiceover(stereo, cache_dir)
    _, extensible_tracker = mock_scene_voiceover(extensible, cache_dir)

    assert stereo_tracker.duration == pytest.approx(1.5)
    assert extensible_tracker.duration == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------------


def test_replacing_audio_at_same_path_updates_next_render(tmp_path, cache_dir):
    source = fixture_copy("a", tmp_path / "input.wav")
    first_scene, first = mock_scene_voiceover(source, cache_dir)
    first_copy = Path(first_scene.sounds[0])
    first_identity = file_identity(first_copy)

    fixture_copy("b", source)  # same pathname, same transcript
    second_scene, second = mock_scene_voiceover(source, cache_dir)
    second_copy = Path(second_scene.sounds[0])

    assert second_copy != first_copy
    assert second_copy.read_bytes() == (FIXTURES / "take_b.wav").read_bytes()
    assert second.duration == pytest.approx(wav_duration(FIXTURES / "take_b.wav"))
    assert first.duration == pytest.approx(wav_duration(FIXTURES / "take_a.wav"))
    assert second_scene.waits == [pytest.approx(second.duration)]
    assert file_identity(first_copy) == first_identity  # the earlier take's copy is untouched


def test_replacement_is_detected_by_content_not_timestamp_or_size(source, cache_dir):
    service = LocalWavService(source, cache_dir=cache_dir)
    first = service._wrap_generate_from_text(TRANSCRIPT)
    original_stat = os.stat(source)

    write_pcm16(source, frames=12000, value=-2000)  # same size, different samples
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert os.stat(source).st_size == original_stat.st_size
    assert os.stat(source).st_mtime_ns == original_stat.st_mtime_ns

    second = service._wrap_generate_from_text(TRANSCRIPT)

    assert second["final_audio"] != first["final_audio"]
    assert (cache_dir / second["final_audio"]).read_bytes() == source.read_bytes()


# ---------------------------------------------------------------------------
# Cache reuse
# ---------------------------------------------------------------------------


def forbid_copies(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("unchanged audio must not be copied again")

    monkeypatch.setattr(local_wav, "_write_copy", fail)
    monkeypatch.setattr(local_wav.os, "replace", fail)
    monkeypatch.setattr(shutil, "copyfile", fail)


def test_unchanged_audio_reuses_cached_copy_without_copying(source, cache_dir, monkeypatch):
    first_scene, _ = mock_scene_voiceover(source, cache_dir)
    copies = audio_files(cache_dir)
    assert len(copies) == 1

    forbid_copies(monkeypatch)
    second_scene, second = mock_scene_voiceover(source, cache_dir)  # new service instance, like a new render
    service = second_scene.speech_service
    third = service._wrap_generate_from_text(TRANSCRIPT)  # same instance, again

    assert second_scene.sounds == first_scene.sounds
    assert third["final_audio"] == Path(first_scene.sounds[0]).name
    assert second.duration == pytest.approx(wav_duration(source))
    assert audio_files(cache_dir) == copies  # same inode, mtime and ctime: nothing rewritten


def test_switching_back_to_earlier_take_reuses_its_original_copy(tmp_path, cache_dir, monkeypatch):
    source = fixture_copy("a", tmp_path / "input.wav")
    first_scene, _ = mock_scene_voiceover(source, cache_dir)
    fixture_copy("b", source)
    mock_scene_voiceover(source, cache_dir)
    copies = audio_files(cache_dir)
    assert len(copies) == 2

    fixture_copy("a", source)
    forbid_copies(monkeypatch)
    third_scene, third = mock_scene_voiceover(source, cache_dir)

    assert third_scene.sounds == first_scene.sounds
    assert third.duration == pytest.approx(wav_duration(FIXTURES / "take_a.wav"))
    assert audio_files(cache_dir) == copies


@pytest.mark.parametrize("damage", ["deleted", "overwritten"])
def test_missing_or_damaged_cached_copy_is_restored(source, cache_dir, damage):
    first_scene, _ = mock_scene_voiceover(source, cache_dir)
    cached = Path(first_scene.sounds[0])
    if damage == "deleted":
        cached.unlink()
    else:
        cached.write_bytes(b"damaged")

    second_scene, _ = mock_scene_voiceover(source, cache_dir)

    assert second_scene.sounds == first_scene.sounds
    assert cached.read_bytes() == source.read_bytes()
    assert list(audio_files(cache_dir)) == [cached.name]


# ---------------------------------------------------------------------------
# Regression: audio data that ends with an incomplete PCM frame
# ---------------------------------------------------------------------------

LIST_CHUNK = b"LIST" + struct.pack("<I", 16) + b"INFOISFT" + struct.pack("<I", 4) + b"s01\x00"
ODD_CHUNK = b"junk" + struct.pack("<I", 3) + b"abc" + b"\x00"  # odd size, followed by its RIFF pad byte


def pcm16_wav(channels, payload, trailing=b"", format_tag=1, extra_fmt=b"", leading=b""):
    """A 16-bit PCM WAV whose data chunk holds exactly ``payload`` (RIFF pad byte added when odd).

    ``leading`` chunks go between fmt and data; ``trailing`` chunks follow the data chunk.
    """
    block_align = channels * 2
    fmt = struct.pack("<HHIIHH", format_tag, channels, 8000, 8000 * block_align, block_align, 16) + extra_fmt
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + leading + data_chunk + trailing
    return b"RIFF" + struct.pack("<I", len(body)) + body


def frames_of(channels, count):
    return struct.pack("<h", 1200) * channels * count


INCOMPLETE_FINAL_FRAME = {
    "mono, 1 leftover byte": (1, 1, b""),
    "stereo, 1 leftover byte": (2, 1, b""),
    "stereo, 2 leftover bytes (one channel only)": (2, 2, b""),
    "stereo, 3 leftover bytes": (2, 3, b""),
    "mono, 1 leftover byte, then a LIST chunk": (1, 1, LIST_CHUNK),
    "stereo, 2 leftover bytes, then a LIST chunk": (2, 2, LIST_CHUNK),
    "stereo, 2 leftover bytes, after an odd-sized chunk": (2, 2, b""),
}


@pytest.mark.parametrize("case", INCOMPLETE_FINAL_FRAME)
def test_incomplete_final_frame_is_rejected_not_dropped(tmp_path, cache_dir, case):
    channels, leftover, trailing = INCOMPLETE_FINAL_FRAME[case]
    leading = ODD_CHUNK if "odd-sized chunk" in case else b""
    source = tmp_path / "input.wav"
    source.write_bytes(pcm16_wav(channels, frames_of(channels, 100) + b"\x7f" * leftover, trailing, leading=leading))
    before = source.read_bytes()

    with pytest.raises(InvalidRecordingError, match=f"incomplete PCM frame .*{leftover} leftover byte"):
        read_recording(source)
    with pytest.raises(InvalidRecordingError, match="incomplete PCM frame"):
        LocalWavService(source, cache_dir=cache_dir)

    assert not cache_dir.exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize("channels", [1, 2])
def test_incomplete_final_frame_replacement_is_rejected_at_render(tmp_path, cache_dir, channels):
    source = tmp_path / "input.wav"
    source.write_bytes(pcm16_wav(channels, frames_of(channels, 400)))
    service = LocalWavService(source, cache_dir=cache_dir)
    service._wrap_generate_from_text(TRANSCRIPT)
    cache_json = (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text()
    copies = audio_files(cache_dir)

    source.write_bytes(pcm16_wav(channels, frames_of(channels, 400) + b"\x7f"))  # same path, same transcript

    with pytest.raises(InvalidRecordingError, match="incomplete PCM frame"):
        service._wrap_generate_from_text(TRANSCRIPT)
    assert (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text() == cache_json
    assert audio_files(cache_dir) == copies


@pytest.mark.parametrize("channels", [1, 2])
def test_file_cut_inside_final_frame_is_rejected(tmp_path, channels):
    complete = pcm16_wav(channels, frames_of(channels, 100))
    source = tmp_path / "input.wav"
    source.write_bytes(complete[:-1])  # header promises 100 frames; the last one is cut short

    with pytest.raises(InvalidRecordingError, match="corrupt"):
        read_recording(source)


EXTENSIBLE_PCM = struct.pack("<HHI", 22, 16, 3) + PCM_SUBFORMAT


@pytest.mark.parametrize(
    ("channels", "frame_count", "trailing", "format_tag", "extra_fmt", "leading"),
    [
        (1, 801, b"", 1, b"", b""),  # odd frame count
        (2, 801, b"", 1, b"", b""),
        (1, 801, LIST_CHUNK, 1, b"", b""),
        (2, 800, LIST_CHUNK, 1, b"", b""),
        (2, 801, b"", 0xFFFE, EXTENSIBLE_PCM, b""),
        (1, 801, b"", 1, b"", ODD_CHUNK),
        (2, 801, b"", 1, b"", ODD_CHUNK),
    ],
    ids=[
        "mono",
        "stereo",
        "mono + LIST chunk",
        "stereo + LIST chunk",
        "stereo extensible",
        "mono after odd-sized chunk",
        "stereo after odd-sized chunk",
    ],
)
def test_complete_mono_and_stereo_frames_pass_validation(
    tmp_path, cache_dir, monkeypatch, channels, frame_count, trailing, format_tag, extra_fmt, leading
):
    # Validation only (mock scene). Decoder tools are stubbed as available so the result does not depend
    # on FFmpeg being installed; real decoding of every layout is covered by the integration tests.
    monkeypatch.setattr(local_wav, "_missing_decoder_tools", lambda: [])
    source = tmp_path / "input.wav"
    source.write_bytes(pcm16_wav(channels, frames_of(channels, frame_count), trailing, format_tag, extra_fmt, leading))

    recording = read_recording(source)
    scene, tracker = mock_scene_voiceover(source, cache_dir)

    assert (recording.channels, recording.frames) == (channels, frame_count)
    assert tracker.duration == pytest.approx(frame_count / 8000)
    assert Path(scene.sounds[0]).read_bytes() == source.read_bytes()


def test_frame_completeness_depends_on_channel_count(tmp_path):
    payload = frames_of(1, 801)  # 1602 bytes: 801 mono frames, or 400.5 stereo frames
    mono, stereo = tmp_path / "mono.wav", tmp_path / "stereo.wav"
    mono.write_bytes(pcm16_wav(1, payload))
    stereo.write_bytes(pcm16_wav(2, payload))

    assert read_recording(mono).frames == 801
    with pytest.raises(InvalidRecordingError, match="2 leftover bytes"):
        read_recording(stereo)


# ---------------------------------------------------------------------------
# Chunk layouts that pydub's built-in WAV reader cannot read (FFmpeg fallback)
# ---------------------------------------------------------------------------


def with_riff_size(content):
    return content[:4] + struct.pack("<I", len(content) - 8) + content[8:]


def odd_chunk_before_data(content):
    index = content.index(b"data")
    return with_riff_size(content[:index] + ODD_CHUNK + content[index:])


def data_after_ten_chunks(content):
    # fmt plus nine more chunks: pydub stops looking after ten chunks, before reaching data.
    index = content.index(b"data")
    extra = b"".join(b"junk" + struct.pack("<I", 2) + b"ab" for _ in range(9))
    return with_riff_size(content[:index] + extra + content[index:])


FFMPEG_LAYOUTS = {"odd-sized chunk before data": odd_chunk_before_data, "data after ten chunks": data_after_ten_chunks}


def tone_wav(channels, frames=16538, rate=22050):
    """About 0.75 s of 16-bit PCM, with a different tone per channel."""
    t = np.arange(frames) / rate
    tones = np.stack([np.sin(2 * np.pi * (440 + 220 * c) * t) for c in range(channels)], axis=1) * 8000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(tones.astype("<i2").tobytes())
    return buffer.getvalue()


def pydub_reads_directly(content):
    try:
        AudioSegment(data=content)
    except Exception:
        return False
    return True


def ffmpeg_tools_installed():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def without_ffmpeg_on_path(monkeypatch, tmp_path):
    """Make ffmpeg and ffprobe unavailable exactly as on a machine without FFmpeg installed."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None


def real_render(recording, media_dir):
    """A real Manim render of one voiceover block; returns what ended up in the output files."""

    class RecordingScene(VoiceoverScene):
        def construct(self):
            self.set_speech_service(LocalWavService(recording))
            with self.voiceover(text="Local recording") as tracker:
                self.voiceover_duration = tracker.duration
            self.wait(0.25)

    settings = {
        "media_dir": str(media_dir),
        "pixel_height": 36,
        "pixel_width": 64,
        "frame_rate": 5,
        "disable_caching": True,
        "output_file": "out",
        "verbosity": "ERROR",
        "progress_bar": "none",
    }
    with tempconfig(settings):
        scene = RecordingScene()
        scene.render()
        movie = Path(scene.renderer.file_writer.movie_file_path)
    with av.open(str(movie)) as container:
        video = container.streams.video[0]
        video_seconds = float(video.duration * video.time_base)
        audio = np.concatenate([frame.to_ndarray().reshape(-1) for frame in container.decode(audio=0)])
    return SimpleNamespace(
        voiceover_duration=scene.voiceover_duration,
        soundtrack=movie.with_suffix(".wav").read_bytes(),
        video_seconds=video_seconds,
        audio=audio,
    )


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_tools_installed(), reason="needs the FFmpeg command-line tools (ffmpeg and ffprobe)")
@pytest.mark.parametrize("channels", [1, 2], ids=["mono", "stereo"])
@pytest.mark.parametrize("layout", FFMPEG_LAYOUTS)
def test_real_render_of_ffmpeg_layout_matches_plain_layout(tmp_path, layout, channels):
    plain = tmp_path / "plain.wav"
    plain.write_bytes(tone_wav(channels))
    special = tmp_path / "special.wav"
    special.write_bytes(FFMPEG_LAYOUTS[layout](plain.read_bytes()))
    assert pydub_reads_directly(plain.read_bytes())
    assert not pydub_reads_directly(special.read_bytes())  # so this render really goes through FFmpeg

    expected = real_render(plain, tmp_path / "plain-media")
    actual = real_render(special, tmp_path / "special-media")

    assert actual.voiceover_duration == pytest.approx(16538 / 22050)
    assert actual.voiceover_duration == expected.voiceover_duration
    assert actual.soundtrack == expected.soundtrack
    assert actual.video_seconds == expected.video_seconds
    assert np.array_equal(actual.audio, expected.audio)


@pytest.mark.integration
@pytest.mark.parametrize("layout", FFMPEG_LAYOUTS)
def test_real_render_without_ffmpeg_raises_dependency_error(tmp_path, monkeypatch, layout):
    source = tmp_path / "special.wav"
    source.write_bytes(FFMPEG_LAYOUTS[layout](tone_wav(1)))
    before = source.read_bytes()
    without_ffmpeg_on_path(monkeypatch, tmp_path)

    with pytest.raises(AudioDecoderDependencyError, match="missing: ffmpeg, ffprobe") as error:
        real_render(source, tmp_path / "media")

    assert not isinstance(error.value, (InvalidRecordingError, FileNotFoundError))
    assert "is a valid 16-bit PCM WAV file" in str(error.value)
    assert source.read_bytes() == before
    assert not list((tmp_path / "media").rglob("*.mp4"))


@pytest.mark.integration
@pytest.mark.parametrize(
    "make",
    [lambda: tone_wav(1), lambda: tone_wav(2), lambda: with_riff_size(tone_wav(2) + LIST_CHUNK)],
    ids=["mono", "stereo", "stereo + trailing LIST chunk"],
)
def test_real_render_of_plain_layouts_needs_no_ffmpeg(tmp_path, monkeypatch, make):
    source = tmp_path / "plain.wav"
    source.write_bytes(make())
    expected = real_render(source, tmp_path / "with-path")
    without_ffmpeg_on_path(monkeypatch, tmp_path)

    actual = real_render(source, tmp_path / "without-ffmpeg")

    assert actual.voiceover_duration == pytest.approx(16538 / 22050)
    assert actual.soundtrack == expected.soundtrack
    assert np.array_equal(actual.audio, expected.audio)


# Validation-level checks of the dependency error (mock scene, no decoding).


@pytest.mark.parametrize("missing", [["ffmpeg"], ["ffprobe"], ["ffmpeg", "ffprobe"]], ids=["ffmpeg", "ffprobe", "both"])
@pytest.mark.parametrize("layout", FFMPEG_LAYOUTS)
def test_missing_decoder_tool_is_a_dependency_error(tmp_path, cache_dir, monkeypatch, layout, missing):
    source = tmp_path / "input.wav"
    source.write_bytes(FFMPEG_LAYOUTS[layout](tone_wav(2)))
    real_which = shutil.which
    monkeypatch.setattr(local_wav.shutil, "which", lambda name: None if name in missing else real_which(name) or name)

    with pytest.raises(AudioDecoderDependencyError, match=f"missing: {', '.join(missing)}\\."):
        LocalWavService(source, cache_dir=cache_dir)
    assert not cache_dir.exists()


def test_dependency_error_at_render_time_keeps_existing_cache(tmp_path, cache_dir, monkeypatch):
    source = tmp_path / "input.wav"
    source.write_bytes(tone_wav(1))
    service = LocalWavService(source, cache_dir=cache_dir)
    service._wrap_generate_from_text(TRANSCRIPT)
    cache_json = (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text()
    copies = audio_files(cache_dir)

    source.write_bytes(odd_chunk_before_data(tone_wav(1)))  # same path, same transcript, FFmpeg-only layout
    without_ffmpeg_on_path(monkeypatch, tmp_path)

    with pytest.raises(AudioDecoderDependencyError):
        service._wrap_generate_from_text(TRANSCRIPT)
    assert (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text() == cache_json
    assert audio_files(cache_dir) == copies


def test_plain_layouts_do_not_look_for_decoder_tools(tmp_path, cache_dir, monkeypatch):
    monkeypatch.setattr(local_wav, "_missing_decoder_tools", lambda: pytest.fail("decoder tools were consulted"))
    for channels in (1, 2):
        source = tmp_path / f"plain-{channels}.wav"
        source.write_bytes(with_riff_size(tone_wav(channels) + LIST_CHUNK))
        mock_scene_voiceover(source, cache_dir)


def test_recording_that_pydub_would_decode_differently_is_rejected(tmp_path, cache_dir, monkeypatch):
    class ShiftedDecoder:
        def __init__(self, data):
            real = AudioSegment(data=data)
            self.channels, self.frame_rate, self.sample_width = real.channels, real.frame_rate, real.sample_width
            self.raw_data = real.raw_data[2:] + b"\x00\x00"

    source = tmp_path / "input.wav"
    source.write_bytes(tone_wav(1))
    monkeypatch.setattr(local_wav, "AudioSegment", ShiftedDecoder)

    with pytest.raises(InvalidRecordingError, match="would decode different audio"):
        LocalWavService(source, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Channel limit: S01 accepts mono and stereo narration only
# ---------------------------------------------------------------------------

SURROUND = {
    "3 channels": lambda: tone_wav(3),
    "4 channels": lambda: tone_wav(4),
    "6 channels": lambda: tone_wav(6),
    "6 channels, extensible 5.1": lambda: pcm16_wav(
        6, frames_of(6, 800), format_tag=0xFFFE, extra_fmt=struct.pack("<HHI", 22, 16, 0x3F) + PCM_SUBFORMAT
    ),
}


@pytest.mark.parametrize("case", SURROUND)
def test_more_than_two_channels_is_rejected(tmp_path, cache_dir, case):
    source = tmp_path / "input.wav"
    source.write_bytes(SURROUND[case]())
    before = source.read_bytes()

    with pytest.raises(InvalidRecordingError, match=r"has \d channels; only mono or stereo narration is supported"):
        read_recording(source)
    with pytest.raises(InvalidRecordingError, match="only mono or stereo"):
        LocalWavService(source, cache_dir=cache_dir)
    assert not cache_dir.exists()
    assert source.read_bytes() == before


@pytest.mark.parametrize(("channels", "accepted"), [(1, True), (2, True), (3, False)], ids=["mono", "stereo", "3 channels"])
def test_channel_limit_boundary(tmp_path, channels, accepted):
    source = tmp_path / "input.wav"
    source.write_bytes(tone_wav(channels))

    if accepted:
        assert read_recording(source).channels == channels
    else:
        with pytest.raises(InvalidRecordingError, match="only mono or stereo"):
            read_recording(source)


def test_surround_replacement_is_rejected_at_render_and_keeps_cache(tmp_path, cache_dir):
    source = tmp_path / "input.wav"
    source.write_bytes(tone_wav(2))
    service = LocalWavService(source, cache_dir=cache_dir)
    service._wrap_generate_from_text(TRANSCRIPT)
    cache_json = (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text()
    copies = audio_files(cache_dir)

    source.write_bytes(tone_wav(6))  # same path, same transcript

    with pytest.raises(InvalidRecordingError, match="only mono or stereo"):
        service._wrap_generate_from_text(TRANSCRIPT)
    assert (cache_dir / DEFAULT_VOICEOVER_CACHE_JSON_FILENAME).read_text() == cache_json
    assert audio_files(cache_dir) == copies


@pytest.mark.integration
def test_real_render_of_surround_recording_fails_before_rendering(tmp_path):
    source = tmp_path / "surround.wav"
    source.write_bytes(tone_wav(6))

    with pytest.raises(InvalidRecordingError, match="6 channels; only mono or stereo"):
        real_render(source, tmp_path / "media")
    assert not list((tmp_path / "media").rglob("*.mp4"))
