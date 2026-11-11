"""Speech service that plays a prerecorded local WAV file.

The recording must be an uncompressed 16-bit PCM WAV file with one (mono) or
two (stereo) channels; S01 is for narration, and Manim would downmix surround
audio to stereo. It is played at normal speed, and the voiceover lasts exactly
as long as the recording.

The source file is only ever read. Each render reads it once, validates
those bytes, and identifies them by their SHA-256 hash. The cached copy is
written from the same bytes. Replacing the audio at the same path
therefore produces a new cache entry on the next render. Unchanged audio
reuses its existing cached copy without writing it again.

Manim's Scene.add_sound decodes WAV files with pydub. pydub's built-in WAV
reader does not skip RIFF pad bytes and reads at most ten chunks. Two valid
layouts therefore need its fallback, which runs the FFmpeg command-line tools
(ffmpeg and ffprobe): an odd-sized chunk before the data chunk, or a data
chunk after ten other chunks. These recordings are accepted; if the tools are
missing, AudioDecoderDependencyError is raised before rendering instead of a
misleading file error.
"""

import hashlib
import io
import os
import shutil
import struct
import uuid
import warnings
import wave
from dataclasses import dataclass, field
from pathlib import Path

from pydub import AudioSegment
from pydub import utils as pydub_utils

from manim_voiceover._typing import JsonValue, VoiceoverData
from manim_voiceover.helper import remove_bookmarks
from manim_voiceover.services.base import PathLike, SpeechService, initialize_speech_service, path_to_string

SERVICE_NAME = "local_wav"
_SAMPLE_WIDTH_BYTES = 2
_MAX_CHANNELS = 2  # mono or stereo narration


class InvalidRecordingError(ValueError):
    """The recording is empty, corrupt, not an uncompressed 16-bit PCM WAV file, or not mono/stereo."""


class AudioDecoderDependencyError(RuntimeError):
    """A valid recording can only be decoded with the FFmpeg command-line tools, which are missing."""


@dataclass(frozen=True)
class WavRecording:
    """A validated snapshot of a recording's bytes and format."""

    data: bytes
    sha256: str
    channels: int
    sample_rate: int
    frames: int
    pcm: bytes = field(repr=False)

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def _data_chunk_size(data: bytes) -> int | None:
    """Return the declared size of the RIFF ``data`` chunk.

    The wave module only reports whole frames, which hides leftover bytes of an
    incomplete final frame. Chunks are word-aligned, as in the wave module.
    """
    offset = 12  # "RIFF", the RIFF size and "WAVE"
    while offset + 8 <= len(data):
        chunk_id, size = struct.unpack_from("<4sI", data, offset)
        if chunk_id == b"data":
            return int(size)
        offset += 8 + size + (size & 1)
    return None


def _check_complete_frames(data: bytes, frames: int, block_align: int, source: Path) -> None:
    data_size = _data_chunk_size(data)
    if data_size is None:
        raise InvalidRecordingError(f"Recording is corrupt: no data chunk found: {source}")
    leftover = data_size % block_align
    if leftover:
        raise InvalidRecordingError(
            f"Recording is corrupt: audio data ends with an incomplete PCM frame "
            f"({leftover} leftover byte{'s' if leftover != 1 else ''} after {frames} complete frames): {source}"
        )


def _parse_pcm16(data: bytes, source: Path) -> WavRecording:
    try:
        with wave.open(io.BytesIO(data)) as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            pcm = audio.readframes(frames)
    except (wave.Error, EOFError, RuntimeError) as exc:
        # wave raises Error for unknown/compressed formats and malformed headers,
        # EOFError for truncated headers and a bare RuntimeError for bad chunk sizes.
        detail = str(exc) or type(exc).__name__
        raise InvalidRecordingError(f"Recording is corrupt or not an uncompressed PCM WAV file: {source} ({detail})") from exc

    if sample_width != _SAMPLE_WIDTH_BYTES:
        raise InvalidRecordingError(f"Recording must be 16-bit PCM, got {8 * sample_width}-bit samples: {source}")
    if channels > _MAX_CHANNELS:
        raise InvalidRecordingError(f"Recording has {channels} channels; only mono or stereo narration is supported: {source}")
    if sample_rate <= 0:
        raise InvalidRecordingError(f"Recording is corrupt: invalid sample rate {sample_rate}: {source}")
    if frames == 0:
        raise InvalidRecordingError(f"Recording contains no audio frames: {source}")
    if len(pcm) != frames * channels * sample_width:
        present = len(pcm) // (channels * sample_width)
        raise InvalidRecordingError(
            f"Recording is corrupt: header declares {frames} frames but only {present} are present: {source}"
        )
    _check_complete_frames(data, frames, channels * sample_width, source)
    return WavRecording(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        channels=channels,
        sample_rate=sample_rate,
        frames=frames,
        pcm=pcm,
    )


