"""Verify a Local WAV Narration render of take a or b, without external FFmpeg executables.

Checks that the supplied fixtures are unchanged, that the MP4 has video and audio, that the
video covers the whole recording and ends at most 0.4 seconds after it, and that the final
voiced second of the recording correlates with the encoded audio above
CORRELATION_THRESHOLD. The result is written to media/local_wav/verification-TAKE.json.

Usage:
    python scripts/verify_local_wav_render.py --take a
"""

import argparse
import hashlib
import json
import wave
from pathlib import Path

import av
import numpy as np
from render_verification import CORRELATION_THRESHOLD, correlation, final_voiced_second, locate

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media" / "local_wav"
VIDEO_DIR = MEDIA / "videos" / "local_wav_baseline" / "480p15"
MAX_VIDEO_OVERHANG_SECONDS = 0.4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--take", choices=("a", "b"), required=True)
    take = parser.parse_args().take
    fixtures = ROOT / "fixtures"
    info = json.loads((fixtures / "info.json").read_text(encoding="utf-8"))
    for filename, expected in info["sha256"].items():
        assert hashlib.sha256((fixtures / filename).read_bytes()).hexdigest() == expected, filename
    source_path = fixtures / f"take_{take}.wav"
    with wave.open(str(source_path), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        rate = audio.getframerate()
        source = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float)
    video_path = VIDEO_DIR / f"baseline-{take}.mp4"
    chunks = []
    with av.open(str(video_path)) as container:
        assert len(container.streams.video) > 0 and len(container.streams.audio) > 0
        stream = container.streams.video[0]
        assert stream.duration is not None and stream.time_base is not None
        duration = float(stream.duration * stream.time_base)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            chunks.extend(output.to_ndarray().reshape(-1) for output in resampler.resample(frame))
        chunks.extend(output.to_ndarray().reshape(-1) for output in resampler.resample(None))
    decoded = np.concatenate(chunks).astype(float)
    source_duration = len(source) / rate
    assert source_duration <= duration <= source_duration + MAX_VIDEO_OVERHANG_SECONDS, (source_duration, duration)
    start, stop = final_voiced_second(source, rate)
    reference = source[start:stop]
    search = decoded[max(0, start - rate // 5) : stop + rate // 5]
    best = locate(reference, search)
    similarity = correlation(search[best : best + len(reference)], reference)
    assert similarity > CORRELATION_THRESHOLD, similarity
    result = {
        "take": take,
        "video": str(video_path.relative_to(ROOT)),
        "source_seconds": source_duration,
        "video_seconds": duration,
        "final_audio_correlation": similarity,
        "source_fixtures_unchanged": True,
    }
    (MEDIA / f"verification-{take}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
