"""Repository-level edge and diagnostic contracts for the imported S01 service."""

import json
import struct
import warnings
from types import SimpleNamespace

import pytest

from manim_voiceover.services import local_wav
from tests.test_local_wav_service import riff, write_pcm16


@pytest.mark.parametrize("size", [0, 0x80000000, 0xFFFFFFFF])
def test_chunk_size_reads_unsigned_size_at_exact_header_boundary(size):
    header = b"RIFF" + struct.pack("<I", 12) + b"WAVEdata" + struct.pack("<I", size)
    assert local_wav._data_chunk_size(header) == size


@pytest.mark.parametrize("tail", [b"", b"data", b"data\x00\x00\x00"])
def test_chunk_size_handles_absent_or_incomplete_chunk_headers(tail):
    assert local_wav._data_chunk_size(b"RIFF\x04\x00\x00\x00WAVE" + tail) is None


def test_missing_data_diagnostic_identifies_the_recording(tmp_path):
    source = tmp_path / "missing-data.wav"
    with pytest.raises(local_wav.InvalidRecordingError) as error:
        local_wav._check_complete_frames(b"RIFF\x04\x00\x00\x00WAVE", 0, 2, source)
    assert str(error.value) == f"Recording is corrupt: no data chunk found: {source}"


@pytest.mark.parametrize(("channels", "leftover"), [(1, 1), (2, 2)])
def test_incomplete_frame_error_reports_bytes_frames_and_path(tmp_path, channels, leftover):
    source = tmp_path / "partial.wav"
    source.write_bytes(riff(1, channels, 8000, 16, b"\x00" * (100 * channels * 2 + leftover)))
    with pytest.raises(local_wav.InvalidRecordingError) as error:
        local_wav.read_recording(source)
    suffix = "s" if leftover != 1 else ""
    assert str(error.value) == (
        "Recording is corrupt: audio data ends with an incomplete PCM frame "
        f"({leftover} leftover byte{suffix} after 100 complete frames): {source}"
    )


@pytest.mark.parametrize("data", [b"RIFF", b"not-a-wave-file"])
def test_parser_error_keeps_cause_detail_and_source_path(tmp_path, data):
    source = tmp_path / "broken.wav"
    source.write_bytes(data)
    with pytest.raises(local_wav.InvalidRecordingError) as error:
        local_wav.read_recording(source)
    cause = error.value.__cause__
    assert cause is not None
    detail = str(cause) or type(cause).__name__
    assert str(error.value) == f"Recording is corrupt or not an uncompressed PCM WAV file: {source} ({detail})"


