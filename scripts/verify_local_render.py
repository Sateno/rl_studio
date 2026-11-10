"""Check the smoke render's duration and preservation of its final spoken audio."""

import hashlib
import io
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import correlate

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "examples" / "rl_lessons" / "assets"
VIDEO = ROOT / "media" / "videos" / "local_smoke" / "480p15" / "LocalSmoke.mp4"
REPORT = ROOT / "media" / "local_validation"


def main() -> None:
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(VIDEO),
            ]
        )
    )
    kinds = {stream["codec_type"] for stream in info["streams"]}
    assert {"video", "audio"} <= kinds, "Video or audio stream missing"
    with wave.open(str(ASSETS / "pilot.wav"), "rb") as audio:
        rate = audio.getframerate()
        source = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float)
    decoded_bytes = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(VIDEO),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "s16le",
            "pipe:1",
        ]
    )
    decoded = np.frombuffer(decoded_bytes, dtype="<i2").astype(float)
    narration_duration = len(source) / rate
    duration = float(info["format"]["duration"])
    assert narration_duration <= duration <= narration_duration + 1.5, (narration_duration, duration)
    last = int(np.flatnonzero(np.abs(source) > 128)[-1])
    start = max(0, last - rate)
    reference = source[start : last + 1]
    search = decoded[max(0, start - rate // 5) : last + 1 + rate // 5]
    scores = correlate(search, reference, mode="valid", method="fft")
    best = int(np.argmax(scores))
    match = search[best : best + len(reference)]
    correlation = float(np.dot(match, reference) / (np.linalg.norm(match) * np.linalg.norm(reference)))
    assert correlation > 0.97, f"Final spoken audio does not match: {correlation}"
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ASSETS.glob("*.wav")}
    assert hashes["take_a.wav"] != hashes["take_b.wav"], "Takes must differ"
    summary = {
        "video": str(VIDEO.relative_to(ROOT)),
        "duration_seconds": duration,
        "narration_seconds": narration_duration,
        "final_audio_correlation": correlation,
        "fixture_sha256": hashes,
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sheet = Image.new("RGB", (1280, 760), "#172033")
    draw = ImageDraw.Draw(sheet)
    section_durations = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))["durations"]
    midpoints = np.cumsum(section_durations) - np.array(section_durations) / 2
    for index, second in enumerate(midpoints):
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(second),
                "-i",
                str(VIDEO),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ]
        )
        frame = Image.open(io.BytesIO(raw)).convert("RGB")
        frame.thumbnail((640, 360))
        x, y = (index % 2) * 640, (index // 2) * 380
        sheet.paste(frame, (x, y))
        draw.text((x + 10, y + 360), f"{second:.1f} seconds", fill="white")
    sheet.save(REPORT / "contact-sheet.png")
    print(json.dumps({key: value for key, value in summary.items() if key != "fixture_sha256"}, indent=2))


if __name__ == "__main__":
    main()
