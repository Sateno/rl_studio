"""S01: a local 16-bit PCM WAV recording played through LocalWavService."""

import os
from pathlib import Path

from manim import DOWN, UP, Text
from manim_voiceover import VoiceoverScene
from manim_voiceover.services.local_wav import LocalWavService

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = "This is the approved local recording for R L Studio. The final words are complete."
# Short hold after the voiceover. It is required, not cosmetic: Manim renders whole frames, so
# without it the encoded video ends slightly before the recording does (measured at 15 fps:
# 11 ms early for take a, 18 ms for take b), and `verify` fails because the video must cover
# the whole recording. With 0.25 s the video ends 0.18-0.19 s after the recording, inside
# the verifier's 0.4 s limit.
HOLD_SECONDS = 0.25


class BaselineRecording(VoiceoverScene):
    def construct(self) -> None:
        audio_path = Path(os.environ.get("S01_AUDIO", str(ROOT / "fixtures" / "take_a.wav")))
        self.set_speech_service(LocalWavService(audio_path))
        self.camera.background_color = "#101827"
        self.add(
            Text("S01 local recording", font_size=42, color="#8BD5CA").shift(UP),
            Text("Local WAV through LocalWavService", font_size=28).shift(DOWN * 0.3),
        )
        # The voiceover block lasts exactly as long as the recording.
        with self.voiceover(text=TRANSCRIPT) as tracker:
            self.add(Text(f"Recording length: {tracker.duration:.2f} s", font_size=22).shift(DOWN * 1.2))
        self.wait(HOLD_SECONDS)
