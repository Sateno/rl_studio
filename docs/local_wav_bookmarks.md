# Manual Narration Bookmarks

Named times in a local recording, in seconds from its start. A scene waits for a bookmark and
then starts an animation, so the animation follows the narration without transcription.
Recordings without bookmarks work exactly as before.

- Python/file names: `local_wav_bookmarks` (`manim_voiceover.services.local_wav_bookmarks`,
  `tests/test_local_wav_bookmarks.py`, `scripts/verify_local_wav_bookmarks_render.py`)
- Command target: `bookmarks` (`bash scripts/dev.sh demo bookmarks`, `.\local.ps1 demo bookmarks`)
- Output: `media/bookmarks/` (video and `verification.json`)

## Use

```python
from manim_voiceover import VoiceoverScene
from manim_voiceover.services.local_wav import LocalWavService


class Lesson(VoiceoverScene):
    def construct(self):
        self.set_speech_service(LocalWavService("calculation.wav", bookmarks="calculation.bookmarks.json"))
        with self.voiceover(text="One quarter times zero, plus three quarters times four, ..."):
            self.wait_until_bookmark("three_quarters_times_four")
            self.play(FadeIn(step))
```

`bookmarks` is optional and may be:

- a mapping: `{"result": 4.57}`;
- a sequence of `(name, seconds)` pairs: `[("question", 0.12), ("result", 4.57)]`;
- the path of a version 1 bookmark file:

```json
{
  "version": 1,
  "bookmarks": [
    {"name": "quarter_times_zero", "seconds": 0.12},
    {"name": "three_quarters_times_four", "seconds": 2.17},
    {"name": "expected_reward_three", "seconds": 4.57}
  ]
}
```

The scene API is the existing one: `self.wait_until_bookmark(name)` and
`tracker.time_until_bookmark(name, buff=..., limit=...)`. Cue Bindings pass bookmarks on:
`cue.service(bookmarks=...)`.

## Rules and errors

All errors are subclasses of `BookmarkError` (a `ValueError`), importable from
`manim_voiceover.services.local_wav_bookmarks`.

| Rule | Error |
|---|---|
| A name is 1-64 lowercase ASCII letters, digits or underscores, starting with a letter (the cue ID rule) | `InvalidBookmarkNameError` |
| Each name appears once (in pairs or a file; a repeated JSON key is also rejected) | `DuplicateBookmarkError` (`BookmarkFileError` for a repeated JSON key) |
| A time is a finite number of seconds: not a boolean, string, null, NaN or infinity | `InvalidBookmarkTimeError` |
| A time is from 0 to the recording's duration, inclusive | `BookmarkOutOfRangeError` |
| At least one bookmark (omit `bookmarks` for a recording without them) | `BookmarkError` |
| A file is UTF-8 JSON (a BOM is allowed) with exactly `version` (1) and `bookmarks`, and entries with exactly `name` and `seconds` | `BookmarkFileError`; a missing file is `FileNotFoundError` |
| A scene requests a bookmark the recording does not have; the message lists the available names | `UnknownBookmarkError` |
| Manual bookmarks are not combined with `transcription_model` or with `<bookmark mark=.../>` tags in the text | `BookmarkError` |

Bookmarks are checked when the service is created, so a wrong bookmark fails before anything
is rendered, and again on every render, because the recording can be replaced (a bookmark after
the end of a shorter replacement is rejected). An unknown bookmark can only be detected when the
scene asks for it.

## Timing

`wait_until_bookmark` waits until the voiceover's start plus the bookmark's seconds. Manim writes
whole frames and rounds a wait down; for manual bookmarks half a frame is added first, so the
next animation starts on the **nearest** frame: within half a frame of the bookmark in the
rendered soundtrack. Text bookmarks of transcribed voiceovers keep their previous behaviour.

A bookmark whose time has already passed (for example because the previous animation was longer
than the gap) does not wait and does not raise an error: the existing behaviour. The animation
then starts late. Keep consecutive bookmarks at least one animation (plus a frame) apart.

In the MP4, the AAC encoder delays the audio by one codec frame (21.3 ms, about a third of a frame
at 15 fps; see [Cue Bindings](cue_bindings.md#codec-offset)). With nearest-frame rounding, every
animation is also within one frame of its bookmark as played from the MP4.

## Caching and sources

Bookmarks are not part of the cache key, and the cache entry's input data is unchanged: a render
with different bookmarks, or without bookmarks, reuses the recording's cached copy without writing
it again. The recording and the bookmark file are only read.

## Demo and verification

```bash
bash scripts/dev.sh demo bookmarks          # or: .\local.ps1 demo bookmarks
bash scripts/dev.sh bookmarks verify        # verify the last render again
```

The demo renders `examples/rl_lessons/expected_reward_bookmarks.py` (`ExpectedRewardBookmarks`):
section 2 of the lesson narration (`section_2.wav`, "One quarter times zero, plus three quarters
times four, gives an expected reward of three.") with the bookmarks in
`examples/rl_lessons/assets/expected_reward_bookmarks.json`. The three times are the starts of
the three spoken phrases, authored from the recording's waveform (the pauses at the commas,
measured at 0.119, 2.172 and 4.573 s); they were not checked by listening. At each bookmark the
scene reveals one step of the calculation and adds a small sync marker in the lower-left corner.

Before rendering, `scripts/verify_local_wav_bookmarks_render.py expect` computes each bookmark's
expected frame from the WAV header, the bookmark file and Manim's frame rules, without importing
Manim or the scene. After rendering, `verify` writes `media/bookmarks/verification.json`. It
measures where the recording starts in Manim's soundtrack and the first frame in which each sync
marker appears, and checks, for every bookmark:

- the animation starts within one frame of the bookmark in the soundtrack, and in the MP4 after
  the measured codec offset;
- it starts at the expected frame (tolerance: one frame), the bookmarks trigger in order, and
  the check is sensitive (a bookmark two frames earlier or later would fail);

and also that the recording is present sample for sample and correlates in the MP4, the video
covers it and has the expected frame count, the sources are unchanged, and the cached copy is
reused.

Outputs:

```text
media/bookmarks/videos/expected_reward_bookmarks/480p15/bookmarks.mp4
media/bookmarks/verification.json
media/bookmarks/expected.json, sources-before.sha256, sources-after.sha256, cache-before.json, cache-after.json
```

Each demo replaces these files (the cache in `media/bookmarks/voiceovers` is kept).

## Tests

`tests/test_local_wav_bookmarks.py`; every test runs on its own, for example:

```bash
UV_NO_DEV=1 uv run --frozen pytest -v tests/test_local_wav_bookmarks.py
UV_NO_DEV=1 uv run --frozen pytest -v "tests/test_local_wav_bookmarks.py::test_invalid_bookmark_time_is_rejected[nan]"
UV_NO_DEV=1 uv run --frozen pytest -v -m integration tests/test_local_wav_bookmarks.py   # the real renders
```

The tests marked `integration` render the lesson with a synthetic recording and authored
bookmarks and verify it, check that the verifier fails the same render against bookmarks two
frames later, and check that bookmarks do not change the rendered audio or the cached copy.

## Limitations

- Bookmark times are authored by hand; nothing checks that the words are spoken at those times.
- Bookmarks belong to one recording. Replacing the recording keeps the bookmarks, which may then
  point at different words (only times beyond the new end are rejected).
- Timing is checked at `-ql` (15 fps) with the Cairo renderer and Manim's animation cache disabled,
  as the demos render.
