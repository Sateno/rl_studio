"""Render verification: the shared module scripts/render_verification.py.

Manim's frame rules and the audio measurements used by every feature's verifier. The frame
rule and measurement tests were moved here unchanged from the cue-bindings lesson and
verifier tests when the code they test became shared.
"""

import numpy as np
import pytest

from tests.test_expected_reward_lesson import ROOT, load_module

pytestmark = pytest.mark.skipif(
    not (ROOT / "scripts" / "render_verification.py").is_file(),
    reason="scripts are not copied into mutmut's generated worktree",
)


@pytest.fixture(scope="module")
def render_verification():
    import render_verification  # the scripts folder is on the import path (tests/conftest.py)

    return render_verification


# ---------------------------------------------------------------------------
# Manim's frame rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seconds", [0.001, 0.05, 1 / 15])
def test_run_times_shorter_than_a_frame_become_one_frame(render_verification, seconds):
    # Manim's Scene.validate_run_time raises them to one frame (measured: a 0.05 s hold wrote one frame).
    assert render_verification.animation_frames(seconds) == 1
    assert render_verification.static_wait_frames(seconds) == 1


@pytest.mark.parametrize("seconds", [0, -0.1])
def test_run_times_of_zero_or_less_are_rejected_like_manim(render_verification, seconds):
    with pytest.raises(ValueError, match="cannot render"):
        render_verification.static_wait_frames(seconds)


def test_frame_clock_counts_frames_and_time_like_the_renderer(render_verification):
    clock = render_verification.FrameClock(15)
    clock.animate(0.4)  # 6 frames
    clock.wait_static(0.25)  # 3 frames

    assert clock.frames == 9
    assert clock.time == pytest.approx(9 / 15)


@pytest.mark.parametrize(
    "script", ["expected_reward_timeline.py", "verify_cue_bindings_render.py", "verify_local_wav_render.py"]
)
def test_feature_scripts_use_the_shared_rules_and_measurements(render_verification, script):
    module = load_module(f"shared_user_{script[:-3]}", ROOT / "scripts" / script)
    shared = {"animation_frames", "static_wait_frames", "validated_run_time", "locate", "correlation", "measure_offset"}

    used = {name for name in shared if hasattr(module, name)}
    assert used, f"{script} uses none of the shared helpers"
    assert all(getattr(module, name) is getattr(render_verification, name) for name in used)


# ---------------------------------------------------------------------------
# Audio measurements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shift", [0, 1, 470, 1234, -300])
def test_offset_is_measured_from_the_signals(render_verification, shift):
    reference = np.random.default_rng(5).integers(-5000, 5000, 20000).astype(np.int16)
    delayed = np.concatenate([np.zeros(shift, np.int16), reference]) if shift >= 0 else reference[-shift:]
    assert render_verification.measure_offset(reference, delayed) == shift


def test_locate_finds_a_reference_inside_a_track(render_verification):
    reference = np.random.default_rng(2).integers(-5000, 5000, 3000).astype(np.int16)
    track = np.concatenate([np.zeros(12345, np.int16), reference, np.zeros(777, np.int16)])
    assert render_verification.locate(reference, track) == 12345
    assert render_verification.locate(track, reference) == 0  # a reference longer than the track is not inside it


def test_final_voiced_second_ends_at_the_last_voiced_sample(render_verification):
    source = np.zeros(50000, np.int16)
    source[30000] = 5000
    source[40000] = 129  # just above the voiced level
    source[45000] = 128  # not voiced

    assert render_verification.final_voiced_second(source, 22050) == (40000 - 22050, 40001)