@pytest.mark.parametrize("bits", [8, 24])
def test_wrong_sample_width_diagnostic_reports_real_width(tmp_path, bits):
    source = tmp_path / "wrong-width.wav"
    source.write_bytes(riff(1, 1, 8000, bits, b"\x00" * (100 * bits // 8)))
    with pytest.raises(local_wav.InvalidRecordingError) as error:
        local_wav.read_recording(source)
    assert str(error.value) == f"Recording must be 16-bit PCM, got {bits}-bit samples: {source}"


def test_lowest_positive_sample_rate_is_not_zero(tmp_path):
    source = write_pcm16(tmp_path / "one-hz.wav", frames=1, rate=1)
    assert local_wav.read_recording(source).duration == 1.0


def test_missing_decoder_check_does_not_hide_unrelated_warnings(monkeypatch):
    def prober():
        warnings.warn("missing probe", RuntimeWarning, stacklevel=2)
        warnings.warn("other diagnostic", UserWarning, stacklevel=2)
        return "ffprobe"

    monkeypatch.setattr(local_wav.pydub_utils, "get_prober_name", prober)
    monkeypatch.setattr(local_wav.shutil, "which", lambda command: None)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        assert local_wav._missing_decoder_tools() == ["ffmpeg", "ffprobe"]
    assert [(warning.category, str(warning.message)) for warning in captured] == [(UserWarning, "other diagnostic")]


@pytest.mark.parametrize("when", ["construction", "render"])
def test_decoder_mismatch_diagnostic_keeps_the_source_path(tmp_path, monkeypatch, when):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    service = local_wav.LocalWavService(source, cache_dir=tmp_path / "cache") if when == "render" else None
    monkeypatch.setattr(
        local_wav, "AudioSegment", lambda data: SimpleNamespace(channels=1, frame_rate=8000, sample_width=2, raw_data=b"wrong")
    )
    with pytest.raises(local_wav.InvalidRecordingError) as error:
        if service is None:
            local_wav.LocalWavService(source, cache_dir=tmp_path / "cache")
        else:
            service.generate_from_text("Transcript")
    assert str(error.value) == f"Manim's WAV reader (pydub) would decode different audio from this recording: {source}"


def test_decoder_dependency_error_is_actionable(tmp_path, monkeypatch):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    monkeypatch.setattr(local_wav, "_decodes_directly", lambda recording, path: False)
    monkeypatch.setattr(local_wav, "_missing_decoder_tools", lambda: ["ffmpeg", "ffprobe"])
    with pytest.raises(local_wav.AudioDecoderDependencyError) as error:
        local_wav.LocalWavService(source, cache_dir=tmp_path / "cache")
    assert str(error.value) == (
        f"{source} is a valid 16-bit PCM WAV file, but its chunk layout (an odd-sized chunk before the data "
        "chunk, or the data chunk after ten other chunks) cannot be read by pydub's built-in WAV reader, "
        "which Manim uses. Decoding it requires the FFmpeg command-line tools ffmpeg and ffprobe; missing: "
        "ffmpeg, ffprobe. Install FFmpeg, or save the recording as a plain WAV file."
    )


def test_nested_output_directories_are_created(tmp_path):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    service = local_wav.LocalWavService(source, cache_dir=tmp_path / "cache")
    result = service._wrap_generate_from_text("Transcript", path="nested/deep/recording.wav")
    assert result["final_audio"] == "nested/deep/recording.wav"
    assert (tmp_path / "cache" / result["final_audio"]).read_bytes() == source.read_bytes()


def test_atomic_copy_preserves_original_failure_if_temporary_is_already_gone(tmp_path, monkeypatch):
    original = OSError("simulated rename failure")

    def failed_replace(source, destination):
        source.unlink()
        raise original

    monkeypatch.setattr(local_wav.os, "replace", failed_replace)
    with pytest.raises(OSError) as error:
        local_wav._write_copy(tmp_path / "cache" / "voice.wav", b"audio")
    assert error.value is original
    assert list((tmp_path / "cache").iterdir()) == []


def test_speed_error_explains_supported_setting(tmp_path):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    with pytest.raises(ValueError) as error:
        local_wav.LocalWavService(source, cache_dir=tmp_path / "cache", global_speed=2.0)
    assert str(error.value) == "LocalWavService plays the recording at normal speed; global_speed must be 1.0"


def test_incomplete_cache_metadata_is_rebuilt(tmp_path):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    cache = tmp_path / "cache"
    service = local_wav.LocalWavService(source, cache_dir=cache)
    first = service._wrap_generate_from_text("Transcript")
    incomplete = {key: value for key, value in first.items() if key not in ("original_audio", "final_audio")}
    (cache / "cache.json").write_text(json.dumps([incomplete]))
    second = service._wrap_generate_from_text("Transcript")
    assert (cache / second["final_audio"]).read_bytes() == source.read_bytes()


def test_missing_custom_cached_filename_is_restored_at_same_path(tmp_path):
    source = write_pcm16(tmp_path / "source.wav", frames=800)
    cache = tmp_path / "cache"
    service = local_wav.LocalWavService(source, cache_dir=cache)
    first = service._wrap_generate_from_text("Transcript", path="custom.wav")
    (cache / first["final_audio"]).unlink()
    second = service._wrap_generate_from_text("Transcript")
    assert second["final_audio"] == "custom.wav"
    assert (cache / "custom.wav").read_bytes() == source.read_bytes()
