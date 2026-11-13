"""Manual Narration Bookmarks: the expected-reward calculation, revealed at named moments of its recording.

The calculation narration (section 2 of the lesson, examples/rl_lessons/assets/section_2.wav)
is played through LocalWavService with manual bookmarks from
examples/rl_lessons/assets/expected_reward_bookmarks.json. Each bookmark is the start of one
spoken phrase, in seconds from the start of the recording; the times were authored from the
recording's waveform (the pauses between phrases), not by transcription. The scene waits for
each bookmark with the existing VoiceoverScene.wait_until_bookmark and reveals that step.

At each bookmark the scene also adds a small sync marker in the lower-left corner, instantly,
so it is visible in the first frame of the reveal. scripts/verify_local_wav_bookmarks_render.py
measures the frame in which each marker appears and checks that it is within one frame of the
bookmark in the rendered audio. That script declares the same layout independently; a test
keeps the two equal.

The recording and bookmark file are taken from LOCAL_WAV_BOOKMARKS_AUDIO and
LOCAL_WAV_BOOKMARKS_FILE, or default to the tracked files above.
"""

import os
from pathlib import Path

from manim import DOWN, UP, WHITE, FadeIn, Square, Text, VGroup

from manim_voiceover import VoiceoverScene
from manim_voiceover.services.local_wav import LocalWavService

ASSETS = Path(__file__).resolve().parent / "assets"
DEFAULT_RECORDING = ASSETS / "section_2.wav"
DEFAULT_BOOKMARKS = ASSETS / "expected_reward_bookmarks.json"
# Verbatim transcript of section 2 in assets/narration.json.
TRANSCRIPT = "One quarter times zero, plus three quarters times four, gives an expected reward of three."
BOOKMARK_ORDER = ("quarter_times_zero", "three_quarters_times_four", "expected_reward_three")
STEPS = {
    "quarter_times_zero": "0.25 \u00d7 0 = 0",
    "three_quarters_times_four": "0.75 \u00d7 4 = 3",
    "expected_reward_three": "Expected reward = 0 + 3 = 3",
}
# Declared layout. scripts/verify_local_wav_bookmarks_render.py declares the same values independently.
REVEAL_SECONDS = 0.5
FINAL_HOLD_SECONDS = 0.25
MARKER_SIDE = 0.3
MARKER_X = -6.6
MARKER_SPACING = 0.5
MARKER_Y = -3.55


def sync_marker(index: int) -> Square:
    """The solid square shown from bookmark ``index`` on; the verifier finds the frame it first appears in."""
    marker = Square(side_length=MARKER_SIDE, stroke_width=0, fill_color=WHITE, fill_opacity=1)
    return marker.move_to([MARKER_X + index * MARKER_SPACING, MARKER_Y, 0])


class ExpectedRewardBookmarks(VoiceoverScene):
    def construct(self) -> None:
        recording = Path(os.environ.get("LOCAL_WAV_BOOKMARKS_AUDIO", DEFAULT_RECORDING))
        bookmarks = Path(os.environ.get("LOCAL_WAV_BOOKMARKS_FILE", DEFAULT_BOOKMARKS))
        self.set_speech_service(LocalWavService(recording, bookmarks=bookmarks))  # bookmarks are checked here
        self.camera.background_color = "#101827"
        self.add(Text("RL Studio | Expected reward of action B", font_size=30, color="#8BD5CA").to_edge(UP))
        steps = VGroup(*(Text(STEPS[name], font_size=40) for name in BOOKMARK_ORDER)).arrange(DOWN, buff=0.6)
        with self.voiceover(text=TRANSCRIPT):  # lasts exactly as long as the recording
            for index, name in enumerate(BOOKMARK_ORDER):
                self.wait_until_bookmark(name)
                self.add(sync_marker(index))
                self.play(FadeIn(steps[index]), run_time=REVEAL_SECONDS)
        self.wait(FINAL_HOLD_SECONDS)
