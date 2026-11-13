# Cue Bindings: stable narration cues

Cue Bindings replaces the expected-reward lesson's single joined recording and list of durations with three stable cue IDs: `question`, `calculation` and `decision`. A versioned JSON manifest binds each ID to a WAV file. Rebinding one cue changes only that cue's recording; the scene, the transcripts and the other recordings stay the same.

Every cue is played through `LocalWavService` (Local WAV Narration), so its audio validation, source preservation and content-addressed caching apply unchanged (see [the Local WAV Narration guide](local_wav.md)). The original lesson (`examples/rl_lessons/local_smoke.py`), its recordings and the Local WAV Narration example (`examples/local_wav_baseline.py`) are unchanged and remain regression baselines. (Renaming that example changed its pinned hash once, deliberately.)

**Status:** the resolver, text-only lesson, independent timeline and verifier are complete. The A → B → B sequence has been rendered and verified with one shared cache. B against A's expectations fails specifically on the calculation.

**Open:** the alternate calculation take is a **candidate generated for testing, pending owner review and approval** (see "Alternate calculation take").

## Files

| File | Purpose |
|---|---|
| `src/manim_voiceover/services/cue_bindings.py` | Manifest loader and cue resolver |
| `examples/rl_lessons/expected_reward_cues.py` | Text-only lesson scene `ExpectedRewardCues` |
| `examples/rl_lessons/assets/expected_reward_cues.json` | Manifest A: sections 0, 2 and 3 of the lesson narration |
| `fixtures/cue_bindings/calculation_alt.wav`, `.txt`, `info.json` | Candidate alternate calculation take, its exact text and its provenance |
| `scripts/expected_reward_timeline.py` | Independent expected timeline |
| `scripts/verify_cue_bindings_render.py` | Render verifier |
| `scripts/dev.sh` (`cue-bindings`, `demo cue-bindings`) | Render, verify, cross-check and demo commands |
| `scripts/render_workflow.sh`, `scripts/render_verification.py` | Shared render workflow (run records, source hashing) and measurements (frame rules, audio alignment) |
| `tests/test_cue_bindings.py` | Resolver tests (220) |
| `tests/test_expected_reward_lesson.py` | Scene, timeline and candidate-take tests (27) |
| `tests/test_cue_bindings_verifier.py` | Verifier tests (33) |
| `tests/test_render_workflow.py`, `tests/test_render_verification.py` | Shared workflow and measurement tests, including the generic frame-rule and alignment tests moved from the two files above |

## Manifest format (version 1)

```json
{
  "version": 1,
  "cues": [
    {"id": "question", "wav": "section_0.wav"},
    {"id": "calculation", "wav": "section_2.wav"},
    {"id": "decision", "wav": "section_3.wav"}
  ]
}
```

- **Structure:** exactly the keys `version` (the integer `1`) and `cues` (a non-empty list). Each entry has exactly `id` and `wav`. Unknown keys, repeated JSON keys and any other version are rejected. A UTF-8 byte order mark is accepted (Windows PowerShell 5.1 writes one).
- **IDs:** 1-64 lowercase ASCII letters, digits or underscores, starting with a letter. **Each ID may appear only once.**
- **WAV names:** `wav` is the **base name** of a WAV file in the manifest's directory, so a binding names the same file on Linux, macOS and Windows. Rejected: path separators (`/`, `\`), drive letters, control characters, the characters `< > : " | ? *`, Windows device names (`CON`, `NUL`, `COM1`, ...) and names ending in a dot or space. Names must end in `.wav` (any case).
- **Symbolic links** are followed, and allowed only if they resolve inside the manifest directory.
- **Unused and shared bindings:** the manifest may bind cues the scene does not use (their names are checked, but the files are never opened), and several cues may share one recording.
- **No hashes in the manifest.** The SHA-256 of each resolved recording is recorded as verification evidence instead.

## Resolver

```python
from manim_voiceover.services.cue_bindings import load_cue_manifest

manifest = load_cue_manifest("assets/expected_reward_cues.json")  # checks JSON, structure, IDs and names
cues = manifest.resolve(("question", "calculation", "decision"))   # the scene's order
for cue in cues:                                                     # cue.cue_id, cue.recording, cue.sha256, cue.duration
    self.set_speech_service(cue.service())                           # a LocalWavService (Local WAV Narration)
```

