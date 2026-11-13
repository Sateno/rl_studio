"""Shared render verification: Manim's frame rules and measurements of rendered audio and video.

Used by the feature verifiers (scripts/verify_local_wav_render.py and
scripts/verify_cue_bindings_render.py) and by the independent expected timelines. It does not
import Manim or Manim Voiceover, so expectations computed with it stay independent of the
render they check.

Manim 0.20.1 frame rules reproduced here (Cairo renderer):
- a run time or wait of zero or less is rejected, and one shorter than a frame is
  raised to exactly one frame (Scene.validate_run_time);
- an animation of run_time r writes len(numpy.arange(0, r, 1 / fps)) frames;
- a static wait of d seconds writes int(d / (1 / fps)) frames;
- the renderer clock advances 1 / fps for each frame written;
- a voiceover block waits for the rest of its recording only if more than one
  frame remains (VoiceoverScene.safe_wait);
- a recording's audio is overlaid at int(1000 * clock) milliseconds.
"""

import hashlib
import json
import os
import wave
from pathlib import Path

import av
import numpy as np

RENDER_FPS = 15  # manim -ql, as used by scripts/dev.sh
CORRELATION_THRESHOLD = 0.97  # minimum correlation of encoded audio with its source recording
VOICED_LEVEL = 128  # |sample| above which a 16-bit sample counts as voiced


# ---------------------------------------------------------------------------
# Manim's frame rules
# ---------------------------------------------------------------------------


def validated_run_time(run_time: float, fps: int = RENDER_FPS) -> float:
    """Manim rejects run times of zero or less and raises shorter than one frame to one frame."""
    if run_time <= 0:
        raise ValueError(f"Manim cannot render a run time of {run_time:g} seconds")
    return max(run_time, 1 / fps)


def animation_frames(run_time: float, fps: int = RENDER_FPS) -> int:
    return len(np.arange(0, validated_run_time(run_time, fps), 1 / fps))


def static_wait_frames(duration: float, fps: int = RENDER_FPS) -> int:
    return int(validated_run_time(duration, fps) / (1 / fps))


class FrameClock:
    """Manim's renderer clock: frames written and the floating-point time they add up to."""

    def __init__(self, fps: int) -> None:
        self.fps = fps
        self.frames = 0
        self.time = 0.0

    def animate(self, run_time: float) -> None:
        for _ in range(animation_frames(run_time, self.fps)):
            self.frames += 1
            self.time += 1 / self.fps

    def wait_static(self, duration: float) -> None:
        count = static_wait_frames(duration, self.fps)
        self.frames += count
        self.time += count * (1 / self.fps)


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
    """Sample range of the last second of ``source`` that ends at its final voiced sample."""
    voiced = np.flatnonzero(np.abs(source.astype(int)) > VOICED_LEVEL)
    last = int(voiced[-1]) if len(voiced) else len(source) - 1
    return max(0, last - rate), last + 1


def check(name: str, passed: bool, **details: object) -> dict[str, object]:
    return {"check": name, "passed": bool(passed), **details}
