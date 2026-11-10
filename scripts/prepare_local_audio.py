"""Generate offline synthetic test fixtures, not production narration."""

import json
import subprocess
import wave
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "examples" / "rl_lessons" / "assets"
SECTIONS = [
    "Which uncertain choice is better? Suppose one action gives a reward of two every time, "
    "while another gives four with probability three quarters, and zero otherwise.",
    "For the uncertain action, we multiply each possible reward by its probability, then add the results.",
    "One quarter times zero, plus three quarters times four, gives an expected reward of three.",
    "Three is greater than two. A decision maker maximizing expected immediate reward therefore prefers the uncertain action. "
    "This comparison is about averages, not a guarantee for any single trial.",
]


def synthesize(path: Path, text: str, speed: int, voice: str = "en-us") -> None:
    subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(path), text], check=True)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    if (ASSETS / "narration.json").exists():
        for name in ("pilot.wav", "take_a.wav", "take_b.wav"):
            if not (ASSETS / name).is_file():
                raise FileNotFoundError(ASSETS / name)
        print("Using existing narration and recording fixtures without modification.")
        return
    parts: list[bytes] = []
    durations: list[float] = []
    rate = 22050
    for index, text in enumerate(SECTIONS):
        path = ASSETS / f"section_{index}.wav"
        synthesize(path, text, 150)
        with wave.open(str(path), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, rate)
            data = audio.readframes(audio.getnframes()) + b"\x00\x00" * int(rate * 0.3)
        parts.append(data)
        durations.append(len(data) / 2 / rate)
    with wave.open(str(ASSETS / "pilot.wav"), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(parts))
    take_text = "This is the approved local recording for R L Studio. The final words are complete."
    synthesize(ASSETS / "take_a.wav", take_text, 150)
    synthesize(ASSETS / "take_b.wav", take_text, 180, "en-gb")
    manifest = {
        "generator": "espeak-ng (offline synthetic test speech)",
        "sections": SECTIONS,
        "durations": durations,
        "duration": sum(durations),
        "take_transcript": take_text,
    }
    (ASSETS / "narration.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Narration duration: {sum(durations):.3f}s; two distinct PCM WAV takes prepared.")


if __name__ == "__main__":
    main()