- The scene decides the order; the order in the file has no effect. The scene may request a cue more than once, and its recording is validated once.
- Every requested recording is validated with the local WAV checks before `resolve` returns. An invalid selection therefore fails before anything is rendered or cached.
- **Errors:** `CueManifestError` (invalid manifest), `DuplicateCueError`, `UnknownCueError` and `UnsafeRecordingPathError`. The local WAV errors `FileNotFoundError`, `InvalidRecordingError` and `AudioDecoderDependencyError` keep their type and message, and gain a note naming the cue and manifest.

## Lesson scene

`ExpectedRewardCues` plays the cues in the order `question`, `calculation`, `decision`, with the verbatim transcripts of sections 0, 2 and 3 of `assets/narration.json`. Section 1 is not used. The panels are plain `Text` (no LaTeX):

- the question, as in `local_smoke.py`;
- `0.25(0) + 0.75(4) = 3`;
- `3 > 2`.

Each voiceover block lasts exactly as long as its recording. The panel fades in (0.4 s) during the block, and fades out (0.3 s) after it, except the last panel, which stays up through the final hold (0.25 s). The manifest is taken from `CUE_BINDINGS_MANIFEST`, or defaults to the tracked manifest A.

## Alternate calculation take (candidate)

`fixtures/cue_bindings/calculation_alt.wav` was generated offline with the speech engine already present in the environment. No package was installed.

| Property | Value |
|---|---|
| Text | `One quarter times zero, plus three quarters times four, gives an expected reward of three` (`calculation_alt.txt`, 89 bytes, no final period) |
| Engine | FFmpeg 6.1.1 `flite` filter (libflite 2.2), voice `slt` (CMU US English, female, 16 kHz native) |
| Command | `ffmpeg -f lavfi -i "flite=textfile=fixtures/cue_bindings/calculation_alt.txt:voice=slt" -ar 22050 -ac 1 -c:a pcm_s16le -fflags +bitexact -flags:a +bitexact -map_metadata -1 fixtures/cue_bindings/calculation_alt.wav` |
| Format | Mono, 16-bit PCM, 22050 Hz, plain layout (`fmt ` and `data` only); `LocalWavService` accepts it, and pydub reads it without FFmpeg |
| Length | 132300 frames = 6.000 s (section 2: 7.303 s). The difference is 1.303 s = **19.55 frames at 15 fps** (at least 2 required) |
| SHA-256 | `891215f01b289d0bb3272626d31ab3668638ed224187524ce5e76f9dd848e8d9` (section 2: `23a264b3...`) |
| Processing | Sample-rate conversion 16000 → 22050 Hz only. **No padding, trimming or tempo change**: the native 16 kHz output is also exactly 6.000000 s, with the same leading (0.224 s) and trailing (0.176 s) silence. |
| Reproducible | Regenerating with the same command gives a byte-identical file. |

Notes:

- The exact 6.000 s is not a cap: Flite produces lengths in 80-sample (5 ms) steps, and other texts gave other lengths, including longer than 6 s.
- The voice (Flite `slt`) differs from the Microsoft Zira voice of sections 0 and 3.
- The scene's calculation transcript (subtitles and cache key) is unchanged: it is section 2's text.

## Commands

Run from the repository root in Linux/WSL after `bash scripts/dev.sh setup`. Cue Bindings uses the same environment and needs no new dependencies. From Windows, `.\local.ps1 demo cue-bindings` runs the demo in WSL.

```bash
# Tests
bash scripts/dev.sh test                                    # focused suite, including all Cue Bindings tests
UV_NO_DEV=1 uv run --frozen pytest -v tests/test_cue_bindings.py   # one file on its own

# The whole demo: A -> B -> B with one shared cache, each render verified, the three cross-checks
# below, and a summary in media/cue_bindings/verification.json
bash scripts/dev.sh demo cue-bindings

# Or step by step (run numbers continue from the highest existing record)
bash scripts/dev.sh cue-bindings render a                  # e.g. run 01-a
bash scripts/dev.sh cue-bindings render b                  # run 02-b: calculation rebound to the alternate take
bash scripts/dev.sh cue-bindings render b                  # run 03-b: B again

# B must fail specifically on the calculation against A's expectations (and the reverse)
bash scripts/dev.sh cue-bindings check-against 02-b 01-a
bash scripts/dev.sh cue-bindings check-against 01-a 02-b
bash scripts/dev.sh cue-bindings check-against 03-b 01-a

# Re-verify a record later (the default is the highest-numbered run); results are never overwritten
bash scripts/dev.sh cue-bindings verify 01-a

# Independent timelines
.venv/bin/python scripts/expected_reward_timeline.py expect media/cue_bindings/cues/manifest-b.json
.venv/bin/python scripts/expected_reward_timeline.py compare media/cue_bindings/runs/01-a/expected.json media/cue_bindings/runs/02-b/expected.json
```

