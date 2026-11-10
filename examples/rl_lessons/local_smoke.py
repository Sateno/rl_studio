"""Local baseline fixture using existing add_sound, not a new speech service.

Uses the upstream examples' VoiceoverScene/play/wait structure. No network,
microphone, transcription model, or prerecorded-file service is required.
"""

import json
from pathlib import Path

from manim import DOWN, UP, FadeIn, FadeOut, MathTex, Text, VGroup

from manim_voiceover import VoiceoverScene

ASSETS = Path(__file__).resolve().parent / "assets"


class LocalSmoke(VoiceoverScene):
    def construct(self) -> None:
        narration = json.loads((ASSETS / "narration.json").read_text(encoding="utf-8"))
        self.camera.background_color = "#101827"
        title = Text("RL Studio | Local rendering test", font_size=34, color="#8BD5CA").to_edge(UP)
        footer = Text("Expected immediate reward", font_size=24, color="#AAB7CE").to_edge(DOWN)
        panels = [
            VGroup(
                Text("Which action should we choose?", font_size=38),
                Text("A: reward 2, every time", font_size=30),
                Text("B: reward 4 with probability 0.75; otherwise 0", font_size=27),
            ).arrange(DOWN, buff=0.55),
            VGroup(
                Text("Weight each reward by its probability", font_size=34),
                MathTex(r"\mathbb{E}[R] = \sum_r p(r)\,r", font_size=56),
            ).arrange(DOWN, buff=0.65),
            VGroup(Text("Action B", font_size=36), MathTex(r"\mathbb{E}[R_B] = 0.25(0) + 0.75(4) = 3", font_size=52)).arrange(
                DOWN, buff=0.65
            ),
            VGroup(
                MathTex(r"\mathbb{E}[R_B] = 3 > 2 = \mathbb{E}[R_A]", font_size=52),
                Text("Prefer B for expected immediate reward", font_size=31),
                Text("An average is not a guarantee for one trial", font_size=27, color="#AAB7CE"),
            ).arrange(DOWN, buff=0.55),
        ]
        self.add(title, footer)
        self.add_sound(str(ASSETS / "pilot.wav"))
        end = 0.0
        for panel, duration in zip(panels, narration["durations"], strict=True):
            if panel.width > 12.5:
                panel.scale_to_fit_width(12.5)
            end += duration
            self.play(FadeIn(panel), run_time=0.4)
            self.wait(max(0.0, end - self.renderer.time - 0.3))
            self.play(FadeOut(panel), run_time=0.3)
        self.wait(0.75)
