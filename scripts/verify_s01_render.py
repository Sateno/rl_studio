"""Validate the supplied render without external FFmpeg executables."""

import argparse
import hashlib
import json
import wave
from pathlib import Path

import av
import numpy as np
from scipy.signal import correlate

ROOT = Path(__file__).resolve().parents[1]


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
    video_path = ROOT / "media" / "s01" / "videos" / "s01_baseline" / "480p15" / f"baseline-{take}.mp4"
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
    assert source_duration <= duration <= source_duration + 0.4, (source_duration, duration)
    last = int(np.flatnonzero(np.abs(source) > 128)[-1])
    start = max(0, last - rate)
    reference = source[start : last + 1]
    search = decoded[max(0, start - rate // 5) : last + 1 + rate // 5]
    scores = correlate(search, reference, mode="valid", method="fft")
    best = int(np.argmax(scores))
    match = search[best : best + len(reference)]
    similarity = float(np.dot(match, reference) / (np.linalg.norm(match) * np.linalg.norm(reference)))
    assert similarity > 0.97, similarity
    result = {
        "take": take,
        "video": str(video_path.relative_to(ROOT)),
        "source_seconds": source_duration,
        "video_seconds": duration,
        "final_audio_correlation": similarity,
        "source_fixtures_unchanged": True,
    }
    (ROOT / "media" / "s01" / f"verification-{take}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