Each `render` takes about 25-50 s including verification, and each `check-against` or `verify` about 20 s.

## Working folder, shared cache, records and checks

`scripts/dev.sh cue-bindings` never changes the original recordings:

- It copies sections 0, 2 and 3, manifest A and, for B, the alternate take into `media/cue_bindings/cues/`. A file is copied only if its bytes differ, so unchanged copies keep their identity.
- It generates manifest B (`media/cue_bindings/cues/manifest-b.json`) from manifest A, and asserts that only the `calculation` entry changes.
- `media/cue_bindings` and its voiceover cache `media/cue_bindings/voiceovers/` are never cleared, so all renders share one cache.

Each render gets a numbered record, `media/cue_bindings/runs/NN-take/`. The number is one more than the highest existing record number, so deleting an earlier record never makes a later render reuse a number; other folders in `runs/` are ignored. Source files are hashed with NUL-separated names, so names (and the checkout path) may contain spaces, quotes, backslashes or newlines.

| File | Written | Content |
|---|---|---|
| `expected.json` | before rendering | Independent expected timeline, including the tolerance |
| `sources-before.sha256` | before rendering | SHA-256 of every recording and manifest (originals, fixtures and working copies) |
| `cache-before.json` | before rendering | SHA-256, inode, size, mtime and ctime of every cached WAV; number of `cache.json` entries |
| `sources-after.sha256`, `cache-after.json` | after rendering | As before; the render fails if any source changed |
| `verification.json` | first verification | All checks and measurements |

Results are never overwritten. A later `verify` of the same record is saved as `media/cue_bindings/checks/verify-RUN-TIMESTAMP.json`, and `check-against` as `media/cue_bindings/checks/RUN-against-OTHER.json`. The video is `media/cue_bindings/videos/expected_reward_cues/480p15/run-NN-take.mp4`, with Manim's uncompressed soundtrack (`.wav`) and subtitles (`.srt`) next to it.

## Expected timeline

`scripts/expected_reward_timeline.py` computes the timeline only from the WAV headers, the declared layout and Manim 0.20.1's frame rules. It does not import Manim, Manim Voiceover, the resolver or the scene. The rules (from Manim's source, confirmed by renders):

- a run time or wait shorter than one frame is raised to one frame, and one of zero or less is rejected (`Scene.validate_run_time`);
- an animation of run time `r` writes `len(numpy.arange(0, r, 1/fps))` frames;
- a static wait of `d` seconds writes `int(d / (1/fps))` frames;
- the renderer clock advances `1/fps` per frame (floating point);
- a voiceover block waits for the rest of its recording only if more than one frame remains;
- a cue's audio is placed at `int(1000 * clock)` milliseconds.

A block therefore ends at most one frame before its recording. The fade-out (5 frames) keeps the next cue clear of it, and the final hold (3 frames) lets the last recording finish. The **timing tolerance is one frame (1/15 s at `-ql`)**, declared in the timeline before any render.

A and B timelines, each computed independently before rendering:

| Cue | A start frame | A audio | B start frame | B audio | Shift |
|---|---:|---|---:|---|---:|
| question | 0 | 0.000 - 12.058 s | 0 | 0.000 - 12.058 s | 0 |
| calculation | 185 | 12.333 - 19.636 s | 185 | 12.333 - 18.333 s | 0 |
| decision | 299 | 19.933 - 34.310 s | 280 | 18.666 - 33.043 s | **-19 frames** |
| video | 517 frames (34.467 s) | | 498 frames (33.200 s) | | -19 |

In A, the calculation's wait is `int(6.9034 s × 15) = 103` frames. In B, the take leaves exactly 5.6 s = 84 frames after the fade-in, which is a frame boundary. The model follows Manim's floating-point clock, and the render confirmed 84. The 19-frame shift is far outside the one-frame tolerance, so the timing alone also distinguishes the two calculation recordings.

## Verifier

