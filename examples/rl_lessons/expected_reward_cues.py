"""Cue Bindings: the expected-reward lesson as three stable narration cues, with text-only panels.

The scene decides the cue order. A version 1 cue manifest binds each cue ID to a WAV
file in the manifest's directory (see manim_voiceover.services.cue_bindings), and each
cue is played through LocalWavService (Local WAV Narration), so its validation, source preservation
and caching apply unchanged. Rebinding one cue in the manifest changes only that cue's
recording; the panels and transcripts below stay the same.

Each voiceover block lasts as long as its recording. The panel fades in during the
block, and fades out after the block except for the last one. A final hold follows.
scripts/expected_reward_timeline.py predicts the resulting timeline independently from the same
declared layout and Manim's frame rules.

The manifest is taken from CUE_BINDINGS_MANIFEST, or defaults to the tracked manifest
next to the original recordings (sections 0, 2 and 3 of the lesson narration).
"""

import os
from pathlib import Path

from manim import DOWN, UP, FadeIn, FadeOut, Text, VGroup

from manim_voiceover import VoiceoverScene
from manim_voiceover.services.cue_bindings import load_cue_manifest

ASSETS = Path(__file__).resolve().parent / "assets"
DEFAULT_MANIFEST = ASSETS / "expected_reward_cues.json"
CUE_ORDER = ("question", "calculation", "decision")
# Verbatim transcripts of sections 0, 2 and 3 in assets/narration.json.
TRANSCRIPTS = {
    "question": (
        "Which uncertain choice is better? Suppose one action gives a reward of two every time, "
        "while another gives four with probability three quarters, and zero otherwise."
    ),
    "calculation": "One quarter times zero, plus three quarters times four, gives an expected reward of three.",
    "decision": (
        "Three is greater than two. A decision maker maximizing expected immediate reward therefore prefers "
        "the uncertain action. This comparison is about averages, not a guarantee for any single trial."
    ),
}
# Declared layout. scripts/expected_reward_timeline.py declares the same values independently; a test keeps them equal.
FADE_IN_SECONDS = 0.4
FADE_OUT_SECONDS = 0.3
FINAL_HOLD_SECONDS = 0.25
MAX_PANEL_WIDTH = 12.5


def build_panel(cue_id: str) -> VGroup:
    """The text-only panel shown while a cue plays."""
    if cue_id == "question":
        panel = VGroup(
            Text("Which action should we choose?", font_size=38),
            Text("A: reward 2, every time", font_size=30),
            Text("B: reward 4 with probability 0.75; otherwise 0", font_size=27),
        )
    elif cue_id == "calculation":
        panel = VGroup(
            Text("Expected reward of action B", font_size=34),
            Text("0.25(0) + 0.75(4) = 3", font_size=48),
        )
    elif cue_id == "decision":
        panel = VGroup(
            Text("3 > 2", font_size=56),
            Text("Prefer B for expected immediate reward", font_size=31),
            Text("An average is not a guarantee for one trial", font_size=27, color="#AAB7CE"),
        )
    else:
        raise ValueError(f"No panel for cue {cue_id!r}")
    panel.arrange(DOWN, buff=0.55)
    if panel.width > MAX_PANEL_WIDTH:
        panel.scale_to_fit_width(MAX_PANEL_WIDTH)
    return panel


class ExpectedRewardCues(VoiceoverScene):
    def construct(self) -> None:
        manifest = load_cue_manifest(os.environ.get("CUE_BINDINGS_MANIFEST", DEFAULT_MANIFEST))
        cues = manifest.resolve(CUE_ORDER)  # every recording is validated before anything renders
        self.camera.background_color = "#101827"
        self.add(
            Text("RL Studio | Expected reward", font_size=34, color="#8BD5CA").to_edge(UP),
            Text("Expected immediate reward", font_size=24, color="#AAB7CE").to_edge(DOWN),
        )
        for index, cue in enumerate(cues):
            panel = build_panel(cue.cue_id)
            self.set_speech_service(cue.service())
            with self.voiceover(text=TRANSCRIPTS[cue.cue_id]):  # lasts exactly as long as the recording
                self.play(FadeIn(panel), run_time=FADE_IN_SECONDS)
            if index < len(cues) - 1:  # the last panel stays up through the final hold
                self.play(FadeOut(panel), run_time=FADE_OUT_SECONDS)
        self.wait(FINAL_HOLD_SECONDS)
