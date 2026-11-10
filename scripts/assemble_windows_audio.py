"""Replace the smoke fixture with WAVs exported by Windows SpeechSynthesizer.

This only prepares example assets; it does not implement a Voiceover service.
"""

import json
import shutil
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "examples" / "rl_lessons" / "assets"
GENERATED = ROOT / "media" / "local_validation" / "windows-voice"
BACKUP = ROOT / "media" / "local_validation" / "espeak-original"


def main() -> None:
    manifest = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))
    voice = json.loads((GENERATED / "voice.json").read_text(encoding="utf-8-sig"))
    if voice["sections"] != manifest["sections"]:
        raise ValueError("Generated narration does not match the existing lesson text")
    parts: list[bytes] = []
    durations: list[float] = []
    rate = 22050
    for index in range(len(manifest["sections"])):
        with wave.open(str(GENERATED / f"section_{index}.wav"), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, rate):
                raise ValueError("Expected mono 16-bit PCM WAV at 22050 Hz")
            data = audio.readframes(audio.getnframes()) + b"\x00\x00" * int(rate * 0.3)
        parts.append(data)
        durations.append(len(data) / 2 / rate)
    if not BACKUP.exists():
        shutil.copytree(ASSETS, BACKUP)
        video = ROOT / "media" / "videos" / "local_smoke" / "480p15" / "LocalSmoke.mp4"
        if video.exists():
            shutil.copy2(video, BACKUP / "LocalSmoke-espeak.mp4")
    with wave.open(str(ASSETS / "pilot.wav"), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(parts))
    for index in range(len(parts)):
        shutil.copy2(GENERATED / f"section_{index}.wav", ASSETS / f"section_{index}.wav")
    manifest.update(
        {
            "generator": "Windows System.Speech (offline)",
            "voice": voice["voice"],
            "voice_rate": voice["rate"],
            "durations": durations,
            "duration": sum(durations),
            "take_generator": "espeak-ng (original A/B test fixtures retained)",
        }
    )
    (ASSETS / "narration.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Updated narration: {voice['voice']}, {sum(durations):.3f} seconds; lesson text unchanged.")


if __name__ == "__main__":
    main()