`scripts/verify_cue_bindings_render.py` measures a render and compares it with the expectations recorded before rendering. For three cues it runs 29 checks.

- **Each cue (5 checks):**
  - the recording is present sample for sample in Manim's uncompressed soundtrack;
  - the soundtrack onset is within the tolerance;
  - the MP4 onset is within the tolerance after the measured codec offset;
  - the whole cue, and its final voiced second, correlate with the recording in the MP4 above 0.97 (as for Local WAV Narration).
- **Endings (4 checks):**
  - each cue finishes before the next starts; this is reported as not measurable, and failed, if either cue is missing;
  - the last recording finishes before the video ends, in the soundtrack and in the MP4 including the codec offset.
- **Codec offset (1 check):** measured by aligning the soundtrack with the decoded MP4 audio, never hard-coded. It is accepted only if it equals one audio codec frame as reported by the MP4 (frame size / sample rate), within one sample of the measurement.
- **Video length (1 check):** the decoded frame count equals the expected frame count.
- **Cached WAVs (5 checks):**
  - every cue's recording has a cached copy with its bytes;
  - the new copies are exactly the recordings that were not cached before the render;
  - copies cached before the render were not rewritten (same inode, size, mtime and ctime);
  - there is one `cache.json` entry per cue;
  - the recorded copies are still unchanged now.
- **Record (3 checks):**
  - the expectations were written before the video;
  - the sources were unchanged during the render;
  - the recordings are the ones the timeline was computed from.

`--expected OTHER --expect-mismatch CUE` checks a render against other expectations. It succeeds only if the failure is **specific** to `CUE`:

- `CUE` is not found sample for sample and does not correlate;
- every check of the cues before `CUE`, the codec check and the record checks still pass;
- only checks that `CUE`'s recording can affect fail (`CUE`, later cues, video length, cache).

### Codec offset

The MP4 audio is AAC at 48 kHz. FFmpeg's AAC encoder adds one codec frame (1024 samples, 21.333 ms) of priming at the start, and the MP4 does not signal it (edit list media time 0, `initial_padding` 0). Players therefore start the audio 21.3 ms late relative to the picture. This is inherited from Manim/FFmpeg and also applies to Local WAV Narration renders. It was measured at 470 samples at 22050 Hz (21.315 ms) in every render.

## Results: A → B → B

All three renders passed all 29 checks against their own pre-render expectations.

| Run | Calculation | Video | Decision onset (expected / measured) | Last recording ends before video end | New cached copies | Rewritten |
|---|---|---:|---|---:|---:|---:|
| 01-a | `section_2.wav` | 517 frames | 19.933 / 19.93297 s | 156.8 ms | 3 | 0 |
| 02-b | `calculation_alt.wav` | 498 frames | 18.666 / 18.66599 s | 157.1 ms | 1 | 0 |
| 03-b | `calculation_alt.wav` | 498 frames | 18.666 / 18.66599 s | 157.1 ms | 0 | 0 |

**All three recordings:**

- In every run, each cue is present sample for sample in the soundtrack, within 0.03 ms of its expected onset, both in the soundtrack and in the MP4 after the codec offset.
- Correlation with the source (whole cue / final voiced second):
  - question: 0.9939 / 0.9947;
  - calculation: A 0.9952 / 0.9979, B 0.9982 / 0.9992;
  - decision: 0.9941-0.9942 / 0.9936-0.9937.
- The gaps between cues are at least 275 ms.

**Timing:** the measured decision shift from A to B is -19.005 frames; the independently expected shift is -19.

**Specific mismatch:** `02-b` against A's expectations, the reverse, and `03-b` against A's expectations each fail specifically on the calculation:

- The failures are the calculation's presence, onset and correlation checks, the decision onset (shifted 19 frames), the two ordering checks involving the calculation, the video length and, where applicable, the cache expectations.
- All question checks, the codec check and the record checks pass. There are no unexpected failures.

**One shared cache:**

- A's three cached copies kept the same SHA-256, inode, size, mtime and ctime from creation through B, B again and a final snapshot.
- B added one copy (the alternate take), unchanged through B again.
- `cache.json` entries: 0 → 3 → 6 → 9.
- Re-verifying A and B after B again passed (`media/cue_bindings/checks/`).

**Sources:**