def read_recording(path: PathLike) -> WavRecording:
    """Read and validate a local uncompressed 16-bit PCM WAV file without modifying it.

    Raises:
        FileNotFoundError: The path does not exist or is not a regular file.
        InvalidRecordingError: The file is empty, corrupt, in an unsupported format, or has more than two channels.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Recording not found or not a regular file: {source}")
    data = source.read_bytes()
    if not data:
        raise InvalidRecordingError(f"Recording is empty: {source}")
    return _parse_pcm16(data, source)


def _decodes_directly(recording: WavRecording, source: Path) -> bool:
    """Whether pydub's built-in WAV reader (tried first by Scene.add_sound) reads the recording."""
    try:
        segment = AudioSegment(data=recording.data)
    except Exception:  # pydub's from_file() also falls back to FFmpeg on any failure here
        return False
    decoded = (segment.channels, segment.frame_rate, segment.sample_width, segment.raw_data)
    if decoded != (recording.channels, recording.sample_rate, _SAMPLE_WIDTH_BYTES, recording.pcm):
        raise InvalidRecordingError(f"Manim's WAV reader (pydub) would decode different audio from this recording: {source}")
    return True


def _missing_decoder_tools() -> list[str]:
    """FFmpeg tools that pydub's fallback decoder runs (both are required) but cannot find."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # pydub warns when it cannot find ffprobe
        prober = pydub_utils.get_prober_name()
    tools = {"ffmpeg": AudioSegment.converter, "ffprobe": prober}
    return [name for name, command in tools.items() if shutil.which(command) is None]


def ensure_decodable(recording: WavRecording, source: Path) -> None:
    """Raise AudioDecoderDependencyError if rendering the recording needs FFmpeg tools that are missing."""
    if _decodes_directly(recording, source):
        return
    missing = _missing_decoder_tools()
    if missing:
        raise AudioDecoderDependencyError(
            f"{source} is a valid 16-bit PCM WAV file, but its chunk layout (an odd-sized chunk before the data "
            "chunk, or the data chunk after ten other chunks) cannot be read by pydub's built-in WAV reader, "
            "which Manim uses. Decoding it requires the FFmpeg command-line tools ffmpeg and ffprobe; missing: "
            f"{', '.join(missing)}. Install FFmpeg, or save the recording as a plain WAV file."
        )


def _is_intact_copy(path: Path, sha256: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256


def _write_copy(destination: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``destination`` so a partial copy is never visible."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(data)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


class LocalWavService(SpeechService):
    """Speech service for a single prerecorded local WAV file.

    The voiceover text is the transcript of the recording. It is used for
    subcaptions and the cache key; it is not synthesized or transcribed.

    Example:
        .. code-block:: python

            self.set_speech_service(LocalWavService("narration.wav"))
            with self.voiceover(text="Transcript of narration.wav") as tracker:
                ...  # tracker.duration is the recording's duration
    """

    def __init__(self, audio_path: PathLike, **kwargs: object) -> None:
        """
        Args:
            audio_path (str | os.PathLike): Local uncompressed 16-bit PCM WAV file.
                It is validated here and again on every render, and never modified.
            **kwargs: Passed to :class:`SpeechService` (for example ``cache_dir``).
                ``global_speed`` must stay 1.0 because the recording plays at normal speed.

        Raises:
            FileNotFoundError: The recording does not exist or is not a regular file.
            InvalidRecordingError: The recording is empty, corrupt, not 16-bit PCM, or not mono/stereo.
            AudioDecoderDependencyError: The recording is valid, but its chunk layout needs the
                FFmpeg command-line tools (ffmpeg and ffprobe) to be decoded, and they are missing.
        """
        self.audio_path = Path(path_to_string(audio_path))
        ensure_decodable(read_recording(self.audio_path), self.audio_path)
        initialize_speech_service(self, kwargs)
        if self.global_speed != 1.0:
            raise ValueError("LocalWavService plays the recording at normal speed; global_speed must be 1.0")

    def generate_from_text(
        self,
        text: str,
        cache_dir: PathLike | None = None,
        path: PathLike | None = None,
        **kwargs: object,
    ) -> VoiceoverData:
        """"""
        if cache_dir is None:
            cache_dir = self.cache_dir

        recording = read_recording(self.audio_path)
        ensure_decodable(recording, self.audio_path)
        input_data: dict[str, JsonValue] = {
            "input_text": remove_bookmarks(text),
            "service": SERVICE_NAME,
            "config": {"sha256": recording.sha256},
        }

        cached_result = self.get_cached_result(input_data, cache_dir)
        cached_audio = None if cached_result is None else cached_result.get("original_audio")
        if cached_result is not None and cached_audio is not None:
            if _is_intact_copy(Path(cache_dir) / cached_audio, recording.sha256):
                return cached_result

        audio_path = self._output_name(input_data, path, cached_audio)
        destination = Path(cache_dir) / audio_path
        if _same_file(destination, self.audio_path):
            raise ValueError(f"Refusing to overwrite the source recording: {self.audio_path}")
        _write_copy(destination, recording.data)

        json_dict: VoiceoverData = {
            "input_text": text,
            "input_data": input_data,
            "original_audio": audio_path,
        }
        return json_dict

    def _output_name(self, input_data: dict[str, JsonValue], path: PathLike | None, cached_audio: str | None) -> str:
        if path is not None:
            audio_path = path_to_string(path)
            if not audio_path.endswith(".wav"):
                raise ValueError(f"LocalWavService output path must end with .wav: {audio_path}")
            return audio_path
        if cached_audio is not None:
            # A cache entry exists but its copy is missing or damaged: restore it in place.
            return cached_audio
        return self.get_audio_basename(input_data) + ".wav"