- Every render's source hashes (16 files for A, 20 for B) were identical before and after the render.
- All WAV and JSON files in the repository, including the candidate take, were unchanged by B and B again.
- Subtitle text is identical in all three renders; only B's timings differ from A's.

**Preservation of A's files:** A's record, cached WAVs, working copies and final video, soundtrack and subtitles were not changed by B. Three kinds of files in `media/cue_bindings` were rewritten by later renders; none is a record, cached WAV or source:

- **Manim's text cache** (`media/cue_bindings/texts/*.svg`): rewritten with identical content.
- **`cache.json`:** grew from 3 to 9 entries. LocalWavService rewrites the whole file to append, so its identity changed, but its first three entries are byte-identical to A's file.
- **Manim's intermediate clips** (`partial_movie_files/ExpectedRewardCues/`): overwritten by each render, because Manim uses one folder per scene. A's intermediate clips are gone; its final MP4 is intact. No check uses them.

## Not verified

- **Approval and listening:** the candidate take is pending owner approval, and it has not been reviewed by listening. All checks are numerical or byte comparisons, plus one visual frame check per render.
- **Voice match:** the candidate's voice (Flite `slt`) differs from the Zira voice of the other sections.
- **Panel fades:** checked only through the total frame count, not frame by frame.
- **Platforms:** no native Windows or macOS run. The name rules are tested on Linux as pure string checks for both POSIX and Windows rules.
- **Full development gates not run in the original Taiga session:** mypy, ty, mutmut, pip-audit and Slophammer are not installed in this environment, and were not installed. Ruff 0.15.17 (the locked version) was run and passes. Mutation-style checks were done by hand on the resolver (21 injected bugs) and the verifier (10), and all were caught.
- **Timeline model scope:** specific to Manim 0.20.1's Cairo renderer at `-ql` (15 fps) and this layout.

## Limitations

- **Cache expectations** are based on recording hashes. This assumes each recording is used with one fixed transcript, which holds for this lesson; the LocalWavService cache key also includes the transcript.
- **Audio channels:** the verifier compares the first channel. All lesson recordings are mono.
- **Intermediate clips:** Manim's intermediate clips are shared by all renders of the scene (see above). Separating them per run would need a per-run `partial_movie_dir`.
- **Inherited from Local WAV Narration:** `cache.json` grows by one entry per voiceover and is rewritten on each append, and cached copies are never removed. `local_smoke.py` still needs `dvisvgm` to render its LaTeX, which is unchanged.

## Cloud integration validation

The Taiga change set was integrated on a branch from `dev` commit
`d66b184f4d9ff249266d7e70c10acfa14b0c2716`. The existing source, tests,
dependency declarations and lockfile were preserved. Integration adjustments were
needed in the new files:

- Parse manifest data through the existing typed JSON helper, and retain the
  validated WAV name, so both ty and strict mypy accept the resolver.
- Anchor mutmut's source paths during the test that deliberately changes its
  working directory. This preserves the test assertions and allows mutation
  instrumentation to collect their coverage.
- Use stable ASCII parameter IDs for Unicode/path test cases so repeated
  in-process pytest runs can select them without double-escaping their names.
- Strengthen filename cases for Windows device-name boundaries and suffix
  diagnostics; name the fixed manifest encoding explicitly as a protocol constant.

Fresh cloud validation passed 527 offline tests with 98.01% package coverage;
one network-marked test was excluded from that run. A separate attempt at that
Google TTS test failed because the proxy denied `translate.google.com` with HTTP
403. The complete online suite and Slophammer execute gate therefore did not
pass in this environment. No assertions or network tests were disabled.

Formatting, Ruff lint, ty, strict mypy, Slophammer's static/duplication checks,
and pip-audit passed. Fresh Cue Bindings A/B/B renders each passed all 29 verifier checks,
and the B-against-A check failed specifically on the calculation as expected.
Both Local WAV Narration renders also passed their verification. Generated renders, caches,
logs and the separate Taiga evidence bundle remain outside the Git change set.

The candidate audio remains pending owner listening approval. Native Windows
and macOS validation remains outstanding.

Mutation validation evaluated 3,599 variants on the final code: 3,591 were killed,
none survived, and eight variants in the unchanged Stitcher service timed out
around a cloud runtime slowdown. Those Stitcher variants had been killed in the
earlier full run. The separate final resolver-only run killed all 131 Cue Bindings
variants. The full mutation checker therefore remains non-green due to the
eight timeouts; it is not reported as passed.
